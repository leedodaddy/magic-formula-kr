"""그린블라트 마법공식 순위 업데이트 (미국 S&P 500).

python scripts/update_us.py          # 시세만 새로 받고, 재무는 캐시 사용 (없는 종목만 새로 받음)
python scripts/update_us.py --full   # 구성종목·재무제표까지 전부 새로 받음
"""
import io, json, math, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIN_DIR = os.path.join(ROOT, "data", "fin_us")
MEMBERS = os.path.join(ROOT, "data", "sp500.json")
OUT = os.path.join(ROOT, "docs", "data_us.json")
KST = timezone(timedelta(hours=9))

# 그린블라트 원칙: 금융·유틸리티 제외 (리츠는 2016년 전까지 금융 섹터였으므로 함께 제외)
EXCL_SECT = {"Financials": "금융", "Utilities": "유틸리티", "Real Estate": "부동산·리츠"}
# GICS상 헬스케어지만 건강보험 사업을 하는 회사 (순위에는 두고 표시만 함)
HEALTH_INS = {"CI", "CVS"}

SECTOR_KO = {"Industrials": "산업재", "Information Technology": "IT", "Health Care": "헬스케어",
             "Consumer Discretionary": "경기소비재", "Consumer Staples": "필수소비재", "Materials": "소재",
             "Communication Services": "커뮤니케이션", "Energy": "에너지", "Financials": "금융",
             "Utilities": "유틸리티", "Real Estate": "부동산"}


def fetch_members():
    h = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                     headers={"User-Agent": "Mozilla/5.0 (magic-formula-kr)"}, timeout=30).text
    t = pd.read_html(io.StringIO(h))[0]
    groups = {}
    for _, r in t.iterrows():  # 같은 회사의 여러 주식 종류(GOOGL/GOOG 등)는 한 회사로 묶음
        g = groups.setdefault(int(r["CIK"]), {"name": r["Security"].split(" (Class")[0], "tickers": [],
                                              "sector": r["GICS Sector"], "industry": r["GICS Sub-Industry"]})
        g["tickers"].append(r["Symbol"].replace(".", "-"))
    members = list(groups.values())
    json.dump(members, open(MEMBERS, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return members


def row(df, *names):
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def fetch_fin(tickers):
    t = yf.Ticker(tickers[0])
    for attempt in range(3):
        try:
            out = {"tickers": tickers, "fetched": datetime.now(KST).isoformat()}
            q = t.quarterly_income_stmt
            op = row(q, "Operating Income", "EBIT")
            out["opq"] = {c.strftime("%Y-%m-%d"): num(v) for c, v in op.items()} if op is not None else {}
            y = t.income_stmt
            opy = row(y, "Operating Income", "EBIT")
            out["opy"] = {c.strftime("%Y-%m-%d"): num(v) for c, v in opy.items()} if opy is not None else {}
            b = t.quarterly_balance_sheet
            if b is None or b.empty:
                b = t.balance_sheet
            col = None
            for c in b.columns:  # 유동자산이 있는 가장 최근 분기
                if "Current Assets" in b.index and num(b.at["Current Assets", c]) is not None:
                    col = c
                    break
            if col is None:
                col = b.columns[0]
            g = lambda *ns: next((num(b.at[n, col]) for n in ns if n in b.index and num(b.at[n, col]) is not None), None)
            total_eq, sh_eq = g("Total Equity Gross Minority Interest"), g("Stockholders Equity")
            out["bs"] = {
                "date": col.strftime("%Y-%m-%d"),
                "ca": g("Current Assets"), "cl": g("Current Liabilities"),
                "cash": g("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"),
                "stdebt": g("Current Debt And Capital Lease Obligation", "Current Debt"),
                "debt": g("Total Debt"), "ppe": g("Net PPE"),
                "minority": g("Minority Interest") or ((total_eq - sh_eq) if total_eq and sh_eq else 0),
                "preferred": g("Preferred Stock") or 0,
            }
            fi = t.fast_info
            mcap, price = fi["marketCap"], fi["lastPrice"]
            out["shares"] = mcap / price if mcap and price else None  # 시세 반영용 환산 주식수
            out["mcap"] = mcap
            return out
        except Exception as e:
            err = str(e)
            time.sleep(10 + attempt * 20)
    return {"tickers": tickers, "err": err}


def load_fin(m, refresh):
    fn = os.path.join(FIN_DIR, f"{m['tickers'][0]}.json")
    if not refresh and os.path.exists(fn):
        d = json.load(open(fn, encoding="utf-8"))
        if "err" not in d:
            return d
    time.sleep(0.5)  # Yahoo 요청 제한 회피
    d = fetch_fin(m["tickers"])
    if "err" in d and os.path.exists(fn):
        return json.load(open(fn, encoding="utf-8"))
    json.dump(d, open(fn, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return d


def ttm(d):
    q = sorted(((k, v) for k, v in d.get("opq", {}).items() if v is not None), reverse=True)
    if len(q) >= 4:
        dates = [datetime.fromisoformat(k) for k, _ in q[:4]]
        if (dates[0] - dates[3]).days < 300:  # 4분기가 연속인지 확인
            return sum(v for _, v in q[:4]), f"최근4분기 ~{q[0][0][:7]}"
    y = sorted(((k, v) for k, v in d.get("opy", {}).items() if v is not None), reverse=True)
    if y:
        return y[0][1], f"연간 {y[0][0][:7]}"
    return None, None


def main():
    full = "--full" in sys.argv
    os.makedirs(FIN_DIR, exist_ok=True)
    members = fetch_members() if full or not os.path.exists(MEMBERS) else json.load(open(MEMBERS, encoding="utf-8"))
    with ThreadPoolExecutor(2) as ex:
        fins = list(ex.map(lambda m: load_fin(m, full), members))

    # 최신 시세 반영: 첫 번째 주식 종류의 가격 × 환산 주식수
    firsts = [m["tickers"][0] for m in members]
    prices = {}
    if not full:
        try:
            px = yf.download(firsts, period="5d", interval="1d", progress=False, threads=True)["Close"]
            prices = {k: float(v) for k, v in px.ffill().iloc[-1].items() if not math.isnan(v)}
        except Exception as e:
            print("price download failed:", e)

    ranked, excluded = [], []
    z = lambda v: v or 0.0
    for m, d in zip(members, fins):
        base = [m["name"], "/".join(m["tickers"]), SECTOR_KO.get(m["sector"], m["sector"])]
        mcap = d.get("mcap")
        if d.get("shares") and m["tickers"][0] in prices:
            mcap = d["shares"] * prices[m["tickers"][0]]
        if m["sector"] in EXCL_SECT:
            excluded.append(base + [round((mcap or 0) / 1e9, 1), f"{EXCL_SECT[m['sector']]} 업종"])
            continue
        bs = d.get("bs") or {}
        ebit, basis = ttm(d) if "err" not in d else (None, None)
        if "err" in d or ebit is None or bs.get("ca") is None or bs.get("cl") is None or not mcap:
            excluded.append(base + [round((mcap or 0) / 1e9, 1), "재무데이터 없음 (유동자산 구분이 없는 회사 등)"])
            continue
        nwc = max(0.0, (bs["ca"] - z(bs["cash"])) - (bs["cl"] - z(bs["stdebt"])))
        cap = nwc + z(bs["ppe"])
        ev = mcap + z(bs["preferred"]) + z(bs["debt"]) - z(bs["cash"]) + z(bs["minority"])
        tag = "건강보험" if m["industry"] == "Managed Health Care" or m["tickers"][0] in HEALTH_INS else ""
        ranked.append(dict(name=m["name"], tick=base[1], sector=base[2], industry=m["industry"], mcap=mcap, tag=tag,
                           ebit=ebit, capital=cap, ev=ev, basis=basis, bs=bs["date"],
                           roc=ebit / cap if cap > 0 else (math.inf if ebit > 0 else -math.inf),
                           ey=ebit / ev if ev > 0 else (math.inf if ebit > 0 else -math.inf)))
    for k in ("roc", "ey"):
        for i, r in enumerate(sorted(ranked, key=lambda r: -r[k])):
            r[k + "_rank"] = i + 1
    ranked.sort(key=lambda r: (r["roc_rank"] + r["ey_rank"], r["ey_rank"]))
    pct = lambda v: (round(v * 100, 1) if math.isfinite(v) else ("inf" if v > 0 else "-inf"))
    b = lambda v: round(v / 1e9, 2)  # 십억 달러
    rows = [[i + 1, r["name"], r["tick"], r["sector"], b(r["mcap"]), b(r["ebit"]), b(r["capital"]), pct(r["roc"]),
             r["roc_rank"], b(r["ev"]), pct(r["ey"]), r["ey_rank"], r["roc_rank"] + r["ey_rank"], r["basis"], r["tag"]]
            for i, r in enumerate(ranked)]
    out = {"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
           "markets": {"SP500": {"total": len(members), "r": rows, "x": excluded,
                                 "bs": max((r["bs"] for r in ranked), default=None)}}}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("members", len(members), "ranked", len(rows), "excluded", len(excluded))


if __name__ == "__main__":
    main()
