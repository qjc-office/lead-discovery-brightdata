#!/usr/bin/env bash
# 리드 발굴 파이프라인 전체 실행. ICP 도출부터 리드 CSV까지 한 번에 돈다.
#
#   ./run.sh              공개 소스 수집 + Bright Data 경로는 픽스처로 검증
#   ./run.sh --live       Bright Data 키가 있을 때 실데이터 경로까지 사용
#
# 필요한 환경변수 (없으면 해당 보강 단계만 건너뛴다)
#   DATA_GO_NTS_API_KEY   국세청 사업자등록상태
#   DART_API_KEY          DART 공시대상 법인 명부
#   BRIGHTDATA_API_KEY    Bright Data Web Scraper API

set -euo pipefail
cd "$(dirname "$0")"

MODE="--mock"
if [ "${1:-}" = "--live" ]; then MODE=""; fi

if [ -f ../../.env ]; then set -a; . ../../.env; set +a; fi

python3 build_icp.py
python3 collect.py --source all --max-pages 50 --wanted-pages 3 ${MODE}
python3 enrich.py --max-enrich 250
python3 score.py --top 15

echo
echo "산출물은 results/ 에 있습니다."
ls -1 results/*.csv results/*.md 2>/dev/null || true
