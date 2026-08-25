#!/usr/bin/env bash
# Level 1: the smallest possible Bright Data Web Scraper API round trip.
# Three calls: trigger, poll, download. Needs BRIGHTDATA_API_KEY.
#
#   export BRIGHTDATA_API_KEY=...        # https://brightdata.com/cp/setting/users
#   ./level1_curl.sh [company-slug]
#
# 규모를 모르는 회사 한 곳의 공개 기업 페이지를 curl 한 번으로 요청해,
# 회사명·업종·임직원 규모가 정리된 JSON을 그대로 받는다.
# 파싱 코드를 짜는 게 아니라 필요한 값을 바로 받는다는 것이 요점이다.
#
# Docs
#   trigger  https://docs.brightdata.com/api-reference/rest-api/scraper/asynchronous-requests
#   progress https://docs.brightdata.com/api-reference/scrapers/management-apis/monitor-progress
#   download https://docs.brightdata.com/api-reference/scrapers/delivery-apis/download-snapshot

set -euo pipefail

: "${BRIGHTDATA_API_KEY:?set BRIGHTDATA_API_KEY first}"

API="https://api.brightdata.com"
# 공개 기업 정보 페이지 "Crunchbase companies information" (collect by url)
# https://docs.brightdata.com/api-reference/web-scraper-api/overview
DATASET_ID="gd_l1vijqt9jfj7olije"

SLUG="${1:-airbnb}"
TARGET_URL="https://www.crunchbase.com/organization/${SLUG}"

echo "1) trigger  ${TARGET_URL}"
SNAPSHOT_ID=$(curl -sS -X POST \
  "${API}/datasets/v3/trigger?dataset_id=${DATASET_ID}&include_errors=true" \
  -H "Authorization: Bearer ${BRIGHTDATA_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "[{\"url\":\"${TARGET_URL}\"}]" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["snapshot_id"])')

echo "   snapshot_id=${SNAPSHOT_ID}"

echo "2) poll until ready"
for _ in $(seq 1 60); do
  STATUS=$(curl -sS "${API}/datasets/v3/progress/${SNAPSHOT_ID}" \
    -H "Authorization: Bearer ${BRIGHTDATA_API_KEY}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","unknown"))')
  echo "   status=${STATUS}"
  [ "$STATUS" = "ready" ] && break
  [ "$STATUS" = "failed" ] && { echo "   collection failed"; exit 1; }
  sleep 5
done

echo "3) download"
curl -sS "${API}/datasets/v3/snapshot/${SNAPSHOT_ID}?format=json" \
  -H "Authorization: Bearer ${BRIGHTDATA_API_KEY}" \
  -o level1_result.json

python3 - <<'PY'
import json

rows = json.load(open("level1_result.json"))
if isinstance(rows, dict):
    rows = [rows]
print(f"   records: {len(rows)}")

# 화면에서 보여줄 값. 응답 스키마가 바뀌어도 죽지 않게 후보 키를 순서대로 찾는다.
WANTED = [
    ("회사명",     ["name", "company_name", "legal_name", "title"]),
    ("업종",       ["industries", "industry", "categories", "category_groups"]),
    ("임직원 규모", ["num_employees", "employees", "company_size", "num_employees_enum"]),
    ("홈페이지",   ["website", "homepage", "url", "domain"]),
    ("소재지",     ["headquarters", "location", "country_code", "region"]),
]

for row in rows[:1]:
    for label, keys in WANTED:
        for k in keys:
            if k in row and row[k] not in (None, "", [], {}):
                v = row[k]
                if isinstance(v, list):
                    def flat(x):
                        if isinstance(x, dict):
                            return x.get("name") or x.get("value") or next(iter(x.values()), "")
                        return x
                    v = ", ".join(str(flat(x)) for x in v[:4])
                print(f"   {label}: {v}")
                break
    print(f"\n   (응답 필드 총 {len(row)}개. 전체는 level1_result.json)")
PY

echo "saved level1_result.json"
