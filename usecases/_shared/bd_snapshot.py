#!/usr/bin/env python3
"""스냅샷 폴링·요약 공용 도구.

사용: python3 bd_snapshot.py <snapshot_id> [표시건수]
레코드 원문을 통째로 찍지 않고 핵심 필드만 요약한다(컨텍스트 보호).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.brightdata.com/datasets/v3"
KEYS = ("name", "num_employees", "country_code", "website", "permalink")


def get(path: str, timeout: int = 90) -> tuple[int, str]:
    token = os.environ.get("BRIGHTDATA_API_KEY") or os.environ.get("BRIGHTDATA_API_TOKEN")
    req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def fetch(snapshot_id: str, max_wait: int = 600) -> list:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        _, body = get(f"/progress/{snapshot_id}", timeout=30)
        try:
            st = json.loads(body).get("status", "?")
        except json.JSONDecodeError:
            st = body[:50]
        if st == "ready":
            _, sb = get(f"/snapshot/{snapshot_id}?format=json", timeout=120)
            try:
                d = json.loads(sb)
            except json.JSONDecodeError:
                d = [json.loads(l) for l in sb.splitlines() if l.strip()]
            return d if isinstance(d, list) else [d]
        if st in ("failed", "error"):
            print(f"실패: {body[:200]}")
            return []
        time.sleep(4)
    print("폴링 시간 초과")
    return []


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    show = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    recs = fetch(sys.argv[1])
    print(f"레코드 {len(recs)}건")
    for r in recs[:show]:
        if not isinstance(r, dict):
            continue
        print("  " + " | ".join(f"{k}={str(r.get(k))[:28]}" for k in KEYS if r.get(k)))
    kr = [r for r in recs if isinstance(r, dict)
          and (r.get("country_code") or "") in ("South Korea", "KR", "KOR")]
    print(f"한국 기업: {len(kr)}건 / 전체 {len(recs)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
