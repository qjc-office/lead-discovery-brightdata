#!/usr/bin/env python3
"""기존 고객 이력에서 ICP(이상적 고객 프로필)를 도출한다.

누구에게 팔지부터 정하는 단계다. 이미 거래한 고객을 업종과 서비스 유형으로
집계해, 다음 고객이 가질 법한 특징을 뽑아낸다.

입력: 고객 목록 JSON (기본 경로는 --index 로 바꾼다)
      기대 형식은 README 의 "고객 목록 형식" 절 참고
출력: results/icp_profile.json, results/icp-evidence.md

산출물에는 고객 실명, 담당자, 거래액을 넣지 않는다. 업종과 서비스 유형의 집계만 남긴다.
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "_shared"))

from common import read_json, write_json, stamp  # noqa: E402
from taxonomy import classify_industry  # noqa: E402

DEFAULT_INDEX = Path(os.environ.get("ICP_CUSTOMER_INDEX", "data/customers.json"))

SERVICE_RULES = [
    ("교육/강의", r"(교육|강의|캠프|클래스|커리큘럼|출강|강연|마스터클래스|교육자료|VOD|워크샵|세미나|타운홀)"),
    ("자동화 구축", r"(자동화|automation|봇|모니터링|SaaS|시스템|개발|입찰|변환|크롤|수집|배포)"),
    ("AX 컨설팅", r"(컨설팅|AX|코칭|1:1|프롬프트|코드\s?리뷰|진단|전환)"),
    ("콘텐츠/디자인", r"(상세\s?페이지|디자인|콘텐츠|홍보|PPT|썸네일|카드뉴스|영상)"),
]

ACTIVE_HINT = re.compile(r"(진행|협상|협의|견적|제안|검토|완료|수금|정산|마무리|신규|후속|초기)")


def classify_service(text: str) -> str:
    for label, pattern in SERVICE_RULES:
        if re.search(pattern, text or "", re.IGNORECASE):
            return label
    return "미분류"


def build(index_path: Path) -> dict:
    data = read_json(index_path)
    if not data:
        raise SystemExit(f"고객 인덱스를 읽지 못했습니다: {index_path}")
    customers = data.get("customers") or {}

    industries = collections.Counter()
    services = collections.Counter()
    active_industries = collections.Counter()
    total = 0
    active = 0

    for name, rec in customers.items():
        total += 1
        blob = " ".join(
            str(rec.get(k, "")) for k in ("업종", "서비스", "메모", "최근활동")
        )
        # 회사명 자체에도 업종 힌트가 들어 있으나, 실명은 집계에만 쓰고 저장하지 않는다.
        bucket = classify_industry(blob + " " + str(name))
        industries[bucket] += 1
        services[classify_service(blob)] += 1
        if ACTIVE_HINT.search(str(rec.get("상태", ""))):
            active += 1
            active_industries[bucket] += 1

    def pct(c: collections.Counter, base: int) -> list[dict]:
        return [
            {"bucket": k, "count": v, "share_pct": round(v * 100 / base, 1)}
            for k, v in c.most_common()
        ]

    top_buckets = [row["bucket"] for row in pct(industries, total)[:6]
                   if row["bucket"] != "미분류"]

    return {
        "generated_at": stamp(),
        "source": "customer index (집계만 사용, 실명 및 거래액 미포함)",
        "customer_total": total,
        "customer_active": active,
        "industry_distribution": pct(industries, total),
        "industry_distribution_active": pct(active_industries, max(active, 1)),
        "service_distribution": pct(services, total),
        "icp": {
            "core_industries": top_buckets,
            "hypothesis": (
                "AI가 본업이 아닌 회사가 AI 또는 데이터 직무를 채용하기 시작했다면, "
                "AI 전환 의지와 예산은 생겼지만 내부 역량은 아직 얇다는 뜻이다. "
                "이 구간이 QJC의 교육과 구축 수요가 가장 크게 잡히는 지점이다."
            ),
            "positive_signals": [
                "비테크 업종에서 AI 또는 데이터 직무를 채용",
                "공고에 업무 자동화, 워크플로우, 사내 시스템 요구가 포함",
                "주니어 또는 신입 허용 (사람으로 채우기 어려워 교육 수요가 생기는 구간)",
                "동시에 여러 공고를 열어 둔 조직 (확장기, 예산 배정 확인)",
                "국세청 기준 계속사업자 (실재 검증)",
            ],
            "negative_signals": [
                "AI 또는 LLM 자체가 제품인 회사 (내부 역량 충분)",
                "SI 및 외주 개발사 (경쟁 위치)",
                "시니어 연구직 단독 채용 (자체 R&D 조직 보유)",
            ],
            "owner_split_rule": (
                "규모 기준선을 넘으면 시니어 담당, 아래면 일반 담당으로 자동 배정한다. "
                "기준선과 담당자 이름은 score.py 상단 상수라 조직에 맞게 바꿔 쓴다. "
                "규모는 DART 공시대상 법인 등재 여부로 추정하며 확정값이 아니다."
            ),
        },
    }


def to_markdown(profile: dict) -> str:
    lines = [
        "# ICP 도출 근거",
        "",
        f"생성일 {profile['generated_at']}. 출처는 QJC 고객 인덱스 "
        f"{profile['customer_total']}건이며, 이 중 진행 신호가 있는 건은 {profile['customer_active']}건이다.",
        "고객 실명과 거래액은 쓰지 않았고 업종과 서비스 유형의 집계만 사용했다.",
        "",
        "## 업종 분포 (전체)",
        "",
        "| 업종 | 건수 | 비중 |",
        "|---|---:|---:|",
    ]
    for row in profile["industry_distribution"]:
        lines.append(f"| {row['bucket']} | {row['count']} | {row['share_pct']}% |")
    lines += ["", "## 서비스 유형 분포", "", "| 유형 | 건수 | 비중 |", "|---|---:|---:|"]
    for row in profile["service_distribution"]:
        lines.append(f"| {row['bucket']} | {row['count']} | {row['share_pct']}% |")
    icp = profile["icp"]
    lines += [
        "",
        "## 가설",
        "",
        icp["hypothesis"],
        "",
        "## 정신호",
        "",
    ]
    lines += [f"- {s}" for s in icp["positive_signals"]]
    lines += ["", "## 역신호", ""]
    lines += [f"- {s}" for s in icp["negative_signals"]]
    lines += ["", "## 담당 분기", "", icp["owner_split_rule"], ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="QJC 고객 이력에서 ICP 도출")
    ap.add_argument("--index", default=str(DEFAULT_INDEX))
    ap.add_argument("--out-dir", default=str(ROOT / "results"))
    args = ap.parse_args(argv)

    profile = build(Path(args.index))
    out = Path(args.out_dir)
    write_json(out / "icp_profile.json", profile)
    (out / "icp-evidence.md").write_text(to_markdown(profile), encoding="utf-8")

    print(f"[icp] 고객 {profile['customer_total']}건 분석, 진행 신호 {profile['customer_active']}건")
    print(f"[icp] 핵심 업종: {', '.join(profile['icp']['core_industries'])}")
    print(f"[icp] wrote {out/'icp_profile.json'}")
    print(f"[icp] wrote {out/'icp-evidence.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
