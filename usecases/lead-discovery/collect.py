#!/usr/bin/env python3
"""채용 공고를 공개 소스에서 수집해 하나의 정규화된 형태로 저장한다.

소스
  jumpit   점핏 공개 포지션 API. robots.txt에서 /positions 경로가 허용된다.
  wanted   원티드 공개 채용 API. robots.txt 요청이 CDN에서 403으로 끊긴다.
           RFC 9309 §2.3.1.3 은 이 경우(400~499)를 "unavailable" 로 보고 크롤러가
           접근해도 된다고 규정하지만, 규정이 허용한다고 마음껏 긁을 이유는 없어서
           요청 간격을 두고 목록 페이지만 최소로 가져온다.
  bd       Bright Data Web Scraper API 경로. 상위 레포 bd_client.py를 재사용한다.
           키가 없으면 mock 픽스처로 파이프라인만 검증한다.

사용
  python3 collect.py --source jumpit --max-pages 45
  python3 collect.py --source all
  python3 collect.py --source bd --mock
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "_shared"))
sys.path.insert(0, str(ROOT.parent.parent))  # 레포 루트의 bd_client.py 재사용

from common import Fetcher, RunLog, BROWSER_UA, robots_allows, write_json, stamp, now_iso  # noqa: E402

JUMPIT_LIST = "https://api.jumpit.co.kr/api/positions?sort=reg_dt&highlight=false&page={page}"
JUMPIT_ROBOTS_PROBE = "https://jumpit.saramin.co.kr/positions"
JUMPIT_VIEW = "https://jumpit.saramin.co.kr/position/{pid}"

WANTED_LIST = (
    "https://www.wanted.co.kr/api/chaos/navigation/v1/results"
    "?job_group_id={group}&country=kr&job_sort=job.latest_order"
    "&locations=all&years=-1&limit=100&offset={offset}"
)
WANTED_VIEW = "https://www.wanted.co.kr/wd/{pid}"
WANTED_GROUPS = {518: "개발", 507: "마케팅/광고", 511: "경영/비즈니스"}


def norm(**kw) -> dict:
    base = {
        "source": "", "posting_id": "", "title": "", "company_name": "",
        "company_ref": "", "tech_stacks": [], "min_career": None, "max_career": None,
        "locations": [], "job_category": "", "url": "", "collected_at": now_iso(),
        "data_origin": "real",
    }
    base.update(kw)
    return base


def collect_jumpit(fetch: Fetcher, log: RunLog, max_pages: int) -> list[dict]:
    allowed, reason = robots_allows(JUMPIT_ROBOTS_PROBE, ua=fetch.ua, log=log.write)
    if not allowed:
        log(f"[jumpit] 건너뜀: {reason}")
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        code, data = fetch.get_json(JUMPIT_LIST.format(page=page))
        if code != 200 or not isinstance(data, dict):
            log(f"[jumpit] page={page} HTTP {code} 중단")
            break
        result = data.get("result") or {}
        rows = result.get("positions") or []
        if page == 1:
            log(f"[jumpit] totalCount={result.get('totalCount')} 페이지당 {len(rows)}건")
        if not rows:
            log(f"[jumpit] page={page} 결과 없음, 종료")
            break
        new = 0
        for p in rows:
            pid = str(p.get("id"))
            if pid in seen:
                continue
            seen.add(pid)
            new += 1
            out.append(norm(
                source="jumpit", posting_id=pid, title=p.get("title") or "",
                company_name=(p.get("companyName") or "").strip(),
                tech_stacks=p.get("techStacks") or [],
                min_career=p.get("minCareer"), max_career=p.get("maxCareer"),
                locations=p.get("locations") or [],
                job_category=p.get("jobCategory") or "",
                url=JUMPIT_VIEW.format(pid=pid),
            ))
        log(f"[jumpit] page={page} 신규 {new}건 누적 {len(out)}건")
        if new == 0:
            break
    return out


def collect_wanted(fetch: Fetcher, log: RunLog, max_pages: int) -> list[dict]:
    allowed, reason = robots_allows("https://www.wanted.co.kr/wdlist", ua=fetch.ua, log=log.write)
    if not allowed:
        log(f"[wanted] robots 판정: {reason}. RFC 9309 §2.3.1.3 상 robots.txt 를 "
            f"못 읽는 경우(400~499)는 접근이 허용되지만, 스스로 상한을 두고 "
            f"목록 페이지만 최소로 요청한다.")
    out: list[dict] = []
    for group, label in WANTED_GROUPS.items():
        for page in range(max_pages):
            url = WANTED_LIST.format(group=group, offset=page * 100)
            code, data = fetch.get_json(url)
            if code != 200 or not isinstance(data, dict):
                log(f"[wanted] group={label} page={page} HTTP {code} 중단")
                break
            rows = data.get("data") or []
            if not rows:
                break
            for j in rows:
                comp = j.get("company") or {}
                addr = j.get("address") or {}
                out.append(norm(
                    source="wanted", posting_id=str(j.get("id")),
                    title=j.get("position") or "",
                    company_name=(comp.get("name") or "").strip(),
                    company_ref=str(comp.get("id") or ""),
                    min_career=j.get("annual_from"), max_career=j.get("annual_to"),
                    locations=[x for x in [addr.get("location"), addr.get("district")] if x],
                    job_category=label,
                    url=WANTED_VIEW.format(pid=j.get("id")),
                ))
            log(f"[wanted] group={label} page={page} {len(rows)}건 누적 {len(out)}건")
            if not (data.get("links") or {}).get("next"):
                break
    return out


def collect_bd(log: RunLog, use_mock: bool) -> list[dict]:
    """Bright Data 경로. 키가 없으면 mock 픽스처로 파이프라인만 확인한다."""
    try:
        from bd_client import BrightDataClient, BrightDataError
    except ImportError as exc:
        log(f"[bd] bd_client 임포트 실패: {exc}")
        return []
    cfg_path = ROOT.parent.parent / "scraper_config.json"
    import json
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    target = cfg["targets"]["public_job_postings"]
    log(f"[bd] dataset_id={target['dataset_id']} mode={'mock' if use_mock else 'live'}")
    try:
        client = BrightDataClient.from_env(
            mock=use_mock, mock_dir=ROOT.parent.parent / "mock",
            dataset_key=target["mock_key"], logger=log.write,
        )
        _, records = client.collect(
            dataset_id=target["dataset_id"], inputs=target["inputs"],
            params=target.get("trigger_params", {}), fmt="json", batch_size=None,
        )
    except BrightDataError as exc:
        log(f"[bd] 실패: {exc}")
        log("[bd] Bright Data 키가 없거나 만료되면 이 경로는 비어 있는 상태로 남는다.")
        return []
    out = []
    for r in records:
        out.append(norm(
            source="bd_public_jobs", posting_id=str(r.get("job_posting_id")),
            title=r.get("job_title") or "", company_name=(r.get("company_name") or "").strip(),
            locations=[r.get("job_location")] if r.get("job_location") else [],
            job_category=r.get("job_seniority_level") or "",
            url=r.get("url") or "",
            data_origin="mock" if use_mock else "real",
        ))
    log(f"[bd] {len(out)}건 (data_origin={'mock' if use_mock else 'real'})")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="채용 공고 수집")
    ap.add_argument("--source", default="all", choices=["all", "jumpit", "wanted", "bd"])
    ap.add_argument("--max-pages", type=int, default=45)
    ap.add_argument("--wanted-pages", type=int, default=3)
    ap.add_argument("--mock", action="store_true", help="Bright Data 경로를 픽스처로 대체")
    ap.add_argument("--out-dir", default=str(ROOT / "results"))
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    log = RunLog(out_dir / "run-log.txt")
    log(f"[collect] source={args.source} max_pages={args.max_pages}")
    fetch = Fetcher(min_interval=1.2)
    browser = Fetcher(min_interval=1.5, ua=BROWSER_UA)

    rows: list[dict] = []
    if args.source in ("all", "jumpit"):
        rows += collect_jumpit(fetch, log, args.max_pages)
    if args.source in ("all", "wanted"):
        rows += collect_wanted(browser, log, args.wanted_pages)
    if args.source in ("all", "bd"):
        rows += collect_bd(log, use_mock=args.mock or args.source == "all")

    path = out_dir / f"raw_postings_{stamp()}.json"
    write_json(path, rows)
    by_src: dict[str, int] = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    log(f"[collect] 총 {len(rows)}건 저장 -> {path}")
    log(f"[collect] 소스별 {by_src}")
    log(f"[collect] 실데이터 {sum(1 for r in rows if r['data_origin']=='real')}건, "
        f"mock {sum(1 for r in rows if r['data_origin']=='mock')}건")
    log.close()
    return 0 if rows else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
