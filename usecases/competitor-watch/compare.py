#!/usr/bin/env python3
"""수집 CSV 를 QJC 상품과 대조해 results/comparison.md 를 만든다.

  python3 compare.py                       # results/ 의 최신 CSV 사용
  python3 compare.py --csv results/x.csv

시사점 문장의 숫자는 전부 CSV 에서 계산한다. real 행만 통계에 넣고
mock 행은 별도 표로 분리한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def log_line(msg: str, log_path: Path) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%SZ')} {msg}"
    print(line, flush=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def latest_csv(out_dir: Path) -> Path | None:
    files = sorted(out_dir.glob("competitors_*.csv"))
    return files[-1] if files else None


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def won(value) -> int | None:
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else None


def median(values: list[int]) -> int:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if not ordered:
        return 0
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) // 2


def money(value) -> str:
    n = won(value)
    return f"{n:,}원" if n is not None else "-"


def percentile_rank(values: list[int], target: int) -> int:
    """target 보다 싼 경쟁 프로그램의 비율(%)."""
    if not values:
        return 0
    return round(100 * sum(1 for v in values if v < target) / len(values))


def segment(row: dict) -> str:
    """가격 비교를 위한 3분류: 오프라인 / 온라인 기수제 / 온라인 상시."""
    fmt = row.get("format", "")
    status = row.get("cohort_status", "")
    if "오프라인" in fmt:
        return "오프라인"
    if any(k in status for k in ("모집", "기수", "예약")):
        return "온라인 기수제"
    return "온라인 상시"


def cell(value) -> str:
    """표 셀 안의 파이프는 칼럼을 깨뜨리므로 이스케이프한다."""
    return str(value).replace("|", "\\|").strip()


def table(rows: list[dict], headers: list[str], keys) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(cell(fn(row)) for fn in keys) + " |")
    return out


def competitor_table(rows: list[dict]) -> list[str]:
    ordered = sorted(rows, key=lambda r: won(r["price_krw"]) or -1, reverse=True)
    return table(
        ordered,
        ["브랜드", "프로그램", "판매가", "정가", "기간", "형태", "모집상태", "키워드"],
        [lambda r: r["brand"],
         lambda r: r["program_name"][:52],
         lambda r: money(r["price_krw"]),
         lambda r: money(r["list_price_krw"]),
         lambda r: r["duration"] or "-",
         lambda r: r["format"] or "-",
         lambda r: r["cohort_status"] or "-",
         lambda r: (r["keywords"] or "-")[:40]])


def qjc_table(products: list[dict]) -> list[str]:
    return table(
        products,
        ["프로그램", "가격", "얼리버드/정가", "기간", "형태", "모집상태"],
        [lambda p: p["program_name"],
         lambda p: money(p.get("price_krw")),
         lambda p: money(p.get("early_price_krw") or p.get("list_price_krw")),
         lambda p: p.get("duration", "-"),
         lambda p: p.get("format", "-"),
         lambda p: p.get("cohort_status", "-")])


def keyword_counts(rows: list[dict], watch: list[str]) -> list[tuple[str, int]]:
    counts = {}
    for row in rows:
        blob = f'{row["program_name"]} {row["keywords"]}'.lower()
        for kw in watch:
            if kw.lower() in blob:
                counts[kw] = counts.get(kw, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def paid_prices(rows: list[dict]) -> list[int]:
    return [p for p in (won(r["price_krw"]) for r in rows) if p]


def insight_price_band(real: list[dict], qjc: list[dict]) -> str:
    captured = [p for p in (won(r["price_krw"]) for r in real) if p is not None]
    prices = [p for p in captured if p]
    if not prices:
        return ""
    # 기준 상품은 가장 비싼 자사 상품으로 잡는다. 상품 구성을 바꿔도 깨지지 않는다.
    anchor = max((p for p in qjc if p.get("price_krw")),
                 key=lambda p: p["price_krw"], default=None)
    band = (f"가격을 읽어낸 {len(captured)}건 가운데 무료 {len(captured) - len(prices)}건을 빼면 "
            f"유료 {len(prices)}건의 가격은 {min(prices):,}원에서 {max(prices):,}원 사이, "
            f"중앙값 {median(prices):,}원입니다. ")
    if anchor:
        rank = percentile_rank(prices, anchor["price_krw"])
        band += (f"{anchor['program_name']} 정가 {anchor['price_krw']:,}원은 "
                 f"이 중 {rank}%보다 비싼 위치에 있습니다. ")
    return band + "저가 VOD 가 표본에 많이 섞여 있어 중앙값 자체보다는 아래 형태별 비교가 실제 경쟁 위치에 가깝습니다."


def insight_by_segment(real: list[dict], qjc: list[dict]) -> str:
    parts = []
    for name in ("온라인 상시", "온라인 기수제", "오프라인"):
        prices = paid_prices([r for r in real if segment(r) == name])
        if prices:
            parts.append(f"{name} {len(prices)}건은 {min(prices):,}~{max(prices):,}원"
                         f"(중앙값 {median(prices):,}원)")
    if not parts:
        return ""
    head = f"형태별로 나눠 보면 {', '.join(parts)}입니다. "
    vod = [p["price_krw"] for p in qjc if "VOD" in p["program_name"] and p.get("price_krw")]
    if vod:
        head += (f"자사 VOD 라인은 {min(vod):,}~{max(vod):,}원으로 "
                 "이 표본의 온라인 구간 안에 들어가 있고, ")
    return head + "라이브·오프라인 기수제 상품은 표본에서 같은 형태의 사례가 적어 직접 비교가 어렵습니다."


def insight_discount(real: list[dict]) -> str:
    """무료·전액지원 특강은 할인율을 왜곡하므로 제외하고 계산한다."""
    paid = [r for r in real if "무료" not in r["program_name"] and won(r["price_krw"])]
    pairs = [(won(r["price_krw"]), won(r["list_price_krw"])) for r in paid]
    discounted = [(s, l) for s, l in pairs if s and l and l > s]
    if not discounted:
        return ""
    rates = sorted(round(100 * (l - s) / l) for s, l in discounted)
    free = len([r for r in real if won(r["price_krw"]) == 0 or "무료" in r["program_name"]])
    unpriced = len([r for r in real if won(r["price_krw"]) is None])
    return (f"정가와 판매가가 함께 노출된 유료 {len(discounted)}건의 할인율은 중앙값 {median(rates)}%, "
            f"범위 {rates[0]}~{rates[-1]}%입니다(무료·전액지원 {free}건과 가격 미노출 {unpriced}건 제외). "
            "정가를 걸어 두고 상시 할인가로 파는 방식이 표본의 기본값이라, "
            "QJC 가 정가 하나만 노출하면 같은 화면에서 더 비싸 보일 수 있습니다.")


def insight_keywords(real: list[dict], watch: list[str], qjc: list[dict]) -> str:
    counts = keyword_counts(real, watch)
    if not counts:
        return ""
    top = ", ".join(f"{k}({v}건)" for k, v in counts[:6])
    qjc_blob = " ".join(p["program_name"] for p in qjc).lower()
    gaps = [k for k, v in counts if v >= 2 and k.lower() not in qjc_blob][:5]
    tail = (f" 반대로 {'·'.join(gaps)} 같은 키워드는 표본에서 반복 등장하는데 QJC 상품명에는 "
            "드러나지 않습니다. 커리큘럼에 있으면 명칭에 노출할 후보이고, 없으면 공백입니다.") if gaps else ""
    return f"프로그램명·태그에서 가장 자주 등장한 키워드는 {top}입니다.{tail}"


def insight_cohort(real: list[dict]) -> str:
    counts = {}
    for row in real:
        status = (row["cohort_status"] or "").strip()
        if status:
            counts[status] = counts.get(status, 0) + 1
    cohort_like = {k: v for k, v in counts.items()
                   if any(t in k for t in ("모집", "마감", "예약", "기"))}
    if not cohort_like:
        return ""
    listed = ", ".join(f"{k} {v}건" for k, v in sorted(cohort_like.items(), key=lambda kv: -kv[1]))
    always = sum(v for k, v in counts.items() if "상시" in k)
    return (f"모집상태가 기수 단위로 찍힌 프로그램은 {listed}이고, 상시 판매 표기는 {always}건입니다. "
            "상시형이 표본의 다수라 기수제는 소수인데, 이 값을 매일 같은 시각에 찍어 두면 "
            "경쟁사가 언제 기수를 열고 닫는지 추적할 수 있습니다.")


def build_markdown(rows: list[dict], cfg: dict, csv_path: Path) -> str:
    real = [r for r in rows if r["data_origin"] == "real"]
    other = [r for r in rows if r["data_origin"] != "real"]
    by_brand = {}
    for row in real:
        by_brand[row["brand"]] = by_brand.get(row["brand"], 0) + 1
    priced = [r for r in real if won(r["price_krw"]) is not None]

    lines = ["# AI 교육·부트캠프 경쟁 프로그램 비교", "",
             f"기준 파일: `{csv_path.name}`",
             f"생성 시각: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
             f"수집 결과: 실제 수집 {len(real)}행 / 브랜드 {len(by_brand)}개 / "
             f"가격 확보 {len(priced)}행, 합성(mock) {len(other)}행", "",
             "브랜드별 수집 건수: " + ", ".join(f"{b} {c}건" for b, c in by_brand.items()), "",
             "## QJC 상품 (비교 기준)", ""]
    lines += qjc_table(cfg["qjc_products"])
    lines += ["", "## 경쟁 프로그램 (실제 수집분)", ""]
    lines += competitor_table(real)
    if other:
        lines += ["", "## 합성 레코드 (Bright Data mock, 실제 값 아님)", ""]
        lines += competitor_table(other)

    lines += ["", "## 포지셔닝 시사점", ""]
    insights = [insight_price_band(real, cfg["qjc_products"]),
                insight_by_segment(real, cfg["qjc_products"]),
                insight_discount(real),
                insight_keywords(real, cfg["watch_keywords"], cfg["qjc_products"]),
                insight_cohort(real)]
    for i, text in enumerate([t for t in insights if t], start=1):
        lines += [f"{i}. {text}", ""]

    lines += ["## 이 표를 읽을 때 주의할 점", "",
              f"- 표본은 이번 실행에서 접근 가능했던 {len(by_brand)}개 브랜드 {len(real)}건뿐입니다. "
              "시장 전체 분포가 아닙니다.",
              "- 가격이 비어 있는 행은 페이지에 금액이 없던 경우입니다. 추정치를 넣지 않았습니다.",
              "- robots.txt 가 금지한 소스와 렌더링이 필요한 소스는 `blocked-sources.md` 에 따로 적었습니다.",
              "- 같은 브랜드라도 국비지원 여부·기수·쿠폰에 따라 실제 결제가는 달라집니다.",
              "- 일부 사이트는 상품 메타태그가 다른 상품 것으로 남아 있습니다. 상품명과 메타 제목이 "
              "어긋나면 그 메타 키워드는 쓰지 않고 프로그램명에서 키워드를 뽑았습니다.", ""]
    return "\n".join(lines)


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="경쟁사 비교표 생성")
    ap.add_argument("--csv")
    ap.add_argument("--out-dir", default=str(ROOT / "results"))
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    log_path = out_dir / "run-log.txt"
    csv_path = Path(args.csv) if args.csv else latest_csv(out_dir)
    if not csv_path or not csv_path.exists():
        log_line("[compare] 수집 CSV 가 없습니다. collect.py 를 먼저 실행하세요.", log_path)
        return 1

    cfg = json.loads((ROOT / "targets.json").read_text(encoding="utf-8"))
    rows = load_rows(csv_path)
    log_line(f"[compare] {csv_path.name} 에서 {len(rows)}행 읽음", log_path)
    md_path = out_dir / "comparison.md"
    md_path.write_text(build_markdown(rows, cfg, csv_path), encoding="utf-8")
    log_line(f"[compare] WROTE {md_path}", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
