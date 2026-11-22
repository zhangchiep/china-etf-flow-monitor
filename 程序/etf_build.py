"""用 Tushare 当期目录和原始份额、未复权收盘价及复权因子，生成图②与图③。"""
import argparse
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd

import etf_source as src

FLOW_METHOD = "tushare-fixed-benchmark-v2"
MARKET_MODE = "tushare-directory"
FILES = {"flows": ("flows.js","window.FLOW_DATA="), "market": ("marketflow.js","window.MARKET_FLOW=")}
SOURCE = ("Tushare fund_basic 和 index_basic 确定候选，上交所披露的基金公告补核申赎业务日期，trade_cal 对齐有效交易日，"
          "fund_share 基金份额（万份）的相邻交易日变化乘 fund_daily 未复权收盘价，除以一万得到亿元；"
          "使用 fund_adj 和份额价格跳变排查折算。")


def read_bundle(which):
    filename,prefix=FILES[which]
    path=src.DATA/filename
    if not path.exists():return None
    raw=path.read_text(encoding="utf-8-sig").strip()
    if not raw.startswith(prefix) or not raw.endswith(";"):
        raise ValueError(f"已有{filename}文件格式异常")
    obj=json.loads(raw[len(prefix):-1])
    if obj.get("method") not in (FLOW_METHOD,"tushare-market-v2") or obj.get("demo"):
        raise ValueError(f"{filename}为其他计算口径，请在单独目录构建")
    return obj


def publish(which,obj):
    filename,prefix=FILES[which]
    with NamedTemporaryFile("w",encoding="utf-8",dir=src.DATA,delete=False) as out:
        path=Path(out.name)
        out.write(prefix+json.dumps(obj,ensure_ascii=False,allow_nan=False,separators=(",",":"))+";\n")
    try:path.replace(src.DATA/filename)
    finally:path.unlink(missing_ok=True)


def numbers(series):
    return [round(float(v),9) if pd.notna(v) and np.isfinite(v) else None for v in series]


def calculations(days, funds, panels, pool, snapshot):
    shares,price,factors=[panels[k] for k in ("fd_share","close","adj_factor")]
    codes=shares.columns
    listed=pd.DataFrame(False,index=days,columns=codes)
    active=listed.copy()
    opened=listed.copy()
    for r in funds.itertuples(index=False):
        live=(days>=r.list_date)&(days<=(r.last or days[-1]))
        open_day=min((t for t in (r.purc_startdate,r.redm_startdate) if t),default="")
        listed[r.ts_code]=live
        active[r.ts_code]=live & (days >= (open_day or r.list_date)) & (days <= (r.eligible_to or days[-1]))
        if open_day:opened[r.ts_code]=active[r.ts_code]
    prior=shares.shift(1)
    known_factors=factors.gt(0) & factors.shift(1).gt(0) & factors.shift(2).gt(0)
    change=factors.diff().abs().gt(.00000001)
    share_ratio=shares/prior.where(prior.gt(0))
    price_ratio=price/price.shift().where(price.shift().gt(0))
    jump=(((share_ratio>=1.25)&(price_ratio<=1/1.15))|((share_ratio<=1/1.25)&(price_ratio>=1.15))) & (share_ratio*price_ratio).sub(1).abs().le(.2)
    suspicious=change | change.shift(fill_value=False) | jump | jump.shift(fill_value=False)
    first_day=active & opened & ~listed.shift(fill_value=False)
    unknown_open=active & ~opened
    comparable=active & opened & ~first_day
    excluded=comparable & suspicious
    raw_valid=~suspicious & known_factors & shares.gt(0) & prior.gt(0) & price.gt(0)
    valid=comparable & raw_valid
    raw_amount=((shares-prior)*price/1e4).where(raw_valid)
    daily=raw_amount.where(valid)
    observed=daily.sum(axis=1,min_count=1); eligible=active.sum(axis=1); covered=valid.sum(axis=1)
    strict=observed.where(eligible.eq(covered) & eligible.gt(0))
    base=(prior*price/1e4).where(valid).sum(axis=1,min_count=1)
    missing=comparable & ~suspicious & ~valid
    if not (eligible==covered+first_day.sum(axis=1)+unknown_open.sum(axis=1)+excluded.sum(axis=1)+missing.sum(axis=1)).all():
        raise ValueError("覆盖数不能与有效及排除成员核对")
    target=days[2:]
    valid_range=observed.loc[target].dropna()
    if valid_range.empty:raise ValueError("当前数据区间没有可观测的净申赎额")
    market_dates=target[target<=valid_range.index[-1]]
    cand_count=int(active.loc[market_dates].any().sum())
    note=(f"以 {snapshot} 的 Tushare 上市及摘牌基金目录、规模/综合指数基准匹配重建。"
          "申赎开始与终止日期包含上交所已披露的基金公告补核。"
          "历史跟踪变更、未匹配基准和无效申赎开放日可能影响覆盖，不能据此识别交易者身份。")
    market=dict(method="tushare-market-v2",sourceMode=MARKET_MODE,dates=market_dates.tolist(),firstDate=market_dates[0],
                lastDate=market_dates[-1],directoryDate=snapshot,dataStage="Tushare 目录与公开公告估算",unit="亿元",
                checkedEligible=int(eligible.iloc[-1]),checkedCovered=int(covered.iloc[-1]),
                distinctFunds=cand_count,groups=int(funds.group.nunique()),universeVerifiedComplete=False,
                universeNote=note,source=SOURCE,membershipStatus=note,description="红绿柱为逐日已覆盖净额，累计曲线随选择的起点更新。",
                limitations="结算份额的记录日期、早年缺口和基金分类调整可能影响结果。份额要求前日和当日均为正，"
                            "零记录不能从该接口单独确认为全部赎回，留空待核。复权因子变动或份额与价格反向异常跳变时当日及次日留空。")
    for field,values in {"daily":strict,"observed":observed,"observedBase":base,"observedRate":100*observed/base.where(base>0)}.items():
        market[field]=numbers(values.loc[market_dates])
    for field,values in {"eligible":eligible,"covered":covered,"firstDay":first_day.sum(axis=1),"split":excluded.sum(axis=1),
                         "missing":missing.sum(axis=1),"unknownOpen":unknown_open.sum(axis=1)}.items():
        market[field]=values.loc[market_dates].astype(int).tolist()
    groups=pd.DataFrame(index=market_dates,columns=src.GROUPS,dtype=float)
    sizes=[]
    for group in src.GROUPS:
        members=[x["code"] for x in pool if x["group"]==group]
        sizes.append(len(members))
        if not members:continue
        amounts=raw_amount.loc[market_dates,members].copy()
        for row in pool:
            if row["code"] not in members:continue
            listed_days=pd.Series((days>=row["listed"]) & (days<=(row["last"] or days[-1])),index=days)
            usable=(listed_days & listed_days.shift(fill_value=False) & (days>=row["open_from"])).loc[market_dates]
            amounts.loc[~usable,row["code"]]=np.nan
        groups[group]=amounts.sum(axis=1,min_count=len(members))
    flows=dict(method=FLOW_METHOD,pool=pool,poolSize=len(pool),groups=src.GROUPS,groupSizes=sizes,dates=market_dates.tolist(),
               daily=[numbers(row) for row in groups.itertuples(index=False,name=None)],catalogReadOn=snapshot,
               source=SOURCE+" 图②锁定十组基准日仍上市开放的被动 ETF，只有所有固定成员都有可比数值时才显示组值，早年可能整组留空。")
    return flows,market


@contextmanager
def update_lock():
    src.CACHE.mkdir(exist_ok=True,parents=True)
    with (src.CACHE/"update.lock").open("a+b") as f:
        if os.name=="nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX)
        yield


def build(start,end,recheck_from=None):
    with update_lock(), closing(src.database()) as db:
        previous=read_bundle("market")
        fixed=read_bundle("flows")
        if start is None and not previous:raise ValueError("首次计算需要 --start YYYY-MM-DD")
        if previous:
            start=min(start or previous["requestedStart"],previous["requestedStart"])
            if end < previous["lastDate"]:raise ValueError("更新截止日不能早于已发布末日")
        if start>end:raise ValueError("起始日须不晚于截止日")
        key=src.token()
        snapshot=datetime.now(src.CHINA).date().isoformat()
        basic,indices=src.directory(key,snapshot)
        base_members=src.members(basic,indices)
        funds,notices=src.disclosed_members(base_members,end)
        days=src.sessions(key,start,end)
        if len(days)<3:raise ValueError("所选区间没有可用交易日")
        funds=funds.loc[funds.list_date.le(days[-1]) & (funds["last"].eq("")|funds["last"].ge(days[0]))]
        asof=fixed["poolAsOf"] if fixed else days[-1]
        if not fixed or not fixed.get("disclosureVersion"):
            # 首建以及一次性迁移：仅将基准日前已公开且被这次公告核实的成员补入旧名单。
            at_base,baseline_notices=src.disclosed_members(base_members,asof)
            sample=at_base.loc[at_base.group.isin(src.GROUPS)&at_base.fund_type.eq("股票型")&
                    ~at_base.name.str.contains("增强|指增") & at_base.list_date.le(asof) &
                    (at_base.eligible_to.eq("")|at_base.eligible_to.ge(asof)) &
                    at_base.purc_startdate.between("0001",asof)&at_base.redm_startdate.between("0001",asof)]
            pool=list(fixed["pool"]) if fixed else []
            if fixed:
                correction_codes={n["code"] for n in baseline_notices if "purc_startdate" in n["fields"]}
                sample=sample.loc[sample.ts_code.isin(correction_codes-set(r["code"] for r in pool))]
            pool.extend(dict(code=r.ts_code,name=r.name,group=r.group,listed=r.list_date,last=r.last,
                             open_from=max(r.list_date,r.purc_startdate,r.redm_startdate)) for r in sample.itertuples(index=False))
            pool.sort(key=lambda row:row["code"])
        else:
            pool=fixed["pool"]
        if recheck_from and not start<=recheck_from<=end:raise ValueError("重查起点须落在构建区间内")
        # 固定组别沿用首建名单。即使当期目录发生换标，图②仍查询该证券；
        # 图③继续只按当期目录识别的宽基范围估算，两个名单独立维护。
        raw_funds=funds[["ts_code","list_date","last"]].copy()
        basic_by_code=basic.set_index("ts_code")
        for row in pool:
            code=row["code"]
            if code in basic_by_code.index:
                metadata=basic_by_code.loc[code]
                end_dates=[src.iso(t) for t in (metadata.delist_date,metadata.due_date if metadata.status=="D" else "") if t]
                if end_dates:row["last"]=min(end_dates)
            if code not in raw_funds.index:
                raw_funds.loc[code]=[code,row["listed"],row["last"]]
        src.download(db,key,raw_funds,days,recheck_from)
        panels=src.raw_panels(db,days,raw_funds.ts_code.tolist())
        flows,market=calculations(days,funds,panels,pool,snapshot)
        flows["poolAsOf"]=asof
        market["requestedStart"]=start
        for bundle in (flows,market):
            bundle["checkedThrough"]=days[-1]
            bundle["requestedEnd"]=end
            bundle["disclosureVersion"]=1
            bundle["disclosures"]=notices
        market["dailyWaitingFor"]=days[-1] if days[-1]>market["lastDate"] else None
        if previous and market["lastDate"]<previous["lastDate"]:
            raise ValueError("本次输入覆盖不及已发布末日，旧图表保留")
        publish("flows",flows)
        publish("market",market)
        print(f"图② {len(pool)} 只固定样本 / 图③ {market['distinctFunds']} 只历史基金；"
              f"图③末日 {market['lastDate']} 有效 {market['covered'][-1]}/{market['eligible'][-1]}，"
              f"净额 {market['observed'][-1]} 亿元。",flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--start",type=date.fromisoformat)
    group.add_argument("--daily",action="store_true")
    now=datetime.now(src.CHINA)
    closed=now.date() if now.hour>=15 else now.date()-timedelta(days=1)
    parser.add_argument("--end",type=date.fromisoformat,default=closed)
    parser.add_argument("--recheck-from",type=date.fromisoformat,help="强制按日重取此前迟报或修订的原始字段")
    args=parser.parse_args()
    if args.end>closed:parser.error("截止日须为已收盘日期")
    build(args.start.isoformat() if args.start else None,args.end.isoformat(),
          args.recheck_from.isoformat() if args.recheck_from else None)
