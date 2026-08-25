# 경쟁사 프로그램 모니터링

AI 교육·부트캠프 경쟁사의 강의명, 가격, 기간, 모집상태를 공개 페이지에서 모아 QJC 상품과 나란히 놓고 비교하는 파이프라인입니다.

## 왜 만들었나

QJC는 AI 코딩 부트캠프(특이점 빌더스), VOD, 기업 강의를 팝니다. 가격을 개정하거나 새 기수를 기획할 때마다 경쟁사 사이트를 하나씩 열어 강의명과 가격을 옮겨 적었습니다. 브랜드 4~5곳만 훑어도 반나절이 지나가고, 두 달 뒤 같은 작업을 하면 지난번 숫자와 비교할 방법이 없었습니다.

필요한 건 화려한 대시보드가 아니라 "오늘 기준으로 경쟁사 프로그램이 얼마에 팔리고 있는지"를 매번 같은 방식으로 찍어 두는 일입니다. 같은 형식으로 쌓이면 그때부터 가격 변화와 기수 회전이 보입니다.

## 무엇을 하나

```
targets.json (감시 대상)
   |
collect.py
   |-- robots.txt 를 실행 시점에 읽어 경로별로 허용 여부를 판정
   |-- sitemap 또는 목록 페이지에서 후보 URL 추출
   |-- 키워드로 후보를 좁힌 뒤 상세 페이지를 1.5초 간격으로 요청
   |-- 사이트별 파서로 강의명·가격·기간·형태·모집상태·키워드 추출
   |-- 정적 수집이 막힌 소스는 Bright Data 어댑터로 넘김
   |
   +--> results/competitors_YYYYMMDD.csv
   +--> results/blocked-sources.md
   +--> results/run-log.txt
   |
compare.py
   +--> results/comparison.md (QJC 상품 대비 비교표 + 포지셔닝 시사점)
```

## 실행

```bash
./run.sh                  # 공개 페이지만 수집
./run.sh --bd             # 막힌 소스를 Bright Data 어댑터(mock)로 보완
./run.sh --bd --bd-live   # 같은 경로를 실제 API 키로 실행
./run.sh --only nova      # 한 소스만 (targets.json 의 key)
python3 collect.py --bd-probe   # 토큰 유효성만 확인
```

Python 3 표준 라이브러리만 씁니다. 설치할 패키지가 없습니다.

## 먼저 감시 대상부터 정하세요

리포에 들어 있는 `targets.json`은 **자리표시자 템플릿**입니다. 여섯 소스가 전부 `example.com` 하위 주소라 그대로 실행하면 아무것도 수집되지 않습니다. 남의 감시 목록을 그대로 물려받는 건 어차피 쓸모가 없으니, 본인이 볼 사이트로 바꿔 쓰는 게 맞습니다.

여섯 개를 남겨 둔 이유는 수집 방식 여섯 가지를 하나씩 보여 주기 위해서입니다. sitemap, sitemap 인덱스, 목록 페이지 링크 추출, `__NEXT_DATA__`, Bright Data 폴백, robots 차단. 본인 대상이 어느 쪽인지 보고 해당 블록을 복사해 주소만 갈아 끼우면 됩니다.

`user_agent`도 반드시 본인 것으로 바꾸세요. 남의 이름을 달고 요청하면 곤란해지는 쪽은 그 이름의 주인입니다.

자리표시자 상태로 `./run.sh`를 돌리면 여섯 소스 모두 robots.txt를 읽지 못해 0행으로 끝나고 종료 코드가 1이 됩니다. 고장이 아니라 "수집 0행"을 cron이 알아채라고 일부러 그렇게 둔 것입니다. `results/blocked-sources.md`에 소스별 사유가 남으니 열어 보면 됩니다. 같은 이유로 `--bd`의 mock 경로도 자리표시자 상태에서는 실행되지 않습니다. robots를 읽지 못한 소스는 Bright Data 경로로도 가지 않기 때문입니다. Bright Data 어댑터만 먼저 확인하고 싶다면 리포 루트의 `python3 fetch_postings.py --mock`이 네트워크 없이 끝까지 돕니다.

## 실제로 돌려 보면 어떤 결과가 나오나 (2026-08-04, 내부 실행)

국내 AI·개발 교육 플랫폼 여러 곳을 대상으로 한 번 돌린 기록입니다. 어느 회사인지는 적지 않습니다. 아래 config로는 재현되지 않으니 규모 감을 잡는 용도로만 보세요.

| 항목 | 값 |
|---|---|
| 실제 수집 | 57행 |
| 브랜드 | 4곳 (19 / 14 / 12 / 12행) |
| 가격을 읽어낸 행 | 55행 |
| 정가와 판매가가 함께 잡힌 행 | 41행 |
| Bright Data mock 합성 행 | 2행 (실제 값 아님, 별도 표로 분리) |
| robots.txt 가 금지해 요청하지 않은 소스 | 2곳 |
| robots 는 허용하지만 정적 수집이 0건인 소스 | 1곳 |

수집 결과에서 나온 관찰 몇 가지입니다.

- 유료 43건의 가격은 1만원에서 220만원까지 퍼져 있고 중앙값은 243,000원입니다. 저가 VOD가 표본의 다수라 중앙값 자체보다 형태별 비교가 유용합니다.
- 정가와 판매가가 함께 걸린 20건의 할인율 중앙값은 45%입니다. 정가를 띄워 두고 상시 할인가로 파는 방식이 이 표본의 기본값입니다.
- 프로그램명에 가장 많이 등장한 키워드는 AI(41건), 코딩(14건), 코드(8건), 바이브(8건) 순입니다.

숫자와 근거는 `results/comparison.md`, 실행 전 과정은 `results/run-log.txt`에 있습니다.

## 수집 규칙

- 대상 호스트의 robots.txt를 실행할 때마다 새로 읽고, `RobotsRules`가 경로별로 Allow/Disallow를 판정합니다. 판정 결과는 요청 한 건마다 로그에 남습니다.
- 금지 경로는 요청하지 않고 `blocked-sources.md`에 사유와 함께 기록합니다. robots가 금지한 소스는 Bright Data 경로로도 수집하지 않습니다.
- 요청 간격은 최소 1.5초입니다.
- User-Agent는 `QJC-research/1.0 (+https://qjc.app)`으로 누가 요청하는지 밝힙니다.
- 로그인이 필요한 영역, 개인정보, 회원 전용 페이지는 대상이 아닙니다. 공개 상품 페이지만 봅니다.

`/api`를 금지한 사이트라면 API를 호출하지 않고 공개 sitemap과 상세 페이지만 씁니다. 템플릿의 `coursebridge` 블록이 그 경우를 그려 둔 것입니다.

## Bright Data가 필요한 지점

정적 HTML만으로는 닿지 않는 자리가 실제로 나왔습니다. 두 경우 모두 robots.txt는 허용하는데 요청 결과가 비어 있는 상황입니다.

**목록이 JS로 그려지는 사이트**: robots.txt는 전체 허용(`Allow: /`)인데 목록 페이지가 9KB짜리 빈 껍데기입니다. 강의 카드가 브라우저에서 JavaScript로 그려지기 때문에 정적 요청으로는 링크 한 개도 나오지 않습니다. 렌더링된 결과가 필요한 전형적인 경우입니다. 템플릿의 `pixelrun` 블록이 이 자리입니다.

**검색 결과 페이지**: `/search?s=클로드 코드`는 200을 반환하지만 HTML에 강의 링크가 없습니다. sitemap을 훑어 URL 슬러그로 후보를 좁히는 방식은 키워드 커버리지가 sitemap에 실린 범위로 제한됩니다. 검색 결과가 렌더링되면 특정 주제의 신규 강의를 훨씬 촘촘히 잡을 수 있습니다.

현재 토큰 상태는 이렇습니다. 환경변수 `BRIGHTDATA_API_TOKEN`(36자)이 설정되어 있지만 실제 호출은 `401 Token expired`를 반환합니다. `collect.py`는 실행할 때마다 이 검증을 먼저 수행해 결과를 로그에 남기므로, 키가 갱신되면 같은 로그 줄이 인증 통과로 바뀝니다.

```
[bd-probe] 토큰 감지 (길이 36자). progress 엔드포인트로 유효성 확인
[bd-probe] 인증 실패: 401 Unauthorized.
```

`--bd --bd-live`로 실제 수집을 시도한 기록도 `run-log.txt`에 남아 있습니다.

```
[bd:live] trigger dataset_id=REPLACE_WITH_CUSTOM_SCRAPER_ID inputs=1
[pixelrun] Bright Data ERROR 401 Unauthorized.
```

키가 준비되면 `targets.json`의 `bright_data.dataset_id`에 실제 스크레이퍼 ID를 넣는 것으로 live 경로가 열립니다. 트리거와 폴링, 스냅샷 다운로드는 상위 폴더의 `bd_client.py`를 그대로 재사용하므로 추가 구현이 없습니다.

mock 픽스처(`mock/pixelrun_courses_part1.json`)는 합성 레코드입니다. 파이프라인이 필드를 CSV까지 흘려보내는지 확인하는 용도라 금액과 모집상태를 일부러 비워 두었고, CSV의 `data_origin` 열과 비교표의 별도 섹션으로 실제 수집분과 구분됩니다.

## 파일

| 파일 | 역할 |
|---|---|
| `targets.json` | 감시 대상, 감시 키워드, QJC 비교 기준 상품 |
| `fetcher.py` | HTTP 요청, robots.txt 파싱과 판정, 요청 간격 제어 |
| `parsers.py` | 사이트별 추출기 (JSON-LD, `__NEXT_DATA__`, 본문 텍스트) |
| `collect.py` | 오케스트레이션, Bright Data 어댑터, CSV·차단목록 출력 |
| `compare.py` | QJC 상품 대비 비교표와 시사점 생성 |
| `run.sh` | 수집에서 비교표까지 한 번에 |
| `mock/` | Bright Data 어댑터용 합성 픽스처 |
| `results/` | 실행 산출물 |

## 감시 대상 추가하기

코드를 고칠 필요 없이 `targets.json`의 `sources`에 항목을 더합니다. 새 소스도 robots 판정과 요청 간격을 똑같이 통과합니다.

`parser`는 그 페이지가 데이터를 어떻게 내놓는지로 고릅니다.

| 값 | 언제 |
|---|---|
| `jsonld_course` | `<script type="application/ld+json">`에 schema.org Course가 있을 때 |
| `jsonld_plus_text` | 위와 같지만 정가·형태·기간을 본문에서 더 긁어야 할 때 |
| `next_data` | Next.js 사이트라 `__NEXT_DATA__`에 상품 객체가 통째로 실릴 때 |
| `text_block` | 구조화 데이터가 없어 `<title>`과 본문 정규식으로 버텨야 할 때 |
| `generic` | 구조를 모를 때. schema.org Course만 신뢰하고 나머지는 비웁니다 |

다섯 개로 안 되면 `parsers.py`에 함수 하나를 추가하고 `PARSERS`에 등록합니다.

```json
{
  "key": "example",
  "brand": "예시교육",
  "base": "https://example.com",
  "discovery": { "type": "sitemap", "url": "https://example.com/sitemap.xml",
                 "url_include": ["/course/"], "url_keyword_filter": true },
  "parser": "generic",
  "max_pages": 10
}
```

## 알아 둘 점

- 값을 못 읽으면 빈칸으로 둡니다. 추정치나 비슷한 상품의 가격을 대신 넣지 않습니다. 오프라인 워크숍처럼 마감된 상품은 페이지에 금액이 없어 가격이 비어 있습니다.
- 표본은 이번 실행에서 접근 가능했던 범위입니다. 시장 전체 분포가 아닙니다.
- 같은 강의라도 국비지원, 기수, 쿠폰에 따라 실제 결제가는 달라집니다.
- 일부 사이트는 상품 메타태그가 다른 상품 것으로 남아 있습니다. 실제로 한 소스에서 `productName`은 클로드 코드 강의인데 `og_title`과 `meta_keywords`는 데이터 시각화 강의 것이 붙어 있었습니다. 두 값이 어긋나면 메타 키워드를 버리고 프로그램명에서 키워드를 뽑습니다(`parse_next_data`).
- 사이트 구조가 바뀌면 파서가 조용히 빈 값을 낼 수 있습니다. `run-log.txt`의 `parsed=no` 비율과 CSV의 빈 가격 비율을 주기적으로 보는 편이 안전합니다.
