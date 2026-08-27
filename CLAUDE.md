# 프로젝트 컨텍스트: 경쟁사 모니터링 에이전트

Claude Code가 이 레포에서 작업할 때 참고하는 지침입니다.

## 이 레포가 하는 일

Bright Data Web Scraper API로 채용공고나 상품목록을 주기적으로 수집하고, 전일 대비 변화를 감지해 Slack으로 알립니다.

## 사용자가 최신 정보를 물으면

"오늘 새로 올라온 채용공고 있어?" 같은 질문을 받으면 추측하지 말고 도구를 호출하세요. 순서는 다음과 같습니다.

1. `fetch_postings` 로 오늘 데이터를 수집합니다
2. `diff_check` 로 전일 대비 변화를 확인합니다
3. 변화가 있고 사용자가 알림을 원하면 `notify_slack` 을 호출합니다

`.mcp.json`이 설정돼 있지 않으면 대신 Bash로 같은 스크립트를 실행하세요.

```bash
python3 fetch_postings.py --mock      # 또는 --live
python3 diff_checker.py
python3 notify.py --dry-run

# python3 라는 이름이 없는 환경(윈도우 python.org 설치본)이면 python 으로 바꿔 친다.
# .sh 스크립트들은 두 이름을 알아서 찾으므로 그대로 두면 된다.
```

## 지켜야 할 것

- **`--mock`이 기본값입니다.** `BRIGHTDATA_API_KEY`가 없으면 `--live`를 시도하지 마세요
- **mock 데이터를 실제 수집 결과로 보고하지 마세요.** 답변에 mock 모드였음을 반드시 밝힙니다
- **API 키와 웹훅 URL을 파일에 쓰지 마세요.** 환경변수로만 읽습니다. `.env`, `.mcp.json`은 커밋 금지입니다
- **결과를 지어내지 마세요.** 수집이 실패하면 실패했다고 답합니다
- 새 모니터링 대상은 코드가 아니라 `scraper_config.json`의 `targets`에 추가합니다

## 구조

| 파일 | 역할 |
|---|---|
| `bd_client.py` | Bright Data API 클라이언트 (trigger, poll, download). live와 mock 전환 지점 |
| `fetch_postings.py` | 수집 후 `data/<target>_YYYYMMDD.csv` 저장 |
| `diff_checker.py` | 최근 CSV 2개 비교, diff JSON 산출 |
| `notify.py` | diff를 Slack Block Kit 메시지로 변환 |
| `mcp_server.py` | 위 스크립트를 MCP 도구로 노출 |
| `scraper_config.json` | 모니터링 대상 정의 |

## 코드 규칙

- 표준 라이브러리만 사용합니다. 새 의존성을 추가하지 마세요
- 파일 300줄, 함수 50줄을 넘기지 않습니다
- 새 API 호출을 추가할 때는 공식 문서 URL을 주석에 남깁니다
