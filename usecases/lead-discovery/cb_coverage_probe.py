#!/usr/bin/env python3
"""Crunchbase 커버리지 실측.

BD Tech팀(2026-08-14)이 권장한 `Crunchbase company information - collect by url`은
crunchbase organization URL을 입력으로 받는다. 우리에겐 slug가 없고 홈페이지 도메인만
있으므로, "도메인 SLD를 slug로 쓰면 몇 %가 맞는가"를 먼저 재본다. 이 수치가
enrichment 경로(직접 slug 추정 vs SERP 해석)를 가른다.

배치 trigger 1회로 N건을 한꺼번에 넣고 스냅샷을 폴링한다. 건당 호출하지 않는다.

사용: python3 cb_coverage_probe.py [샘플수]
"""
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://api.brightdata.com/datasets/v3"
DATASET = "gd_l1vijqt9jfj7olije"  # Crunchbase companies information
HERE = Path(__file__).parent
LEADS = HERE / "results" / "leads_20260804.csv"


def req(url: str, token: str, data: bytes | None = None, timeout: int = 90):
    r = urllib.request.Request(url, data=data, method="POST" if data else "GET", headers={
        "Authorization": f"Bearer {token}",
        **({"Content-Type": "application/json"} if data else {}),
    })
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def slug_from_homepage(homepage: str) -> str | None:
    host = re.sub(r"^https?://", "", homepage.strip()).split("/")[0].replace("www.", "")
    sld = host.split(".")[0]
    return sld if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,38}", sld) else None


def main() -> int:
    token = os.environ.get("BRIGHTDATA_API_KEY") or os.environ.get("BRIGHTDATA_API_TOKEN")
    if not token:
        print("BRIGHTDATA_API_KEY 미설정")
        return 2
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20

    rows = [r for r in csv.DictReader(open(LEADS, encoding="utf-8-sig"))
            if (r.get("homepage") or "").strip()]
    sample, seen = [], set()
    for r in rows:
        s = slug_from_homepage(r["homepage"])
        if s and s not in seen:
            seen.add(s)
            sample.append((r["company_name"], s))
        if len(sample) >= n:
            break

    print(f"[coverage] 샘플 {len(sample)}건 (도메인 SLD → slug 추정)")
    inputs = [{"url": f"https://www.crunchbase.com/organization/{s}"} for _, s in sample]

    status, body = req(f"{BASE}/trigger?dataset_id={DATASET}&include_errors=true",
                       token, json.dumps(inputs).encode())
    if status not in (200, 202):
        print(f"[coverage] trigger 실패 HTTP {status}: {body[:200]}")
        return 1
    snap = json.loads(body).get("snapshot_id")
    print(f"[coverage] snapshot={snap}")

    deadline = time.time() + 600
    while time.time() < deadline:
        _, pb = req(f"{BASE}/progress/{snap}", token, timeout=30)
        try:
            st = json.loads(pb).get("status", "?")
        except json.JSONDecodeError:
            st = pb[:40]
        if st == "ready":
            break
        if st in ("failed", "error"):
            print(f"[coverage] 실패: {pb[:200]}")
            return 1
        time.sleep(4)
    else:
        print("[coverage] 폴링 시간 초과")
        return 1

    _, sb = req(f"{BASE}/snapshot/{snap}?format=json", token, timeout=120)
    try:
        recs = json.loads(sb)
    except json.JSONDecodeError:
        recs = [json.loads(l) for l in sb.splitlines() if l.strip()]
    if isinstance(recs, dict):
        recs = [recs]

    hit = [r for r in recs if isinstance(r, dict) and r.get("name") and not r.get("error")]
    emp = [r for r in hit if r.get("num_employees")]
    ind = [r for r in hit if r.get("industries")]

    print(f"\n[coverage] === 결과 ===")
    print(f"  시도 {len(sample)}건 / 응답 {len(recs)}건")
    print(f"  Crunchbase 페이지 존재: {len(hit)}건 ({len(hit)/len(sample)*100:.0f}%)")
    print(f"  임직원수 확보: {len(emp)}건 / 업종 확보: {len(ind)}건")
    print("\n  적중 사례:")
    for r in hit[:8]:
        print(f"   - {str(r.get('name'))[:24]:26s} emp={str(r.get('num_employees'))[:10]:12s}"
              f" country={str(r.get('country_code'))[:14]}")
    miss = {s for _, s in sample} - {(r.get("permalink") or "") for r in hit}
    print(f"\n  미적중 slug 예: {sorted(miss)[:10]}")

    # 정밀도: slug가 우연히 동명 해외 기업을 잡는 오탐을 걸러야 한다.
    kr = [r for r in hit if (r.get("country_code") or "").strip() in ("South Korea", "KR", "KOR")]
    print(f"\n[coverage] === 정밀도 ===")
    print(f"  적중 {len(hit)}건 중 한국 기업: {len(kr)}건")
    print(f"  전체 대비 실사용 가능: {len(kr)}/{len(sample)} = {len(kr)/len(sample)*100:.0f}%")
    others = [(r.get("name"), r.get("country_code")) for r in hit if r not in kr]
    print(f"  해외로 잡힌 오탐: {others[:8]}")
    out = HERE / "results" / "cb_coverage_probe.json"
    out.write_text(json.dumps({
        "sample": len(sample), "hit": len(hit), "korea": len(kr),
        "records": [{k: r.get(k) for k in ("name", "num_employees", "country_code", "website")}
                    for r in hit],
    }, ensure_ascii=False, indent=2))
    print(f"  상세 저장: {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
