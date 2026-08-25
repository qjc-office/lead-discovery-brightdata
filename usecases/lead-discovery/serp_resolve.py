#!/usr/bin/env python3
"""경로 (a): SERP로 Crunchbase organization URL을 해석하고 도메인 대조로 판별한다.

배경. Crunchbase 데이터셋에는 이름 기반 조회가 없다(BD Tech팀 확인, 2026-08-15).
discover(keyword)는 철자 유사 검색이라 정답을 못 찾는다(DISCOVER_API_NOTES.md).
남은 경로는 SERP로 organization URL을 찾아 collect-by-url에 넣는 것이다.

판별 레이어. slug 추정의 문제는 동명 해외 기업 오탐이었다(적중 중 48%).
Crunchbase 레코드에는 `website` 필드가 있고 우리는 각 기업의 homepage를 알고 있으므로,
**도메인 일치 여부로 결정론적으로 걸러낸다.** LLM 없이 대부분이 정리된다.
도메인이 비어 있거나 서로 다르지만 한국 기업인 경우만 `needs_review`로 남긴다.

사용:
  python3 serp_resolve.py [샘플수]        # 기본 60
"""
import concurrent.futures as cf
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
LEADS = HERE / "results" / "leads_20260804.csv"
OUT_RESOLVE = HERE / "results" / "serp_resolve_v2.json"
OUT_ENRICH = HERE / "results" / "cb_enrich_v2.json"

DATASET = "gd_l1vijqt9jfj7olije"          # Crunchbase companies information
SERP_ZONE = os.environ.get("BD_SERP_ZONE", "serp_api1")
REQUEST_API = "https://api.brightdata.com/request"
DATASETS = "https://api.brightdata.com/datasets/v3"
ORG_RE = re.compile(r"crunchbase\.com/organization/([a-z0-9][a-z0-9\-]{1,60})", re.I)


def token() -> str:
    t = os.environ.get("BRIGHTDATA_API_KEY") or os.environ.get("BRIGHTDATA_API_TOKEN")
    if not t:
        raise SystemExit("BRIGHTDATA_API_KEY 미설정")
    return t


def call(url: str, payload: dict | list | None = None, timeout: int = 120) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET", headers={
        "Authorization": f"Bearer {token()}",
        **({"Content-Type": "application/json"} if data else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def host_of(url: str) -> str:
    return re.sub(r"^https?://", "", (url or "").strip()).split("/")[0].replace("www.", "").lower()


def serp_slugs(query: str) -> list[str]:
    """SERP 1회 호출로 crunchbase organization slug 후보를 뽑는다."""
    q = urllib.parse.quote(f"site:crunchbase.com/organization/ {query}")
    target = f"https://www.google.com/search?q={q}&num=10"
    code, body = call(REQUEST_API, {"zone": SERP_ZONE, "url": target, "format": "raw"})
    if code != 200:
        return []
    seen, out = set(), []
    for m in ORG_RE.finditer(body):
        s = m.group(1).lower()
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:5]


def collect(slugs: list[str]) -> list[dict]:
    """확정 slug들을 배치 1회로 수집한다."""
    if not slugs:
        return []
    inputs = [{"url": f"https://www.crunchbase.com/organization/{s}"} for s in slugs]
    code, body = call(f"{DATASETS}/trigger?dataset_id={DATASET}&include_errors=true", inputs)
    if code not in (200, 202):
        print(f"  trigger 실패 HTTP {code}: {body[:150]}", flush=True)
        return []
    snap = json.loads(body)["snapshot_id"]
    print(f"  snapshot={snap} 폴링…", flush=True)
    deadline = time.time() + 900
    while time.time() < deadline:
        _, pb = call(f"{DATASETS}/progress/{snap}")
        try:
            st = json.loads(pb).get("status")
        except json.JSONDecodeError:
            st = None
        if st == "ready":
            _, sb = call(f"{DATASETS}/snapshot/{snap}?format=json", timeout=180)
            try:
                d = json.loads(sb)
            except json.JSONDecodeError:
                d = [json.loads(l) for l in sb.splitlines() if l.strip()]
            return d if isinstance(d, list) else [d]
        if st in ("failed", "error"):
            return []
        time.sleep(5)
    return []


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    rows = [r for r in csv.DictReader(open(LEADS, encoding="utf-8-sig"))
            if (r.get("homepage") or "").strip()][:n]
    print(f"[serp] 샘플 {len(rows)}건 (homepage 보유 선두 {n})", flush=True)

    # SERP는 1건당 30~60초라 순차로 돌리면 178건에 3시간이 넘는다. 스레드로 병렬화한다.
    # 도메인으로 먼저 찾고(정확도 높음) 실패하면 회사명으로 재시도한다.
    def resolve_one(r: dict) -> dict | None:
        host = host_of(r["homepage"])
        cands = serp_slugs(host) or serp_slugs(r["company_name"])
        if not cands:
            return None
        return {"company": r["company_name"], "host": host, "slugs": cands}

    resolved, done = [], 0
    with cf.ThreadPoolExecutor(max_workers=int(os.environ.get("SERP_WORKERS", "6"))) as ex:
        futures = {ex.submit(resolve_one, r): r for r in rows}
        for fut in cf.as_completed(futures):
            done += 1
            try:
                item = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"  해석 예외: {type(e).__name__}", flush=True)
                item = None
            if item:
                resolved.append(item)
            if done % 10 == 0 or done == len(rows):
                print(f"  … {done}/{len(rows)} 해석 {len(resolved)}건", flush=True)
                OUT_RESOLVE.write_text(json.dumps(resolved, ensure_ascii=False, indent=2))
    OUT_RESOLVE.write_text(json.dumps(resolved, ensure_ascii=False, indent=2))
    print(f"[serp] 해석 {len(resolved)}/{len(rows)} ({len(resolved)/len(rows)*100:.0f}%)", flush=True)

    # 후보 1순위를 모아 한 번에 수집
    first = [r["slugs"][0] for r in resolved]
    print(f"[collect] {len(first)}건 배치 수집", flush=True)
    recs = collect(first)

    # ⚠️ 수집 API는 입력 순서를 보장하지 않는다(실측: A사 자리에 B사 레코드가 왔다).
    # 위치로 짝지으면 안 되고, 레코드가 스스로 들고 있는 website 도메인으로 역매칭해야 한다.
    # 이 역매칭이 곧 판별 레이어다. 도메인이 우리 리드와 일치하면 그 회사가 맞다.
    lead_by_host = {r["host"]: r for r in resolved}
    confirmed, rejected, review = [], [], []
    matched_hosts = set()
    for rec in recs:
        if not isinstance(rec, dict) or not rec.get("name"):
            continue
        cb_host = host_of(rec.get("website") or "")
        kr = (rec.get("country_code") or "") in ("South Korea", "KR", "KOR")
        lead = lead_by_host.get(cb_host)
        entry = {"company": lead["company"] if lead else None, "host": cb_host,
                 "cb_name": rec.get("name"), "cb_host": cb_host,
                 "country": rec.get("country_code"),
                 "num_employees": rec.get("num_employees"),
                 "industries": bool(rec.get("industries"))}
        if lead:
            matched_hosts.add(cb_host)
            confirmed.append(entry)
        elif kr:
            review.append(entry)          # 한국 기업인데 도메인이 다름 → 사람이 볼 것
        else:
            rejected.append(entry)        # 동명 해외 기업 오탐

    for h, lead in lead_by_host.items():
        if h not in matched_hosts:
            review.append({"company": lead["company"], "host": h, "cb_name": None,
                           "cb_host": None, "country": None,
                           "num_employees": None, "industries": False})

    summary = {
        "sample": len(rows), "serp_resolved": len(resolved), "records": len(recs),
        "confirmed": len(confirmed), "rejected_by_domain": len(rejected), "needs_review": len(review),
        "with_employees": sum(1 for e in confirmed if e["num_employees"]),
        "with_industry": sum(1 for e in confirmed if e["industries"]),
        "confirmed_rows": confirmed, "rejected_rows": rejected[:20], "review_rows": review[:20],
    }
    OUT_ENRICH.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print("\n[결과]", flush=True)
    print(f"  SERP 해석      {len(resolved)}/{len(rows)}", flush=True)
    print(f"  레코드 반환    {len(recs)}", flush=True)
    print(f"  도메인 확정    {len(confirmed)}  ← 사용 가능", flush=True)
    print(f"  도메인 불일치로 제거 {len(rejected)}", flush=True)
    print(f"  사람 확인 필요 {len(review)}", flush=True)
    print(f"  임직원수 {summary['with_employees']} / 업종 {summary['with_industry']}", flush=True)
    print(f"  사용가능률 {len(confirmed)}/{len(rows)} = {len(confirmed)/len(rows)*100:.0f}%", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
