#!/usr/bin/env python3
"""경로 (b) 검증: Crunchbase 데이터셋이 discover(이름·키워드 기반 발견)를 지원하는가.

BD Tech팀이 제시한 "Discover api(serp rerank based on intent) => crunchbase api"가
Scraper API의 discover 모드(`type=discover_new` + `discover_by=`)인지 직접 확인한다.
문서 조사 대신 API에 물어본다. 미지원이면 에러 메시지가 지원 목록을 알려주는 경우가 많다.

출력은 의도적으로 짧다. 응답 본문은 200자까지만 본다.
"""
import json
import os
import urllib.error
import urllib.request

BASE = "https://api.brightdata.com/datasets/v3"
DATASET = "gd_l1vijqt9jfj7olije"  # Crunchbase companies information
TOKEN = os.environ.get("BRIGHTDATA_API_KEY") or os.environ.get("BRIGHTDATA_API_TOKEN")

# (설명, 쿼리스트링, 바디)
CASES = [
    ("discover_by=keyword", f"?dataset_id={DATASET}&type=discover_new&discover_by=keyword",
     [{"keyword": "AcmeCorp"}]),
    ("discover_by=name", f"?dataset_id={DATASET}&type=discover_new&discover_by=name",
     [{"name": "AcmeCorp"}]),
    ("discover_by=company_name", f"?dataset_id={DATASET}&type=discover_new&discover_by=company_name",
     [{"company_name": "AcmeCorp"}]),
    ("discover_by=url(도메인)", f"?dataset_id={DATASET}&type=discover_new&discover_by=url",
     [{"url": "acmecorp.com"}]),
    ("type=discover_new만", f"?dataset_id={DATASET}&type=discover_new",
     [{"keyword": "AcmeCorp"}]),
]


def post(qs: str, body: list) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{BASE}/trigger{qs}", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def main() -> int:
    if not TOKEN:
        print("BRIGHTDATA_API_KEY 미설정")
        return 2
    print(f"[discover] dataset={DATASET}")
    supported = []
    for label, qs, body in CASES:
        code, text = post(qs, body)
        flat = " ".join(text.split())[:190]
        print(f"  {label:26s} HTTP {code:>4}  {flat}")
        if code in (200, 202):
            supported.append(label)
    print()
    if supported:
        print(f"[discover] 지원 확인: {supported}")
    else:
        print("[discover] 어떤 discover 조합도 수락되지 않음 → 이 데이터셋은 collect-by-url 전용")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
