"""그린블라트 마법공식 순위 업데이트.

python scripts/update.py          # 시세만 새로 받고, 재무는 캐시 사용 (없는 종목만 새로 받음)
python scripts/update.py --full   # 재무제표까지 전부 새로 받음
"""
import json, math, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIN_DIR = os.path.join(ROOT, "data", "fin")
OUT = os.path.join(ROOT, "docs", "data.json")
TOP_N = 300
UA = {"User-Agent": "Mozilla/5.0"}
WR = "https://navercomp.wisereport.co.kr/v2/company/"
KST = timezone(timedelta(hours=9))

EXCL_SECT = {"은행", "증권", "보험", "손해보험", "생명보험", "다각화된금융", "카드", "창업투자", "기타금융", "부동산",
             "전기유틸리티", "가스유틸리티", "복합유틸리티", "수도유틸리티", "독립전력생산및에너지거래"}
# 필요한 계정코드만 저장
KEEP = {
    "isq": ["201370", "200220"],  # 영업이익, 지분법이익(매출 내)
    "isy": ["201370"],
    "bsq": ["112830", "131580", "110020", "190980", "190990", "120620", "131650", "190940"],
    # 유동자산, 유동부채, 유형자산, 이자발생부채, 순부채, 비지배지분, 단기차입금, 유동성장기부채
}


def fetch_list(market):
    rows = []
    for p in range(1, 40):
        r = requests.get(f"https://m.stock.naver.com/api/stocks/marketValue/{market}?page={p}&pageSize=100",
                         headers=UA, timeout=20).json()
        if not r["stocks"]:
            break
        for x in r["stocks"]:
            if x["stockEndType"] != "stock":
                continue
            rows.append(dict(code=x["itemCode"], name=x["stockName"], price=int(x["closePriceRaw"]),
                             mcap=int(x["marketValueRaw"]) / 1e8, status=x.get("marketStatus")))
        time.sleep(0.15)
    return rows


def fetch_fin(code):
    s = requests.Session()
    s.headers.update(UA)
    for attempt in range(3):
        try:
            h = s.get(WR + f"c1010001.aspx?cmp_cd={code}", timeout=20).text
            m = re.search(r"encparam: '([^']+)'", h)
            w = re.search(r"WICS : ([^<]+)", h)
            out = {"code": code, "wics": w.group(1).strip() if w else None, "fetched": datetime.now(KST).isoformat()}
            if not m:
                out["err"] = "noenc"
                return out
            for key, rpt, frq in [("isq", 0, 1), ("bsq", 1, 1), ("isy", 0, 0)]:
                j = s.get(WR + "cF3002.aspx",
                          params=dict(cmp_cd=code, frq=frq, rpt=rpt, finGubun="MAIN", frqTyp=frq, cn="", encparam=m.group(1)),
                          headers={"Referer": WR + f"c1030001.aspx?cmp_cd={code}"}, timeout=20).json()
                out[key] = {"YYMM": [re.sub(r"<br />.*", "", y) for y in j["YYMM"][:6]],
                            "acc": {d["ACCODE"]: [d.get(f"DATA{i}") for i in range(1, 7)]
                                    for d in j["DATA"] if d["ACCODE"] in KEEP[key]}}
            return out
        except Exception as e:
            err = str(e)
            time.sleep(2)
    return {"code": code, "err": err}


def load_fin(code, refresh):
    fn = os.path.join(FIN_DIR, f"{code}.json")
    if not refresh and os.path.exists(fn):
        return json.load(open(fn, encoding="utf-8"))
    d = fetch_fin(code)
    if "err" in d and os.path.exists(fn):  # 실패하면 기존 캐시 유지
        return json.load(open(fn, encoding="utf-8"))
    json.dump(d, open(fn, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return d


def actual_cols(blk):
    return [i for i, y in enumerate(blk["YYMM"]) if "(E)" not in y]


def compute(stock, pref_mcap, d):
    z = lambda v: v if v is not None else 0.0
    r = dict(name=stock["name"], code=stock["code"], mcap=stock["mcap"], wics=d.get("wics"))
    if r["wics"] in EXCL_SECT:
        return r, f"금융·유틸리티 ({r['wics']})"
    if "err" in d or "bsq" not in d:
        return r, "재무데이터 없음"
    bs = d["bsq"]
    a = lambda blk, k: blk["acc"].get(k, [None] * 6)
    ca, cl = a(bs, "112830"), a(bs, "131580")
    bi = [i for i in actual_cols(bs) if ca[i] is not None and cl[i] is not None]
    if not bi:
        return r, "재무상태표(유동자산) 없음"
    b = bi[-1]
    debt, nd = z(a(bs, "190980")[b]), a(bs, "190990")[b]
    cash = debt - nd if nd is not None else 0.0
    stdebt = z(a(bs, "131650")[b]) + z(a(bs, "190940")[b])
    nwc = max(0.0, (ca[b] - cash) - (cl[b] - stdebt))
    nfa = z(a(bs, "110020")[b])

    iq = d["isq"]
    op = a(iq, "201370")
    qa = [i for i in actual_cols(iq) if op[i] is not None]
    if len(qa) >= 4 and qa[-4:] == list(range(qa[-4], qa[-4] + 4)):
        q = qa[-4:]
        ebit = sum(op[i] for i in q)
        basis = f"최근4분기 {iq['YYMM'][q[0]]}~{iq['YYMM'][q[-1]]}"
        eqi = sum(z(a(iq, "200220")[i]) for i in q)
        if ebit > 0 and eqi > 0.5 * ebit:
            return r, f"투자지주회사 (영업이익의 {eqi / ebit * 100:.0f}%가 지분법이익)"
    else:
        iy = d["isy"]
        opy = a(iy, "201370")
        ya = [i for i in actual_cols(iy) if opy[i] is not None]
        if not ya:
            return r, "영업이익 없음"
        ebit = opy[ya[-1]]
        basis = f"연간 {iy['YYMM'][ya[-1]]}"

    ev = stock["mcap"] + pref_mcap + z(nd) + z(a(bs, "120620")[b])
    cap = nwc + nfa
    r.update(ebit=ebit, capital=cap, ev=ev, basis=basis, bs=bs["YYMM"][b],
             roc=ebit / cap if cap > 0 else (math.inf if ebit > 0 else -math.inf),
             ey=ebit / ev if ev > 0 else (math.inf if ebit > 0 else -math.inf))
    return r, None


def main():
    full = "--full" in sys.argv
    os.makedirs(FIN_DIR, exist_ok=True)
    result = {"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "markets": {}}
    for market in ["KOSPI", "KOSDAQ"]:
        stocks = fetch_list(market)
        common = [s for s in stocks if s["code"][-1] == "0"]
        pref = {}
        for s in stocks:
            if s["code"][-1] != "0":
                k = s["code"][:5] + "0"
                pref[k] = pref.get(k, 0) + s["mcap"]
        common.sort(key=lambda s: -s["mcap"])
        top = common[:TOP_N]
        with ThreadPoolExecutor(4) as ex:
            fins = list(ex.map(lambda s: load_fin(s["code"], full), top))
        ranked, excluded = [], []
        for s, d in zip(top, fins):
            r, why = compute(s, pref.get(s["code"], 0), d)
            if why:
                excluded.append([r["name"], r["code"], r["wics"], round(r["mcap"]), why])
            else:
                ranked.append(r)
        for k in ("roc", "ey"):
            for i, r in enumerate(sorted(ranked, key=lambda r: -r[k])):
                r[k + "_rank"] = i + 1
        ranked.sort(key=lambda r: (r["roc_rank"] + r["ey_rank"], r["ey_rank"]))
        pct = lambda v: (round(v * 100, 1) if math.isfinite(v) else ("inf" if v > 0 else "-inf"))
        rows = [[i + 1, r["name"], r["code"], r["wics"], round(r["mcap"]), round(r["ebit"]), round(r["capital"]),
                 pct(r["roc"]), r["roc_rank"], round(r["ev"]), pct(r["ey"]), r["ey_rank"],
                 r["roc_rank"] + r["ey_rank"], r["basis"]] for i, r in enumerate(ranked)]
        result["markets"][market] = {"total": len(common), "r": rows, "x": excluded,
                                     "bs": max((r["bs"] for r in ranked), default=None)}
        print(market, "common", len(common), "ranked", len(rows), "excluded", len(excluded))
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
