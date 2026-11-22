"""图⑥。中证公布的沪深300股息点、价格指数与财政部中国国债收益率曲线。"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep

import requests

from series_build import api_rows, bundle_lock, completed_date


DEST = Path(__file__).resolve().parents[1] / "数据" / "yields.js"
PREFIX = "window.PUBLIC_YIELDS="
METHOD = "tushare-csi-rolling-dividend-points-mof-10y-v1"
POINTS_BASE = date(2004, 12, 31)
BOND_BASE = date(2006, 3, 1)
DAY = timedelta(days=1)
MOF = "https://yield.chinabond.com.cn/cbweb-czb-web/czb/historyQuery"
SOURCES = {
    "dividendPoints": "https://www.csindex.com.cn/#/indices/family/detail?indexCode=H30401",
    "methodology": "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/H30401_Index_Methodology_cn.pdf",
    "factsheet": "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/H30401factsheet.pdf",
    "price": "https://www.csindex.com.cn/#/indices/family/detail?indexCode=000300",
    "bond": "https://yield.chinabond.com.cn/cbweb-czb-web/czb/showHistory?locale=cn_ZH&nameType=1",
    "bondMethodology": "https://yield.chinabond.com.cn/cbweb-czb-web/czb/bzcxsmDown?locale=cn_ZH",
    "indexQuotesApi": "https://tushare.pro/document/2?doc_id=95", "bondCurveApi": MOF,
}


def last_year(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 2月29日向前一年，对齐2月28日。
        return date(day.year - 1, 2, 28)


def request_json(url, params):
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=(8, 45))
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            sleep(attempt + 1)


def index_rows(token, code, start, end):
    symbol = {"000300": "000300.SH", "H30401": "H30401.CSI"}[code]
    items = api_rows(token, "index_daily",
                     {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"),
                      "end_date": end.strftime("%Y%m%d")}, ["ts_code", "trade_date", "close"])
    rows = {}
    for ticker, stamp, close in items:
        day = date.fromisoformat(stamp)
        value = float(close)
        if (ticker != symbol or not start <= day <= end or day.weekday() >= 5 or day in rows
                or not math.isfinite(value) or value < 0 or (code == "000300" and value == 0)):
            raise ValueError(f"中证 {code} 原始行情异常，日期 {day}")
        rows[day] = value
    return rows


def bond_rows(start, end):
    result = request_json(MOF, {"startDate": start.isoformat(), "endDate": end.isoformat(),
                               "gjqx": "10", "locale": "cn_ZH", "qxmc": "1"})
    if str(result.get("flag")) == "1":
        return {}
    if str(result.get("flag")) != "0" or not isinstance(result.get("heList"), list):
        raise ValueError("财政部国债收益率曲线接口未返回查询结果")
    rows = {}
    for item in result["heList"]:
        # 日期是财政部查询表中的曲线日期，未使用网页访问日或填充前值。
        day = date.fromisoformat(item["workTime"])
        if not start <= day <= end or day in rows:
            raise ValueError(f"财政部曲线日期异常 {day}")
        if item["tenYear"] not in (None, ""):
            rate = float(item["tenYear"])
            if not math.isfinite(rate):
                raise ValueError(f"财政部10年期曲线数值异常 {day}")
            rows[day] = rate
    return rows


def fetch(token, job):
    field, start, end = job
    rows = bond_rows(start, end) if field == "bond" else index_rows(token, field, start, end)
    if not rows and (end - start).days > 25:
        raise RuntimeError(f"{field} 在 {start} 至 {end} 返回空数据，保留现有发布包")
    return field, rows


def jobs(fields, start, end):
    for field in fields:
        left = max(start, BOND_BASE if field == "bond" else POINTS_BASE)
        while left <= end:
            # 财政部查询跨度小于365天；中证行情可一次返回多年历史。
            right = min(end, left + timedelta(days=360 if field == "bond" else 1094))
            yield field, left, right
            left = right + DAY


def second_friday(year):
    first = date(year, 12, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 7)


def index_units(value):
    units = Decimal(str(value)) * 1000000
    if units != units.to_integral_value():
        raise ValueError("中证行情精度发生变化，须核实后再更新")
    return int(units)


def rolling_yields(prices, points):
    # 按股息点的累计增量相减，可避免逐日舍入误差。未满一个自然年的窗口不输出。
    days = sorted(prices.keys() | points.keys())
    point_totals, missing_totals = [], []
    total = missing = 0
    previous = previous_day = None
    for day in days:
        current = index_units(points[day]) if day in points else None
        if current is None or previous is None:
            missing += 1
        elif previous_day <= second_friday(day.year) < day:
            # 每年12月第二个星期最后一个交易日收盘后归零，下一笔读数全是新增。
            total += current
        else:
            if current < previous:
                raise ValueError(f"股息点在编制规则规定的重置期外下降，日期 {day}")
            total += current - previous
        point_totals.append(total)
        missing_totals.append(missing)
        previous, previous_day = current, day
    values = {}
    for i, day in enumerate(days):
        j = bisect_right(days, last_year(day)) - 1
        if day in prices and day in points and j >= 0 and missing_totals[i] == missing_totals[j]:
            trailing = point_totals[i] - point_totals[j]
            values[day] = round(100 * trailing / index_units(prices[day]), 8)
    return values


def read_cache():
    if not DEST.exists():
        return {}, None
    text = DEST.read_text(encoding="utf-8").strip()
    if not text.startswith(PREFIX) or not text.endswith(";"):
        raise ValueError("图⑥发布文件格式异常")
    obj = json.loads(text[len(PREFIX):-1])
    if obj.get("methodVersion") != METHOD:
        raise ValueError("图⑥发布文件来源或方法版本不匹配")
    raw = {date.fromisoformat(row[0]): row[1:] for row in obj["raw"]}
    return raw, date.fromisoformat(obj["displayStart"])


def write_bundle(raw, display_start):
    days = sorted(raw)
    prices = {d: raw[d][0] for d in days if raw[d][0] is not None}
    points = {d: raw[d][1] for d in days if raw[d][1] is not None}
    bonds = {d: raw[d][2] for d in days if raw[d][2] is not None}
    dividends = rolling_yields(prices, points)
    chart = [d for d in days if d >= display_start and (d in dividends or d in bonds)]
    if not chart:
        raise ValueError("所选期间未取得可发布的收益率")
    obj = {
        "methodVersion": METHOD, "displayStart": display_start.isoformat(),
        "verifiedThrough": {k: max(v).isoformat() if v else None
                            for k, v in (("dividend", dividends), ("bond", bonds))},
        "source": "股息率使用Tushare Pro 的 index_daily 接口提供的两条中证行情计算，即沪深300股息点（H30401.CSI）和沪深300收盘指数（000300.SH）。国债取财政部中国国债收益率曲线10年期当日值。利差只在两项均有观测的同日相减。",
        "dividendLabel": "沪深300近12个月现金股息率（股息点推算）",
        "bondLabel": "财政部10年期国债收益率",
        "dividendMethod": "过去12个自然月计入的税前现金股息点之和÷当日沪深300收盘点位×100。区间左开右闭，年末重置按中证编制方案处理。2014-09-25正式发布前的股息点来自中证回溯行情。",
        "bondMethod": "财政部曲线以工作日最新市场价格编制，约17:30发布。使用官网历史表中的曲线日期及十年期收益率（%）。",
        "sourceUrls": SOURCES, "dates": [d.isoformat() for d in chart],
        "dividend": [dividends.get(d) for d in chart], "bond": [bonds.get(d) for d in chart],
        "rawFields": ["日期", "000300收盘点", "H30401累计股息点", "财政部10年期国债收益率(%)"],
        "raw": [[d.isoformat(), *raw[d]] for d in days],
    }
    with NamedTemporaryFile(mode="w", dir=DEST.parent, delete=False, encoding="utf-8") as file:
        tmp = Path(file.name)
        try:
            file.write(PREFIX)
            json.dump(obj, file, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            file.write(";\n")
        except BaseException:
            file.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, DEST)
    finally:
        tmp.unlink(missing_ok=True)
    both = sum(d in bonds and d in dividends for d in chart)
    print(f"图⑥ {chart[0]} 至 {chart[-1]}，{len(chart)} 个观测日期，{both} 个同日利差。")


def fetch_source(kind, token, left, right):
    if kind == "dividend" and not token:
        raise RuntimeError("请在看板中输入自己的 Tushare Token 以更新股息率")
    fields = ("000300", "H30401") if kind == "dividend" else ("bond",)
    result = {field: {} for field in fields}
    with ThreadPoolExecutor(max_workers=2) as executor:
        for field, records in executor.map(lambda job: fetch(token, job), jobs(fields, left, right)):
            result[field].update(records)
    return result


def update(start, end, only=None):
    with bundle_lock(DEST.with_suffix(".lock")):
        raw, display_start = read_cache()
        if start and start > end:
            raise ValueError("开始日期晚于结束日期")
        display_start = min(filter(None, (start, display_start)), default=POINTS_BASE)
        columns = {"000300": 0, "H30401": 1, "bond": 2}
        sources = (only,) if only else ("dividend", "bond")
        token = os.environ.get("TUSHARE_TOKEN", "").strip() if "dividend" in sources else None
        failures = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}
            for kind in sources:
                col = columns["000300"] if kind == "dividend" else columns["bond"]
                history = [d for d, row in raw.items() if row[col] is not None]
                requested = start or display_start
                warmup = (last_year(requested) - timedelta(days=16)
                          if kind == "dividend" else requested)
                left, right = warmup, end
                if history:
                    first, last = min(history), max(history)
                    left = min(warmup, last - timedelta(days=31)) if start else last - timedelta(days=31)
                    right = max(end, first)  # 补抓更早区间时，把原始历史接续至已有记录。
                futures[kind] = executor.submit(fetch_source, kind, token, left, right)
            for kind, future in futures.items():
                try:
                    updates = future.result()
                except (requests.RequestException, ValueError, RuntimeError) as error:
                    failures.append(("股息率" if kind == "dividend" else "国债") + "未更新（" + str(error) + "）")
                    continue
                for field, rows in updates.items():
                    for day, value in rows.items():
                        raw.setdefault(day, [None, None, None])[columns[field]] = value
        if len(failures) == len(sources):
            raise RuntimeError("；".join(failures))
        write_bundle(raw, display_start)
        return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--start", type=date.fromisoformat, help="首个展示日或补抓历史的开始日")
    mode.add_argument("--daily", action="store_true", help="回查最近观测并续接；首次运行获取完整历史")
    parser.add_argument("--end", type=date.fromisoformat, help="截至日期，默认最近已收盘日")
    parser.add_argument("--only", choices=["dividend", "bond"], help="单独更新股息率或财政部曲线")
    args = parser.parse_args()
    failures = update(args.start, min(args.end or completed_date(), completed_date()), args.only)
    if failures:
        raise SystemExit("图⑥部分更新，" + "；".join(failures) + "。其他来源已正常处理。")
