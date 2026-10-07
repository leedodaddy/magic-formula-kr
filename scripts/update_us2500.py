"""그린블라트 마법공식 순위 업데이트 (미국 시가총액 상위 2,500개).

python scripts/update_us2500.py               # 시세만 새로 받고, 재무는 캐시 사용 (없는 종목만 새로 받음)
python scripts/update_us2500.py --refresh 400 # 가장 오래된 재무 캐시 400개를 새로 받음 (매일 나눠서 갱신)
"""
import json, math, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_us import FIN_DIR, KST, ROOT, fetch_fin, fetch_profile, ttm  # noqa: E402

OUT = os.path.join(ROOT, "docs", "data_us2500.json")
UNIVERSE = os.path.join(ROOT, "data", "us2500.json")
TOP_N = 2500

# 그린블라트 원칙: 금융·유틸리티 제외 (리츠 포함). Yahoo 업종 기준
EXCL_SECT = {"Financial Services": "금융", "Utilities": "유틸리티", "Real Estate": "부동산·리츠"}
SECTOR_KO = {"Technology": "IT", "Healthcare": "헬스케어", "Industrials": "산업재", "Consumer Cyclical": "경기소비재",
             "Consumer Defensive": "필수소비재", "Basic Materials": "소재", "Communication Services": "커뮤니케이션",
             "Energy": "에너지", "Financial Services": "금융", "Utilities": "유틸리티", "Real Estate": "부동산"}
# 보통주가 아닌 증권 (워런트, 유닛, 우선주, 채권 등)
NOT_COMMON = re.compile(r"\b(Warrants?|Units?|Rights?|Preferred|Depositary|Notes? due|Debentures|Subordinated)\b", re.I)


def fetch_universe():
    """Nasdaq 종목 검색에서 미국 기업 보통주를 시가총액 순으로 2,500개 고름"""
    r = requests.get("https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true",
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=60).json()
    rows = []
    for x in r["data"]["rows"]:
        if x["country"] != "United States" or NOT_COMMON.search(x["name"]) or "^" in x["symbol"]:
            continue
        try:
            mcap = float(x["marketCap"] or 0)
        except ValueError:
            continue
        if mcap <= 0:
            continue
        # 같은 회사의 여러 주식 종류(GOOGL/GOOG, BRK/A·BRK/B 등)는 한 회사로 묶음
        stem = re.split(r"\s+(?:Class|Series) [A-Z]\b|\s*(?:Common|Capital)\s+Stock|\s*Ordinary\s+Shares", x["name"], flags=re.I)[0]
        stem = stem.strip().lower()
        rows.append(dict(ticker=x["symbol"].replace("/", "-"), name=x["name"], stem=stem, mcap=mcap,
                         vol=float(x["volume"] or 0)))
    rows.sort(key=lambda x: (-x["mcap"], -x["vol"]))
    seen, uni = {}, []
    for x in rows:
        if x["stem"] in seen:
            seen[x["stem"]]["tickers"].append(x["ticker"])
            continue
        name = re.split(r"\s+Class [A-Z]\b|\s*(?:Common|Capital)\s+Stock|\s*Ordinary\s+Shares", x["name"], flags=re.I)[0]
        name = re.sub(r"\s+", " ", name).strip()
        g = {"name": name, "tickers": [x["ticker"]], "mcap": x["mcap"]}
        seen[x["stem"]] = g
        uni.append(g)
        if len(uni) == TOP_N:
            break
    json.dump(uni, open(UNIVERSE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return uni


def cache_path(m):
    return os.path.join(FIN_DIR, f"{m['tickers'][0]}.json")


def load_fin(m, refresh):
    fn = cache_path(m)
    if not refresh and os.path.exists(fn):
        d = json.load(open(fn, encoding="utf-8"))
        if "err" not in d:
            if "ysector" not in d:  # S&P 500 캐시에서 넘어온 파일은 업종만 보충
                time.sleep(0.3)
                d.update(fetch_profile(yf.Ticker(m["tickers"][0])))
                json.dump(d, open(fn, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
            return d
    time.sleep(0.5)  # Yahoo 요청 제한 회피
    d = fetch_fin(m["tickers"])
    if "err" in d and os.path.exists(fn):
        return json.load(open(fn, encoding="utf-8"))
    json.dump(d, open(fn, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return d


def main():
    n_refresh = int(sys.argv[sys.argv.index("--refresh") + 1]) if "--refresh" in sys.argv else 0
    os.makedirs(FIN_DIR, exist_ok=True)
    try:
        uni = fetch_universe()
    except Exception as e:  # Nasdaq 접속 실패 시 지난 목록 사용
        print("universe fetch failed:", e)
        uni = json.load(open(UNIVERSE, encoding="utf-8"))

    # 재무 캐시가 오래된 순서로 n_refresh개만 새로 받음
    age = lambda m: (json.load(open(cache_path(m), encoding="utf-8")).get("fetched", "")
                     if os.path.exists(cache_path(m)) else "")
    stale = {m["tickers"][0] for m in sorted(uni, key=age)[:n_refresh]} if n_refresh else set()
    with ThreadPoolExecutor(2) as ex:
        fins = list(ex.map(lambda m: load_fin(m, m["tickers"][0] in stale), uni))

    ranked, excluded = [], []
    z = lambda v: v or 0.0
    for m, d in zip(uni, fins):
        mcap = m["mcap"]  # Nasdaq 시가총액 (회사 전체 기준)
        sect = d.get("ysector")
        base = [m["name"], "/".join(m["tickers"]), SECTOR_KO.get(sect, sect or "")]
        if sect in EXCL_SECT:
            excluded.append(base + [round(mcap / 1e9, 2), f"{EXCL_SECT[sect]} 업종"])
            continue
        if d.get("country") and d["country"] != "United States":
            excluded.append(base + [round(mcap / 1e9, 2), f"외국 기업 ({d['country']})"])
            continue
        bs = d.get("bs") or {}
        ebit, basis = ttm(d) if "err" not in d else (None, None)
        if "err" in d or ebit is None or bs.get("ca") is None or bs.get("cl") is None:
            excluded.append(base + [round(mcap / 1e9, 2), "재무데이터 없음"])
            continue
        nwc = max(0.0, (bs["ca"] - z(bs["cash"])) - (bs["cl"] - z(bs["stdebt"])))
        cap = nwc + z(bs["ppe"])
        ev = mcap + z(bs["preferred"]) + z(bs["debt"]) - z(bs["cash"]) + z(bs["minority"])
        ranked.append(dict(name=m["name"], tick=base[1], sector=base[2], mcap=mcap, ebit=ebit, capital=cap, ev=ev,
                           basis=basis, bs=bs["date"],
                           roc=ebit / cap if cap > 0 else (math.inf if ebit > 0 else -math.inf),
                           ey=ebit / ev if ev > 0 else (math.inf if ebit > 0 else -math.inf)))
    for k in ("roc", "ey"):
        for i, r in enumerate(sorted(ranked, key=lambda r: -r[k])):
            r[k + "_rank"] = i + 1
    ranked.sort(key=lambda r: (r["roc_rank"] + r["ey_rank"], r["ey_rank"]))
    pct = lambda v: (round(v * 100, 1) if math.isfinite(v) else ("inf" if v > 0 else "-inf"))
    b = lambda v: round(v / 1e9, 2)
    rows = [[i + 1, r["name"], r["tick"], r["sector"], b(r["mcap"]), b(r["ebit"]), b(r["capital"]), pct(r["roc"]),
             r["roc_rank"], b(r["ev"]), pct(r["ey"]), r["ey_rank"], r["roc_rank"] + r["ey_rank"], r["basis"], ""]
            for i, r in enumerate(ranked)]
    out = {"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
           "markets": {"US2500": {"total": len(uni), "min_mcap": round(min(m["mcap"] for m in uni) / 1e9, 2),
                                  "r": rows, "x": excluded,
                                  "bs": max((r["bs"] for r in ranked), default=None)}}}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("universe", len(uni), "ranked", len(rows), "excluded", len(excluded))


if __name__ == "__main__":
    main()
