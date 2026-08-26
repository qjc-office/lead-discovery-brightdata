#!/usr/bin/env bash
# Level 1: the smallest possible Bright Data Web Scraper API round trip.
# Three calls: trigger, poll, download. Needs BRIGHTDATA_API_KEY.
#
# 키는 둘 중 아무 방식이나 된다. .env 에 적어 두면 이 스크립트가 알아서 읽는다.
#
#   cp .env.example .env && vi .env      # BRIGHTDATA_API_KEY=... 한 줄
#   ./level1_curl.sh [company-slug]
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

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 키가 셸에 없으면 .env 를 읽는다. run_daily.sh 와 같은 방식이다.
# 이게 없으면 ".env 에 키를 넣었는데 왜 안 되나"에서 막힌다.
if [ -z "${BRIGHTDATA_API_KEY:-}" ] && [ -f "$DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$DIR/.env"
  set +a
fi

if [ -z "${BRIGHTDATA_API_KEY:-}" ]; then
  echo "BRIGHTDATA_API_KEY 가 없습니다. 둘 중 하나를 하세요." >&2
  echo "  1) cp .env.example .env  후 .env 에 BRIGHTDATA_API_KEY=... 를 적는다" >&2
  echo "  2) export BRIGHTDATA_API_KEY=...  로 이 터미널에 직접 넣는다" >&2
  echo "키는 https://brightdata.com/cp/setting/users 에서 받습니다." >&2
  exit 1
fi

API="https://api.brightdata.com"
# 공개 기업 정보 페이지 "Crunchbase companies information" (collect by url)
# https://docs.brightdata.com/api-reference/web-scraper-api/overview
DATASET_ID="gd_l1vijqt9jfj7olije"

SLUG="${1:-airbnb}"
TARGET_URL="https://www.crunchbase.com/organization/${SLUG}"

echo "1) trigger  ${TARGET_URL}"
TRIGGER_BODY=$(curl -sS -X POST \
  "${API}/datasets/v3/trigger?dataset_id=${DATASET_ID}&include_errors=true" \
  -H "Authorization: Bearer ${BRIGHTDATA_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "[{\"url\":\"${TARGET_URL}\"}]")

# 응답에 snapshot_id 가 없으면 여기서 멈추고 이유를 사람 말로 알려 준다.
# 그냥 python 에 넘기면 KeyError 트레이스백이 떠서 무엇이 잘못됐는지 안 보인다.
SNAPSHOT_ID=$(printf '%s' "$TRIGGER_BODY" \
  | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("snapshot_id") or "")
except Exception:
    print("")' 2>/dev/null)

if [ -z "$SNAPSHOT_ID" ]; then
  echo "요청이 거절됐습니다. Bright Data 응답은 이렇습니다." >&2
  printf '  %s\n' "$(printf '%s' "$TRIGGER_BODY" | head -c 300)" >&2
  echo "" >&2
  echo "흔한 원인 두 가지입니다." >&2
  echo "  - 키가 틀렸거나 만료됐다 (응답에 Unauthorized 나 401 이 보이면 이 경우)" >&2
  echo "    https://brightdata.com/cp/setting/users 에서 키를 다시 확인하세요." >&2
  echo "  - 계정에 이 데이터셋 사용 권한이나 크레딧이 없다" >&2
  exit 1
fi

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
