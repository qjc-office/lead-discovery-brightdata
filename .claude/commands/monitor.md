---
description: 모니터링 대상을 수집하고, 전일 대비 변화를 확인하고, 필요하면 Slack으로 알린다.
---

이 레포의 Bright Data 모니터링 파이프라인을 처음부터 끝까지 한 번에 실행한다.

인자로 대상 이름이 주어지면 그 대상을, 없으면 `scraper_config.json`의 `default_target`을 쓴다.
사용 예: `/monitor`, `/monitor amazon_products`, `/monitor public_job_postings`

## 순서

1. `qjc-monitor` MCP 서버가 연결돼 있으면 도구를 이 순서로 호출한다.
   - `list_targets` — 대상 목록과 dataset_id를 확인한다 (대상을 지정하지 않았을 때만)
   - `fetch_postings` — 오늘 데이터를 수집한다. `BRIGHTDATA_API_KEY`가 없으면 `mock: true`로 호출한다
   - `diff_check` — 전일 대비 변화를 확인한다 (신규·삭제·가격 등 감시 필드 변동)
   - 변화가 있으면 `notify_slack`을 호출한다. `SLACK_WEBHOOK_URL`이 없으면 `dry_run: true`로 payload만 보여준다

2. MCP 서버가 연결돼 있지 않으면 Bash로 같은 스크립트를 순서대로 실행한다.

   ```bash
   python3 fetch_postings.py --mock   # 키가 있으면 --live
   python3 diff_checker.py
   python3 notify.py --dry-run        # 실제 전송하려면 --dry-run 제거
   ```

## 지켜야 할 것

- `CLAUDE.md`에 적힌 규칙을 그대로 따른다: mock 데이터를 실제 결과로 보고하지 않는다, 결과를 지어내지 않는다, 키·웹훅 URL을 파일에 쓰지 않는다.
- 수집이 실패하면 실패했다고 있는 그대로 답한다.
- 변화가 없으면 "변화 없음"이라고 답하고 Slack에는 보내지 않는다 (사용자가 `--always`를 명시하지 않는 한).
