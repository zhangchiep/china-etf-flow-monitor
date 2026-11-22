"""沪深场内 ETF 的 Tushare 原始输入。缓存保存接口字段和查询覆盖，不含其他来源。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata

import pandas as pd
import requests

DATA = Path(__file__).resolve().parents[1] / "数据"
CACHE = DATA / "ETF原始"
CHINA = timezone(timedelta(hours=8))
APIS = {"fund_share": "fd_share", "fund_daily": "close", "fund_adj": "adj_factor"}
GROUPS = ["沪深300","上证50","中证500","中证1000","中证2000","中证A500","创业板指","科创50","科创创业50","深证100"]
LOCAL = threading.local()
TIMES = {}
LOCK = threading.Lock()


def token():
    value = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not value:
        raise RuntimeError("请在看板中输入自己的 Tushare Token")
    return value


def query(key, api, fields, **params):
    if not hasattr(LOCAL, "http"):
        LOCAL.http = requests.Session()
    items, offset, last_first = [], 0, None
    while True:
        for attempt in range(4):
            with LOCK:
                due = max(time.monotonic(), TIMES.get(api, 0))
                TIMES[api] = due + .36
            time.sleep(max(0, due - time.monotonic()))
            try:
                r = LOCAL.http.post("https://api.tushare.pro", json={
                    "api_name":api, "token":key,"fields":",".join(fields),
                    "params":dict(params, offset=offset, limit=2000)}, timeout=(6,35),allow_redirects=False)
                r.raise_for_status()
                obj = r.json()
                if obj.get("code") != 0:
                    raise RuntimeError(f"Tushare {api} 返回 {obj.get('code')}，请检查接口权限和频次")
                block = obj["data"]
                if block["fields"] != fields:
                    raise ValueError(f"{api} 字段与请求不符")
                break
            except (requests.RequestException, RuntimeError, ValueError):
                if attempt == 3:
                    raise
                time.sleep(3 * (2 ** attempt))
        part = block["items"]
        if offset and part and part[0] == last_first:
            raise ValueError(f"{api} 重复返回同一页")
        items.extend(part)
        if not block.get("has_more") and len(part) < 2000:
            break
        if not part:
            raise ValueError(f"{api} 分页为空")
        last_first = part[0]
        offset += len(part)
    return pd.DataFrame(items, columns=fields)


def iso(t):
    return pd.to_datetime(t, format="%Y%m%d").strftime("%Y-%m-%d") if t else ""


def label(s):
    s = unicodedata.normalize("NFKC", str(s)).replace(" ","").upper()
    s = re.sub(r"(×|\*)100%$", "", s)
    s = s.replace("收益率","").replace("指数","").replace("上海证券交易所","上证").replace("深圳证券交易所","深证")
    return s.removesuffix("价格")


def directory(key, snapshot):
    date_tag = snapshot.replace("-","")
    CACHE.mkdir(parents=True, exist_ok=True)
    fundfile = CACHE / f"fund_basic_{date_tag}.csv.gz"
    ixfile = CACHE / f"index_basic_{date_tag}.csv.gz"
    if not fundfile.exists():
        fields = "ts_code,name,fund_type,list_date,delist_date,due_date,benchmark,status,purc_startdate,redm_startdate,market".split(",")
        frame = pd.concat([query(key,"fund_basic",fields,market="E",status=s) for s in ("D","L")],ignore_index=True)
        if frame.ts_code.duplicated().any() or not {"D","L"} <= set(frame.status):
            raise ValueError("Tushare 场内目录缺少存续状态或证券代码重复")
        write_table(fundfile, frame)
    if not ixfile.exists():
        fields = "ts_code,name,fullname,market,publisher,category".split(",")
        frame = pd.concat([query(key,"index_basic",fields,market=m,category=c)
                           for m in ("SSE","SZSE","CSI") for c in ("规模指数","综合指数")],ignore_index=True)
        ix = frame[frame.category.isin(["规模指数","综合指数"])].drop_duplicates()
        if not set(GROUPS) <= set(ix.name):
            raise ValueError("规模与综合指数目录未覆盖十个基础分组，请核对接口返回")
        write_table(ixfile, ix)
    cni_file = CACHE / f"index_basic_CNI_{date_tag}.csv.gz"
    if not cni_file.exists():
        fields = "ts_code,name,fullname,market,publisher,category".split(",")
        ix = pd.concat([query(key,"index_basic",fields,market="CNI",category=c)
                        for c in ("规模指数","综合指数")],ignore_index=True)
        if ix.empty:raise ValueError("国证指数目录为空，请稍后重试")
        write_table(cni_file,ix[ix.category.isin(["规模指数","综合指数"])].drop_duplicates())
    return (pd.read_csv(fundfile,dtype=str,keep_default_na=False),
            pd.concat([pd.read_csv(p,dtype=str,keep_default_na=False) for p in (cni_file,ixfile)],ignore_index=True))


def write_table(path, frame):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        frame.to_csv(tmp,index=False,encoding="utf-8-sig",compression="gzip")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def members(funds, indices):
    cross_border = r"港|海外|境外|全球|亚洲|大中华|美国|日本|韩国|印度|台湾|欧洲|新兴市场|美元|B股|恒生|标普|纳斯达克"
    indices = indices.loc[~(indices.name+indices.fullname).str.contains(cross_border, regex=True, case=False)]
    names = {}
    for r in indices.itertuples():
        for text in (r.name,r.fullname):
            if text:
                names[label(text)] = r.name
    # 同一公开指数的标准简称和全称存在表示方式差异。
    names.update({label(alias): group for alias,group in {
        "上证50成份":"上证50","上海证券交易所50成份":"上证50","创业板":"创业板指",
        "上证科创板50成份":"科创50","中证科创创业50":"科创创业50","深圳证券交易所成份":"深证成指"}.items()})
    # 外资指数公司编制、仅含中国 A 股的规模指数。精确名单避免引入价值、低波、行业衍生指数。
    for group, extra in {
        "MSCI中国A50互联互通": ["MSCI中国A50互联互通人民币"],
        "MSCI中国A股": ["MSCI中国A股人民币"],
        "MSCI中国A股国际通": ["MSCI中国A股国际通","MSCI中国A股国际通指数(MSCIChinaAInclusionRMBIndex)",
                           "MSCIChinaAInclusionRMBIndex(MSCI中国A股国际通指数)"],
        "MSCI中国A股国际": ["MSCI中国A股国际","MSCI中国A股国际指数(MSCIChinaAInternationalIndex)"],
        "富时中国A50": ["富时中国A50"],
    }.items():
        for alias in extra:names[label(alias)] = group
    f = funds.loc[funds.ts_code.str.fullmatch(r"\d{6}\.(?:SH|SZ)") & funds.market.eq("E")
                  & funds.name.str.contains("ETF",case=False) & funds.list_date.ne("")
                  & ~funds.name.str.contains("联接|主动|QDII|"+cross_border,case=False,regex=True)].copy()
    f["group"] = f.benchmark.map(lambda x:names.get(label(x),""))
    f = f.loc[f.group.ne("")].copy()
    for col in ("list_date","delist_date","due_date","purc_startdate","redm_startdate"):
        f[col] = f[col].map(iso)
    f["last"] = [min((x for x in (r.delist_date,r.due_date if r.status=="D" else "") if x),default="") for r in f.itertuples()]
    f = f.loc[~(f.status.eq("D") & f["last"].eq(""))]
    return f.sort_values("ts_code").set_index("ts_code",drop=False)


def disclosed_members(funds, through):
    """在派生名单上应用已披露的申赎业务日期，保留 Tushare 原始字段供核对。"""
    dates = json.loads(Path(__file__).with_name("etf_disclosures.json").read_text(encoding="utf-8"))
    available = [item for item in dates if item["publishedOn"] <= through and item["code"] in funds.index]
    f = funds.copy()
    f["eligible_to"] = f["last"]
    events = {}
    for item in available:
        code = item["code"]
        for field in item["fields"]:
            when = item["effectiveOn"]
            if field in ("purc_startdate","redm_startdate"):
                f.at[code,field] = when
            else:
                events.setdefault(code,{})[field] = when
    for code, timeline in events.items():
        suspensions = [timeline.get("suspend_purchases"),timeline.get("suspend_redemptions")]
        if all(suspensions):
            closed = max(suspensions)
            # 暂停当日有公告直接支持；后续无限期排除须有明确的不再恢复决定。
            if closed == through or timeline.get("liquidation_without_reopening"):
                last = (date.fromisoformat(closed)-timedelta(days=1)).isoformat()
                f.at[code,"eligible_to"] = min(x for x in (last,f.at[code,"last"]) if x)
    return f,available


def sessions(key, start, end):
    days = []
    first = (date.fromisoformat(start)-timedelta(days=40)).isoformat()
    for year in range(int(first[:4]), int(end[:4])+1, 4):
        a,z = max(f"{year}-01-01",first),min(f"{year+3}-12-31",end)
        table=query(key,"trade_cal",["cal_date","is_open"],exchange="SSE",
                    start_date=a.replace("-",""),end_date=z.replace("-",""))
        opened=pd.to_numeric(table.is_open,errors="raise")
        if set(table.cal_date)!=set(pd.date_range(a,z).strftime("%Y%m%d")) or table.cal_date.duplicated().any() or not opened.isin([0,1]).all():
            raise ValueError("交易日历缺失、重复或尚未发布")
        days+=table.loc[opened.eq(1),"cal_date"].map(iso).tolist()
    index=pd.Index(sorted(days))
    return index[index<start][-2:].append(index[index>=start])


def database():
    CACHE.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(CACHE/"tushare_etf.sqlite",timeout=120)
    db.executescript("""PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS raw(code TEXT,day TEXT,fd_share REAL,close REAL,adj_factor REAL,
            PRIMARY KEY(code,day)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS span(code TEXT PRIMARY KEY, left_day TEXT, right_day TEXT);
        CREATE TABLE IF NOT EXISTS cross_section(day TEXT PRIMARY KEY, checked REAL);""")
    return db


def observations(key, **params):
    common=["ts_code","trade_date"]
    cols=[]
    for api,col in APIS.items():
        f=query(key,api,common+[col],**params)
        if "ts_code" in params and not f.ts_code.eq(params["ts_code"]).all():
            raise ValueError(f"{api} 返回证券与请求不符")
        if "trade_date" in params and not f.trade_date.eq(params["trade_date"]).all():
            raise ValueError(f"{api} 返回日期与请求不符")
        unique=f.drop_duplicates()
        bad=unique.duplicated(common,keep=False)
        unique=unique.copy()
        unique.loc[bad,col]=float("nan")
        cols.append(unique.drop_duplicates(common).set_index(common)[col])
    new=pd.concat(cols,axis=1).reset_index()
    new["trade_date"]=new.trade_date.map(iso)
    return new


def store(db, frame):
    db.executemany("""INSERT INTO raw VALUES(?,?,?,?,?) ON CONFLICT(code,day) DO UPDATE SET
        fd_share=coalesce(excluded.fd_share,raw.fd_share), close=coalesce(excluded.close,raw.close),
        adj_factor=coalesce(excluded.adj_factor,raw.adj_factor)""",
        frame.astype(object).where(frame.notna(),None).itertuples(index=False,name=None))


def download(db, key, funds, days, recheck_from=None):
    left,right=days[0],days[-1]
    codes=set(funds.ts_code)
    span={c:(a,z) for c,a,z in db.execute("SELECT * FROM span")}
    needs={}
    tails={}
    for r in funds.itertuples(index=False):
        a,z=max(left,r.list_date),min(right,r.last or right)
        if a>z:continue
        covered=span.get(r.ts_code)
        if covered and covered[0]<=a and covered[1]>=z:continue
        if (covered and covered[0] <= a <= covered[1] < z
                and len(days[(days>covered[1]) & (days<=z)])<=25):
            tails[r.ts_code] = (a,z,covered[1])
        else:
            needs[r.ts_code]=(a,z)
    # A few shared trading days are much cheaper to fetch by date than by fund.
    if len(days)>25 and needs:
        def fetch(item):
            code,(a,z) = item
            cached=span.get(code)
            ra,rz = a,z
            if cached and a>=cached[0] and a<=cached[1] and z>cached[1]:
                ra=cached[1]
            elif cached and z>=cached[0] and z<=cached[1] and a<cached[0]:
                rz=cached[0]
            frame=observations(key,ts_code=code,start_date=ra.replace("-",""),end_date=rz.replace("-",""))
            if len(frame) and not frame.trade_date.between(ra,rz).all():raise ValueError("证券查询日期不符")
            return code,a,z,frame
        with ThreadPoolExecutor(8) as workers:
            futures=[workers.submit(fetch,item) for item in needs.items()]
            for i,future in enumerate(as_completed(futures),1):
                code,a,z,frame=future.result()
                with db:
                    store(db,frame)
                    db.execute("INSERT OR REPLACE INTO span VALUES (?,?,?)",(code,a,z))
                if i%40==0 or i==len(needs):print(f"已保存 {i}/{len(needs)} 只的原始行情、份额与因子",flush=True)
    checked=dict(db.execute("SELECT * FROM cross_section"))
    recent = set(days if len(days)<=25 else days[-8:])
    if tails:
        last_known=min(span_end for _,_,span_end in tails.values())
        recent.update(days[days>last_known])
    if recheck_from:
        recent.update(days[days>=recheck_from])
    for day in sorted(recent):
        if time.time()-checked.get(day,0)<1800 and not (recheck_from and day>=recheck_from):continue
        frame=observations(key,trade_date=day.replace("-",""))
        # Keep the complete raw cross section so new index members can share the same cache.
        with db:
            store(db,frame)
            if not frame.empty:
                checked[day]=time.time()
                db.execute("INSERT OR REPLACE INTO cross_section VALUES(?,?)",(day,checked[day]))
    # 单日全市场补取覆盖此证券后才能扩展其采集区间。
    with db:
        for code,(a,z,span_end) in tails.items():
            if all(d in checked for d in days[(days>span_end)&(days<=z)]):
                db.execute("UPDATE span SET right_day=? WHERE code=?",(z,code))


def raw_panels(db, days, codes):
    raw=pd.read_sql_query("SELECT * FROM raw WHERE day BETWEEN ? AND ?",db,params=(days[0],days[-1]))
    raw=raw[raw.code.isin(codes)]
    return {field:raw.pivot(index="day",columns="code",values=field).reindex(index=days,columns=codes)
            for field in APIS.values()}
