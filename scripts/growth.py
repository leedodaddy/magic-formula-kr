"""영업이익 성장 점수 (마법공식 + 성장 반영 순위에 사용).

성장률 = 최근 4분기 영업이익 ÷ 약 3년 전 회계연도 영업이익 의 연평균 증가율.
"""
from datetime import date


def to_date(s):
    """'2026/06', '2026-06-30', '2025/12' → date (월말 기준은 중요하지 않아 1일로 둠)"""
    s = s.replace("/", "-")
    y, m = int(s[:4]), int(s[5:7])
    return date(y, m, 1)


def cagr(cur, cur_end, annual):
    """annual: {기간끝: 영업이익}. 3년에 가장 가까운(2년 이상) 과거 연도와 비교한 연평균 성장률.

    반환: (성장률 또는 None, 표시용 라벨)
    """
    if cur is None:
        return None, "자료부족"
    end = to_date(cur_end)
    best = None
    for k, v in annual.items():
        if v is None:
            continue
        yrs = (end - to_date(k)).days / 365.25
        if yrs >= 2 and (best is None or abs(yrs - 3) < abs(best[0] - 3)):
            best = (yrs, v, k)
    if best is None:
        return None, "자료부족"
    yrs, base, _ = best
    if cur <= 0:
        return -1.0, "적자"
    if base <= 0:
        return None, "흑자전환"
    return (cur / base) ** (1 / yrs) - 1, ""


def rank_growth(items):
    """items: [(성장률 또는 None, 라벨)] → 순위 리스트 (1이 가장 높은 성장).
    흑자전환·자료부족은 중간 순위, 적자는 맨 아래."""
    n = len(items)
    valid = sorted((g for g, lab in items if g is not None and lab == ""), reverse=True)
    pos = {}
    for i, g in enumerate(valid):
        pos.setdefault(g, i + 1)
    ranks = []
    for g, lab in items:
        if lab == "":
            ranks.append(pos[g])
        elif lab == "적자":
            ranks.append(n)
        else:
            ranks.append((n + 1) // 2)
    return ranks
