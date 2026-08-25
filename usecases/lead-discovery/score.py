#!/usr/bin/env python3
"""보강된 회사 목록을 ICP 기준으로 채점해 우선순위가 붙은 리드 리스트를 만든다.

산출
  results/leads_YYYYMMDD.csv        점수와 근거가 함께 붙은 리드 전체
  results/top-leads.md              상위 리드별 접근 각도 메모
  results/scoring-summary.md        분포 요약과 데이터 출처 구분

점수는 0에서 100 사이로 정규화하고, 각 항목의 근거를 CSV 컬럼으로 함께 남긴다.
점수만 있고 근거가 없으면 영업 담당자가 쓸 수 없다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "_shared"))

from common import RunLog, read_json, write_csv, stamp  # noqa: E402

# 규모별 담당 분기. 조직마다 다르니 본인 기준으로 바꿔 쓰세요.
# 기준선 위아래로 다른 사람에게 붙인다는 뼈대만 예시로 남겨 둔 값입니다.
OWNER_SPLIT_HEADCOUNT = 50
OWNER_ABOVE = "담당 A"
OWNER_BELOW = "담당 B"
OWNER_UNKNOWN = "확인 후 배정"
from taxonomy import NON_TECH_BUCKETS, NEGATIVE_BUCKETS  # noqa: E402

# 배점. 신호가 있으면 기본 점수를 주고, 신호가 여러 개면 상한까지 가산한다.
# 단순 유무만 보면 "AI 디자이너 1명 뽑는 곳"과 "데이터 조직을 통째로 만드는 곳"이
# 같은 점수를 받아 상위 목록이 뒤집힌다.
W = {
    "ai_signal": (10, 4, 22),      # (기본, 신호당 가산, 상한)
    "automation": (8, 4, 18),
    "non_tech": 24,                # 비테크 업종에서의 AI 채용. 가장 강한 단일 신호
    "nondev_ai": 8,                # 비개발 직군에 AI 활용 요구. 임직원 교육 수요
    "junior_ok": 6,                # 주니어 허용. 사람으로 못 채우는 구간
    "multi_base": 3,               # 동시 다수 채용. 확장기이고 예산이 있다
    "multi_per": 2, "multi_cap": 16,
    "ai_tooling": 14,              # 공고에 Claude Code, Cursor 등 AI 코딩 도구 요구
    "verified": 6,                 # 국세청 계속사업자 확인
    "ai_native": -30,              # AI 자체가 본업
    "si_vendor": -22,              # SI 및 외주 개발사
    "research_only": -12,          # 시니어 연구직 중심
}

TIER_A, TIER_B = 60, 40


def _graded(spec: tuple[int, int, int], hits: list) -> int:
    base, per, cap = spec
    return min(cap, base + per * max(0, len(hits) - 1))


def score_one(c: dict) -> dict:
    pts: list[tuple[str, int, str]] = []
    ind = c.get("industry") or "미분류"

    ai = c.get("ai_hits") or []
    if ai:
        pts.append(("ai_signal", _graded(W["ai_signal"], ai),
                    f"AI 및 데이터 직무 신호 {len(ai)}종 " + ", ".join(ai[:4])))
    if ind in NON_TECH_BUCKETS and ai:
        pts.append(("non_tech", W["non_tech"], f"비테크 업종({ind})에서 AI 직무 채용"))
    auto = c.get("automation_hits") or []
    if auto:
        pts.append(("automation", _graded(W["automation"], auto),
                    "자동화 요구 " + ", ".join(auto[:4])))
    if c.get("nondev_ai_titles"):
        pts.append(("nondev_ai", W["nondev_ai"],
                    "비개발 직군 AI 요구 " + " / ".join(t for t in c["nondev_ai_titles"][:2] if t)))
    mc = c.get("min_career_min")
    if mc is not None and mc <= 2:
        pts.append(("junior_ok", W["junior_ok"], f"최소 경력 {mc}년 허용"))
    n = c.get("posting_count") or 0
    if n >= 2:
        pts.append(("multi_posting",
                    min(W["multi_cap"], W["multi_base"] + W["multi_per"] * (n - 1)),
                    f"동시 공고 {n}건"))
    tools = c.get("ai_tool_signals") or []
    if tools:
        pts.append(("ai_tooling", W["ai_tooling"],
                    "AI 코딩 도구 실무 도입 요구 " + ", ".join(sorted(set(tools))[:4])))
    if c.get("nts_status") == "계속사업자":
        pts.append(("verified", W["verified"],
                    f"국세청 계속사업자 확인 ({c.get('nts_tax_type','')})".strip()))

    if c.get("ai_native_evidence") or ind == "AI/ML 전문":
        ev = ", ".join(c.get("ai_native_evidence") or []) or ind
        pts.append(("ai_native", W["ai_native"], f"AI 자체가 본업 ({ev})"))
    if c.get("si_vendor_evidence") or ind == "SI/외주개발":
        ev = ", ".join(c.get("si_vendor_evidence") or []) or ind
        pts.append(("si_vendor", W["si_vendor"], f"SI 및 외주 개발사, 경쟁 위치 ({ev})"))
    if c.get("research_hits") and not c.get("automation_hits"):
        pts.append(("research_only", W["research_only"],
                    "시니어 연구직 중심 " + ", ".join(c["research_hits"][:2])))

    raw = sum(p[1] for p in pts)
    score = max(0, min(100, raw))
    tier = "A" if score >= TIER_A else ("B" if score >= TIER_B else "C")
    return {
        "score": score,
        "tier": tier,
        "score_breakdown": "; ".join(f"{k}{v:+d}" for k, v, _ in pts),
        "score_reasons": " | ".join(r for _, _, r in pts),
    }


def owner_and_size(c: dict) -> tuple[str, str]:
    """규모 추정과 담당 분기.

    공고 소개문에 임직원 수가 적혀 있으면 그 값을 쓰고, 없으면 DART 공시대상 법인
    등재 여부를 대리 지표로 쓴다. 어느 쪽도 확정 직원수는 아니다.
    """
    hc = c.get("headcount_stated")
    if isinstance(hc, int) and hc > 0:
        label = f"{hc}명 (회사 소개문 기재)"
        return label, (OWNER_ABOVE if hc >= OWNER_SPLIT_HEADCOUNT else OWNER_BELOW)
    listed = c.get("dart_listed")
    if listed is True:
        conf = c.get("dart_match_confidence") or ""
        if conf.startswith("낮음"):
            return "중견 이상 추정 (DART 매칭 신뢰도 낮음, 확인 필요)", OWNER_UNKNOWN
        return "중견 이상 추정 (DART 공시대상 법인)", OWNER_ABOVE
    if listed is False:
        return f"{OWNER_SPLIT_HEADCOUNT}인 미만 추정 (DART 미등재)", OWNER_BELOW
    return "확인 불가", OWNER_UNKNOWN


def offer_for(c: dict) -> str:
    """QJC 상품 중 무엇을 먼저 제안할지."""
    if c.get("ai_tool_signals"):
        return "AI 코딩 실무 교육 (도구는 정했고 쓸 사람이 없는 단계)"
    if c.get("automation_hits"):
        return "업무 자동화 구축 (사내 워크플로우 진단 후 구축)"
    if c.get("nondev_ai_titles"):
        return "임직원 AI 활용 교육 (기업 강의 또는 사내 부트캠프)"
    if c.get("industry") in NON_TECH_BUCKETS:
        return "AX 컨설팅 (AI 전환 로드맵 수립 후 파일럿)"
    return "AI 코딩 교육 (개발 조직 역량 강화)"


def size_note(c: dict) -> str:
    """규모 추정 근거를 한 줄로 남긴다."""
    bits = []
    if c.get("dart_listed") is True:
        bits.append("DART 공시대상 법인 등재")
    elif c.get("dart_listed") is False:
        bits.append("DART 미등재")
    if c.get("nts_tax_type"):
        bits.append(c["nts_tax_type"])
    if c.get("posting_count"):
        bits.append(f"동시 공고 {c['posting_count']}건")
    return " / ".join(bits)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="ICP 기반 리드 채점")
    ap.add_argument("--companies", default="")
    ap.add_argument("--out-dir", default=str(ROOT / "results"))
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    log = RunLog(out_dir / "run-log.txt")
    src = Path(args.companies) if args.companies else out_dir / f"companies_{stamp()}.json"
    rows = read_json(src, [])
    if not rows:
        log(f"[score] 회사 데이터 없음: {src}")
        log.close()
        return 3
    log(f"[score] 회사 {len(rows)}곳 채점 시작")

    leads = []
    for c in rows:
        s = score_one(c)
        size, owner = owner_and_size(c)
        leads.append({
            "rank": 0, "company_name": c["company_name"], "industry": c["industry"],
            "score": s["score"], "tier": s["tier"],
            "size_estimate": size, "size_evidence": size_note(c), "owner": owner,
            "recommended_offer": offer_for(c),
            "posting_count": c["posting_count"],
            "ai_signals": c["ai_hits"], "automation_signals": c["automation_hits"],
            "ai_tool_signals": c.get("ai_tool_signals") or [],
            "headcount_stated": c.get("headcount_stated"),
            "nondev_ai_roles": [t for t in c["nondev_ai_titles"] if t],
            "min_career_min": c["min_career_min"],
            "nts_status": c["nts_status"], "nts_tax_type": c["nts_tax_type"],
            "dart_listed": c["dart_listed"],
            "dart_match_confidence": c.get("dart_match_confidence", ""),
            "tech_stacks": c["tech_stacks"][:8],
            "locations": c["locations"],
            "sample_posting_title": (c["sample_postings"][0]["title"]
                                     if c["sample_postings"] else ""),
            "sample_posting_url": (c["sample_postings"][0]["url"]
                                   if c["sample_postings"] else ""),
            "homepage": c["homepage"],
            "collected_from": c["sources"],
            "score_breakdown": s["score_breakdown"],
            "score_reasons": s["score_reasons"],
            "data_origin": c["data_origin"],
            "enriched": c["enriched"],
        })

    leads.sort(key=lambda r: (-r["score"], -r["posting_count"], r["company_name"]))
    for i, r in enumerate(leads, 1):
        r["rank"] = i

    fields = list(leads[0].keys())
    csv_path = out_dir / f"leads_{stamp()}.csv"
    write_csv(csv_path, leads, fields)

    tiers = {t: sum(1 for r in leads if r["tier"] == t) for t in "ABC"}
    real = sum(1 for r in leads if r["data_origin"] == "real")
    log(f"[score] 리드 {len(leads)}건 -> {csv_path}")
    log(f"[score] 티어 분포 A {tiers['A']} / B {tiers['B']} / C {tiers['C']}")
    log(f"[score] 실데이터 {real}건, mock {len(leads)-real}건")

    (out_dir / "top-leads.md").write_text(top_markdown(leads, args.top), encoding="utf-8")
    (out_dir / "scoring-summary.md").write_text(summary_markdown(leads, tiers), encoding="utf-8")
    log(f"[score] wrote {out_dir/'top-leads.md'}, {out_dir/'scoring-summary.md'}")
    log.close()
    return 0


def top_markdown(leads: list[dict], n: int) -> str:
    out = ["# 상위 리드와 접근 각도", "",
           f"점수 상위 {n}건이다. 모두 공개 채용 공고에서 나온 실제 기업이며, "
           "점수 근거를 함께 적었다. 접촉 전에 담당자가 근거를 직접 확인하고 판단한다.", ""]
    for r in leads[:n]:
        if r["data_origin"] != "real":
            continue
        out += [
            f"## {r['rank']}. {r['company_name']} ({r['score']}점, {r['tier']}티어)",
            "",
            f"- 업종 {r['industry']} / 규모 {r['size_estimate']} / 담당 {r['owner']}",
            f"- 제안 상품: {r['recommended_offer']}",
            f"- 채용 중 공고 {r['posting_count']}건, 대표 공고 \"{r['sample_posting_title']}\"",
            f"- 점수 근거: {r['score_reasons']}",
        ]
        if r["sample_posting_url"]:
            out.append(f"- 공고 링크: {r['sample_posting_url']}")
        if r["homepage"]:
            out.append(f"- 홈페이지: {r['homepage']}")
        out.append("")
    return "\n".join(out)


def summary_markdown(leads: list[dict], tiers: dict) -> str:
    import collections
    ind = collections.Counter(r["industry"] for r in leads)
    own = collections.Counter(r["owner"] for r in leads)
    off = collections.Counter(r["recommended_offer"] for r in leads)
    real = sum(1 for r in leads if r["data_origin"] == "real")
    out = [
        "# 채점 요약", "",
        f"리드 {len(leads)}건. 이 중 실제 공개 데이터에서 나온 건이 {real}건, "
        f"Bright Data 픽스처에서 나온 mock이 {len(leads)-real}건이다.", "",
        "## 티어 분포", "",
        "| 티어 | 기준 | 건수 |", "|---|---|---:|",
        f"| A | 60점 이상, 즉시 접촉 | {tiers['A']} |",
        f"| B | 40점 이상, 2주 내 접촉 | {tiers['B']} |",
        f"| C | 40점 미만, 모니터링 | {tiers['C']} |",
        "", "## 업종 분포", "", "| 업종 | 건수 |", "|---|---:|",
    ]
    out += [f"| {k} | {v} |" for k, v in ind.most_common()]
    out += ["", "## 담당 분기 (규모 기준선)", "", "| 담당 | 건수 |", "|---|---:|"]
    out += [f"| {k} | {v} |" for k, v in own.most_common()]
    out += ["", "## 추천 상품 분포", "", "| 상품 | 건수 |", "|---|---:|"]
    out += [f"| {k} | {v} |" for k, v in off.most_common()]
    hc = sum(1 for r in leads if r.get("headcount_stated"))
    low = sum(1 for r in leads if str(r.get("dart_match_confidence", "")).startswith("낮음"))
    out += ["", "## 규모 추정에 관한 한계", "",
            f"임직원 수가 회사 소개문에 적혀 있던 곳은 {len(leads)}건 중 {hc}건이다. "
            "나머지는 DART 공시대상 법인 등재 여부로 추정했다. 등재되면 외부감사 대상 이상이라 "
            "조직이 큰 편이라고 볼 수 있으나 정확한 직원수는 아니다.", "",
            f"DART 대조는 상호명 문자열 매칭이라 짧은 상호는 동명 법인과 충돌할 수 있다. "
            f"신뢰도가 낮다고 표시한 건이 {low}건이고, 이 중 임직원 수가 따로 확인되지 않은 "
            f"{own.get('확인 후 배정', 0)}건은 담당을 자동 배정하지 않고 확인 후 배정으로 남겼다.", "",
            "국민연금 사업장 API로 실제 가입자수를 붙이려 했지만 키 인증은 통과하는데 "
            "조회 결과가 계속 0건이라 이번에는 쓰지 못했다.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
