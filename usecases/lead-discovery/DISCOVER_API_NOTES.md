# 경로 (b) Discover API 조사 결과 (2026-08-15)

BD 본사 Tech팀이 회사명에서 Crunchbase organization URL을 얻는 경로로 제시한
"Discover api(serp rerank based on intent) => crunchbase api"를 실제로 확인한 기록이다.
문서 조사가 아니라 API에 직접 물어봤다. 검증 스크립트는 `discover_probe.py`.

## 1. 실체: Scraper API의 discover 모드가 맞다

```
POST /datasets/v3/trigger?dataset_id=gd_l1vijqt9jfj7olije&type=discover_new&discover_by=keyword
body: [{"keyword": "<회사명>"}]
```

`discover_by` 변형을 전수 시도한 결과다.

| 시도 | 응답 |
|---|---|
| `discover_by=keyword` | **HTTP 200**, snapshot_id 반환 |
| `discover_by=name` | 400 `Incorrect discovery collector id Available types: keyword` |
| `discover_by=company_name` | 400 (동일) |
| `discover_by=url` | 400 (동일) |
| `type=discover_new` 단독 | 400 `discover_by is required` |

Crunchbase 데이터셋의 discover는 **keyword 한 가지만** 지원한다. 별도 zone이나 추가 결제는
필요 없었고, 기존 Scraper API 토큰으로 그대로 호출된다.

## 2. 품질: 우리 용도로는 쓸 수 없다

`keyword="<회사명>"` 한 건으로 측정했다.

- 반환 **25건**, 그중 한국 기업 **2건**
- **정답인 AcmeCorp은 25건 안에 없다**
- 실제로 반환된 것은 철자가 비슷한 다른 회사들이다: Biropharma(헝가리), barapharma(스페인),
  BioPharma(한국, 다른 회사), Baupharma(체코), BoroPharm(미국), BioPharma Scientific(미국) 등

결정적인 대목은 **AcmeCorp이 Crunchbase에 실재한다는 점**이다. 앞선 slug 추정 실험에서
`crunchbase.com/organization/acmecorp`이 `emp=101-250, country=South Korea`로 정상 조회됐다.
즉 레코드가 데이터셋에 있는데도 keyword discover가 그것을 못 찾았다.

discover는 정확 해석기가 아니라 **철자 유사 텍스트 검색**이다. 후보군에 정답이 없으므로
판별 레이어를 뒤에 붙여도 구제되지 않는다.

## 3. 비용 관점

1개 키워드에 25 레코드를 소비했다. 210건에 적용하면 산술적으로 5,000여 레코드를 쓰면서
정답률은 위와 같다. 한정된 크레딧을 여기에 쓸 이유가 없다.

## 4. 결론

경로 (b)는 **기각**한다. 회사명 → organization URL 해석에는 부적합하다.
경로 (a)(SERP 해석 + 판별 레이어)로 간다.

다만 discover 자체가 무용한 것은 아니다. "특정 회사를 찾는" 용도가 아니라
"어떤 키워드에 걸리는 회사들을 훑는" 탐색형 용도(예: 신규 리드 발굴)라면 맞는 도구다.
9월·10월 콘텐츠에서 다른 각도로 쓸 여지는 남겨 둔다.

## 5. 재현

```bash
cd <repo-root> && set -a && source .env && set +a
python3 usecases/lead-discovery/discover_probe.py          # 지원 여부 전수 확인
python3 usecases/_shared/bd_snapshot.py <snapshot_id> 10   # 결과 요약
```
