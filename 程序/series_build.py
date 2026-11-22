"""图①④⑤：用 Tushare 交易日历、指数收盘价、三只 ETF 份额和两市融资余额建库。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep

import requests


DEST = Path(__file__).resolve().parents[1] / "数据" / "series.js"
PREFIX = "window.SERIES_DATA="
METHOD = "tushare-market-series-v1"
INDICES = {
    "000300.SH": "沪深300", "000016.SH": "上证50", "000905.SH": "中证500",
    "000852.SH": "中证1000", "932000.CSI": "中证2000", "000510.SH": "中证A500",
    "399006.SZ": "创业板指", "000688.SH": "科创50", "931643.CSI": "科创创业50",
    "399330.SZ": "深证100", "000001.SH": "上证综指",
}
FUNDS = ("510300.SH", "510310.SH", "510330.SH")
EXCHANGES = {"marginSH": "SSE", "marginSZ": "SZSE"}


def completed_date():
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.date() if now.hour >= 15 else now.date() - timedelta(days=1)


@contextmanager
def bundle_lock(path=None):
    path = path or DEST.parent / ".series.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0, os.SEEK_END)
            if not lock.tell():
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def api_rows(token, name, params, fields):
    body = dict(api_name=name, token=token, params=params, fields=",".join(fields))
    for attempt in range(3):
        try:
            response = requests.post("https://api.tushare.pro", json=body, allow_redirects=False, timeout=(8, 45))
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 0:
                raise RuntimeError(f"Tushare {name} 返回错误码 {result.get('code')}，请检查对应接口权限")
            table = result["data"]
            if table.get("has_more") or (table["items"] and table["fields"] != fields):
                raise ValueError(f"Tushare {name} 返回结果截断或字段不匹配")
            return table["items"]
        except (requests.RequestException, RuntimeError):
            if attempt == 2:
                raise
            sleep(attempt + 1)


def windows(start, end):
    while start <= end:
        # 五个历年的交易日数低于 fund_share 的单次 2,000 行限制。
        stop = min(end, date(start.year + 4, 12, 31))
        yield start.strftime("%Y%m%d"), stop.strftime("%Y%m%d")
        start = stop + timedelta(days=1)


def iso(compact):
    return datetime.strptime(compact, "%Y%m%d").date().isoformat()


def read_field(token, spec, start, end):
    key, endpoint, parameter, code, field = spec
    values = {}
    for first, last in windows(start, end):
        rows = api_rows(token, endpoint, {parameter: code, "start_date": first, "end_date": last},
                        [parameter, "trade_date", field])
        for actual_code, stamp, raw in rows:
            if actual_code != code or not first <= stamp <= last:
                raise ValueError(f"{endpoint} 返回了不在请求范围内的标的或日期")
            if raw is not None:
                value = float(raw)
                if value > 0 and math.isfinite(value):
                    day = iso(stamp)
                    if day in values and values[day] != value:
                        raise ValueError(f"{key} {day} 有冲突的重复记录")
                    values[day] = value
    return key, values


def load_existing():
    if not DEST.exists():
        return None
    source = DEST.read_text(encoding="utf-8-sig").strip()
    if not source.startswith(PREFIX) or not source.endswith(";"):
        raise ValueError("无法识别已有图①④⑤文件")
    obj = json.loads(source[len(PREFIX):-1])
    if obj.get("method") != METHOD or obj.get("demo"):
        raise ValueError("已有文件不属于此公开数据口径，已保留原文件")
    return obj


def fetch_range(token, start, end):
    calendar = set()
    for first, last in windows(start, end):
        rows = api_rows(token, "trade_cal", dict(exchange="SSE", is_open="1",
                        start_date=first, end_date=last), ["cal_date"])
        for (stamp,) in rows:
            if not first <= stamp <= last or iso(stamp) in calendar:
                raise ValueError("交易日历日期超界或重复")
            calendar.add(iso(stamp))
    specs = [(code, "index_daily", "ts_code", code, "close") for code in INDICES]
    specs += [(code, "fund_share", "ts_code", code, "fd_share") for code in FUNDS]
    specs += [(key, "margin", "exchange_id", exchange, "rzye") for key, exchange in EXCHANGES.items()]
    with ThreadPoolExecutor(max_workers=3) as workers:
        market = dict(workers.map(lambda spec: read_field(token, spec, start, end), specs))
    last_price = max(market["000001.SH"], default=None)
    if not last_price:
        if not calendar:
            return [], {}
        raise ValueError("所选交易日没有取得上证综指收盘价，已有数据保留")
    if set(market["000001.SH"]) - calendar:
        raise ValueError("上证综指与上交所交易日历冲突")
    days = sorted(day for day in calendar if day <= last_price)
    common = set.intersection(*(set(market[c]) for c in FUNDS))
    # fund_share.fd_share 以万份计，三只需同日齐备。margin.rzye 以元计。
    market["shares"] = {day: round(sum(market[c][day] for c in FUNDS) / 1e4, 8) for day in common}
    for key in EXCHANGES:
        market[key] = {d: round(v / 1e8, 8) for d, v in market[key].items()}
    return days, market


def refresh(start, end, previous):
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("请在看板中输入自己的 Tushare Token")
    old_days = previous["dates"] if previous else []
    if old_days and start > date.fromisoformat(old_days[-1]):
        start = date.fromisoformat(old_days[-1])
    if old_days and end < date.fromisoformat(old_days[0]):
        end = date.fromisoformat(old_days[0])
    fresh_days, market = fetch_range(token, start, end)
    if not fresh_days:
        if previous:
            return previous
        raise ValueError("所选区间无已出数交易日")
    days = sorted(set(old_days).union(fresh_days))
    old = {row["code"]: row["values"] for row in previous["indices"]} if previous else {}
    if previous:
        old.update({name: previous[name] for name in ("shares", *EXCHANGES)})

    def merged(name):
        values = dict(zip(old_days, old.get(name, [])))
        values.update(market[name])
        return [values.get(d) for d in days]

    indices = [dict(code=code, name=name, values=merged(code)) for code, name in INDICES.items()]
    return dict(method=METHOD, dates=days, indices=indices, hs300=indices[0]["values"],
                shares=merged("shares"), marginSH=merged("marginSH"), marginSZ=merged("marginSZ"),
                sources=dict(indices="Tushare index_daily 指数收盘点位，按各指数最早可用日期展示（含发布前回溯历史）",
                             shares="Tushare fund_share，三只沪市沪深300 ETF 相同交易日份额合计（亿份）及沪深300收盘点位",
                             margin="Tushare margin.rzye，沪、深交易所融资余额分别从元换算为亿元"))


def publish(obj):
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=DEST.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(PREFIX)
            json.dump(obj, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.write(";\n")
        except BaseException:
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, DEST)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", type=date.fromisoformat, help="首次建库或补抓历史的开始日")
    mode.add_argument("--daily", action="store_true", help="逐项回查最近观测并续接")
    parser.add_argument("--end", type=date.fromisoformat, default=completed_date())
    args = parser.parse_args()
    if args.end > completed_date() or (args.start and args.start > args.end):
        parser.error("须选择已收盘的有效日期区间")
    with bundle_lock():
        previous = load_existing()
        if args.daily and not previous:
            parser.error("首次生成请使用 --start YYYY-MM-DD")
        start = args.start
        if args.daily:
            days = previous["dates"]
            anchors = [days[-min(10, len(days))]]
            for values in [previous["shares"], previous["marginSH"], previous["marginSZ"],
                           *(row["values"] for row in previous["indices"])]:
                anchors.append(next((day for day, v in zip(reversed(days), reversed(values)) if v is not None), anchors[0]))
            start = date.fromisoformat(min(anchors))
            if start > args.end:
                return
        obj = refresh(start, args.end, previous)
        if obj != previous:
            publish(obj)
        print(f"图①④⑤已覆盖 {len(obj['dates'])} 个交易日，{obj['dates'][0]} 至 {obj['dates'][-1]}")


if __name__ == "__main__":
    main()
