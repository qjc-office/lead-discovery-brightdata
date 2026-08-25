#!/usr/bin/env python3
"""Bright Data 동기(sync) Scraper API 최소 검증.

BD Tech팀(2026-08-14) 안내대로 POST /datasets/v3/scrape 로
단일 URL을 동기 수집해 본다. 크레딧이 실제로 살아 있는지, 그리고 호출 한 번으로
구조화 JSON이 돌아오는지를 한 번에 확인하는 용도다.

사용: python3 bd_sync_probe.py [dataset_id] [url]
기본값은 Crunchbase companies + BD Tech팀이 준 예시 URL.

출력은 의도적으로 짧다. 전체 레코드를 찍지 않고 핵심 필드만 본다.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.brightdata.com/datasets/v3/scrape"
DEFAULT_DATASET = "gd_l1vijqt9jfj7olije"  # Crunchbase companies information
DEFAULT_URL = "https://www.crunchbase.com/organization/aisci"
INTERESTING = ("name", "legal_name", "num_employees", "industries", "categories",
               "location", "region", "country_code", "website", "founded_date")


def _get(url: str, token: str, timeout: int = 60) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def poll_snapshot(snapshot_id: str, token: str, max_wait: int = 300) -> list:
    """BD Tech팀 권장대로 2~3초 간격으로 진행 상태를 확인하고 완료되면 내려받는다."""
    base = "https://api.brightdata.com/datasets/v3"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        code, body = _get(f"{base}/progress/{snapshot_id}", token, timeout=30)
        try:
            status = json.loads(body).get("status", "?")
        except json.JSONDecodeError:
            status = f"parse_err:{body[:60]}"
        print(f"   polling… {status} ({int(time.time()-(deadline-max_wait))}s)")
        if status == "ready":
            code, body = _get(f"{base}/snapshot/{snapshot_id}?format=json", token, timeout=90)
            try:
                data = json.loads(body)
                return data if isinstance(data, list) else [data]
            except json.JSONDecodeError:
                out = []
                for line in body.splitlines():
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                return out
        if status in ("failed", "error"):
            print(f"   실패: {body[:200]}")
            return []
        time.sleep(3)
    print("   폴링 시간 초과")
    return []


def main() -> int:
    token = os.environ.get("BRIGHTDATA_API_KEY") or os.environ.get("BRIGHTDATA_API_TOKEN")
    if not token:
        print("BRIGHTDATA_API_KEY 미설정. set -a && source .env && set +a 후 실행하세요.")
        return 2

    dataset_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATASET
    target = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_URL

    body = json.dumps([{"url": target}]).encode()
    url = f"{API}?dataset_id={dataset_id}&include_errors=true"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })

    print(f"[probe] dataset={dataset_id}")
    print(f"[probe] url={target}")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        print(f"[probe] HTTP {e.code} ({time.time()-started:.1f}s)")
        print(f"[probe] body: {raw[:400]}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[probe] 실패: {type(e).__name__}: {e}")
        return 1

    elapsed = time.time() - started
    print(f"[probe] HTTP {status} / {elapsed:.1f}s / {len(raw)} bytes")

    # sync 엔드포인트는 NDJSON 또는 JSON 배열로 돌려준다. 둘 다 받아준다.
    records = []
    stripped = raw.strip()
    if stripped.startswith("["):
        try:
            records = json.loads(stripped)
        except json.JSONDecodeError:
            pass
    if not records:
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not records:
        print(f"[probe] 파싱 실패. 앞부분: {stripped[:300]}")
        return 1

    # sync 한도(약 60초)를 넘기면 202 + snapshot_id로 떨어진다. 그때는 폴링해서 받는다.
    if len(records) == 1 and isinstance(records[0], dict) and "snapshot_id" in records[0] \
            and "name" not in records[0]:
        snap = records[0]["snapshot_id"]
        print(f"[probe] 동기 한도 초과 → 스냅샷 폴링: {snap}")
        records = poll_snapshot(snap, token)
        if not records:
            return 1

    print(f"[probe] 레코드 {len(records)}건")
    rec = records[0]
    if isinstance(rec, dict):
        if rec.get("error") or rec.get("warning"):
            print(f"[probe] 오류 필드: {str(rec)[:300]}")
        hits = {k: rec.get(k) for k in INTERESTING if rec.get(k) not in (None, "", [])}
        print(f"[probe] 사용 가능 필드 {len(rec)}개 중 관심 필드:")
        for k, v in hits.items():
            print(f"   - {k}: {str(v)[:90]}")
        if not hits:
            print(f"   (관심 필드 없음) 키 목록: {list(rec)[:20]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
