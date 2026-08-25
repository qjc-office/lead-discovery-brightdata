#!/usr/bin/env python3
"""보유 zone 목록 조회. SERP/Unlocker 경로는 zone 이름이 필요하다."""
import json
import os
import urllib.error
import urllib.request

for path in ("/zone/get_active_zones", "/status"):
    token = os.environ.get("BRIGHTDATA_API_KEY") or os.environ.get("BRIGHTDATA_API_TOKEN")
    req = urllib.request.Request(f"https://api.brightdata.com{path}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"{path} -> HTTP {r.status}")
            print(r.read().decode()[:600])
    except urllib.error.HTTPError as e:
        print(f"{path} -> HTTP {e.code}: {e.read().decode()[:200]}")
    print()
