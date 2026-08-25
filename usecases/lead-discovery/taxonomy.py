"""업종 분류와 직무 신호 사전. QJC 기존 고객과 신규 후보를 같은 축에 올리기 위한 단일 기준.

업종 분류는 처음 걸리는 키워드를 채택하지 않고 가중 점수로 판정한다.
회사 소개문 뒤쪽에 스치듯 나오는 단어("투자를 유치했습니다", "교육 지원 제도") 때문에
업종이 통째로 뒤집히는 문제가 실측에서 나왔기 때문이다. 회사명과 소개문 앞부분에
나오는 단어에 더 큰 가중치를 준다.
"""

from __future__ import annotations

import re

HEAD_CHARS = 300  # 소개문 앞부분. 보통 여기에 본업이 적힌다.

# 채용 사이트 본문에는 폭 없는 공백이 섞여 들어온다. 눈에 보이지 않지만 정규식의
# \s 에 걸리지 않아 "160명의 임직원" 같은 구절이 통째로 매칭에 실패한다.
_ZERO_WIDTH = str.maketrans({c: " " for c in "​‌‍⁠﻿\xa0"})


def clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").translate(_ZERO_WIDTH))
W_NAME, W_HEAD, W_BODY = 3, 2, 1
W_STRONG, W_WEAK = 3, 1
MIN_SCORE = 4

# bucket -> (강한 근거, 약한 근거)
BUCKETS: dict[str, tuple[list[str], list[str]]] = {
    "AI/ML 전문": (
        [r"AI\s?(서비스|솔루션|스타트업|전문|기술)\s?(기업|회사|스타트업)",
         r"인공지능\s?(전문|솔루션|기술)\s?(기업|회사)", r"\bLLM\b", r"파운데이션\s?모델",
         r"생성형\s?AI\s?(기업|스타트업|전문)", r"AI\s?컴퍼니", r"비전\s?AI", r"MLOps\s?플랫폼"],
        [r"AI\s?기반", r"머신러닝", r"딥러닝"],
    ),
    "SI/외주개발": (
        [r"SI\s?(기업|업체|전문)", r"시스템\s?통합", r"개발\s?용역", r"외주\s?개발", r"웹\s?에이전시"],
        [r"SI/?SM", r"아웃소싱"],
    ),
    "교육/HRD": (
        [r"교육\s?(플랫폼|서비스|기업|회사|컨텐츠|콘텐츠)", r"이러닝", r"에듀테크", r"에듀\s?",
         r"학원", r"대학교?\b", r"아카데미", r"부트캠프", r"입시", r"어학", r"학습자", r"수강생",
         r"교육\s?과정", r"연수원", r"평생교육"],
        [r"교육", r"강의", r"학습"],
    ),
    "이커머스/유통": (
        [r"이커머스", r"e-?commerce", r"커머스", r"쇼핑몰", r"리테일", r"유통\s?(사업|기업|플랫폼)",
         r"셀러", r"D2C", r"온라인\s?판매", r"오픈마켓", r"백화점", r"편의점"],
        [r"유통", r"브랜드몰", r"구매\s?고객"],
    ),
    "물류/공급망": (
        [r"물류", r"해운", r"항만", r"공급망", r"supply\s?chain", r"포워딩", r"운송", r"창고\s?관리",
         r"라스트\s?마일", r"배송\s?(플랫폼|서비스)"],
        [r"배송", r"화물"],
    ),
    "제조/건설/설비": (
        [r"제조\s?(기업|업|현장|사)", r"공장", r"생산\s?(라인|공정)", r"설비", r"반도체", r"자동차\s?부품",
         r"건설", r"시공", r"플랜트", r"중공업", r"철강", r"소재\s?(기업|산업)", r"스마트\s?팩토리"],
        [r"제조", r"기계", r"장비", r"품질\s?검사"],
    ),
    "금융/투자": (
        [r"핀테크", r"은행", r"증권", r"보험", r"자산운용", r"카드사", r"대출\s?(플랫폼|서비스)",
         r"가상자산", r"거래소", r"벤처캐피탈", r"액셀러레이터", r"자본시장", r"결제\s?(플랫폼|솔루션)"],
        [r"금융", r"투자\s?(플랫폼|서비스)"],
    ),
    "헬스케어/바이오": (
        [r"약국", r"의약품", r"병원", r"의료", r"바이오", r"제약", r"진단", r"헬스케어", r"환자",
         r"디지털\s?치료", r"임상", r"약사"],
        [r"웰니스", r"피트니스", r"건강"],
    ),
    "미디어/콘텐츠": (
        [r"미디어\s?(기업|그룹|커머스)?", r"콘텐츠\s?(제작|기업|스튜디오)", r"방송", r"엔터테인먼트",
         r"웹툰", r"게임\s?(개발|회사|스튜디오)", r"음악", r"출판", r"광고\s?대행", r"퍼블리싱",
         r"크리에이터", r"OTT"],
        [r"콘텐츠", r"영상", r"마케팅\s?대행"],
    ),
    "공공/기관": (
        [r"공공기관", r"공단", r"공사\b", r"재단", r"협회", r"진흥원", r"연구원", r"지자체", r"국립"],
        [r"공공", r"정부"],
    ),
    "부동산/프롭텍": (
        [r"부동산", r"프롭테크", r"중개\s?(플랫폼|서비스)", r"분양", r"임대\s?(관리|플랫폼)", r"시행사"],
        [r"공간", r"임대"],
    ),
    "여행/모빌리티": (
        [r"여행", r"항공", r"호텔", r"숙박", r"모빌리티", r"택시", r"렌터카", r"주차"],
        [r"관광", r"이동\s?수단"],
    ),
    "식음료/외식": (
        [r"외식", r"프랜차이즈", r"카페\s?(브랜드|프랜차이즈)", r"음식점", r"F&B", r"식품\s?(기업|제조)",
         r"레스토랑", r"주문\s?(플랫폼|서비스)"],
        [r"식품", r"메뉴"],
    ),
    "뷰티/패션": (
        [r"화장품", r"뷰티\s?(브랜드|기업)", r"패션\s?(브랜드|플랫폼|기업)", r"스킨케어", r"코스메틱"],
        [r"뷰티", r"패션"],
    ),
    "IT/SaaS": (
        [r"SaaS", r"소프트웨어\s?(기업|회사)", r"클라우드\s?(서비스|플랫폼)", r"보안\s?솔루션",
         r"개발자\s?도구", r"디자인\s?(툴|플랫폼)", r"협업\s?툴", r"업무\s?(툴|소프트웨어)",
         r"데이터\s?(플랫폼|인프라)"],
        [r"플랫폼", r"솔루션", r"소프트웨어", r"IT\b"],
    ),
}

# 도메인 특정 버킷을 일반 버킷보다 우선한다. 동점일 때만 쓰인다.
GENERIC_LAST = ["IT/SaaS"]


def _hits(patterns: list[str], text: str) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


# 상호 자체가 업종을 선언하는 경우. 소개문보다 우선한다.
# 교육 회사가 소개문의 의대 입시 설명 때문에 헬스케어로 잡히던 사례를 막는다.
NAME_OVERRIDES: list[tuple[str, str]] = [
    # "러닝"은 딥러닝, 머신러닝에도 들어 있어 앞 글자를 확인한다.
    ("교육/HRD", r"(교육|에듀|edu|아카데미|학원|대학|스쿨|스터디|캠퍼스|(?<!딥)(?<!머신)(?<!e)러닝)"),
    ("헬스케어/바이오", r"(병원|제약|바이오|팜\b|pharm|메디|medi|헬스|덴탈|클리닉)"),
    ("금융/투자", r"(은행|증권|캐피탈|자산운용|보험|카드\b|페이\b|핀테크)"),
    ("물류/공급망", r"(로지|logi|물류|해운|택배|포워딩)"),
    ("미디어/콘텐츠", r"(미디어|엔터|스튜디오|방송|퍼블리|매거진)"),
    ("제조/건설/설비", r"(중공업|반도체|전자\b|화학|소재\b|건설|산업\b|정밀|기계)"),
    ("이커머스/유통", r"(커머스|마켓|몰\b|스토어|유통|리테일)"),
    ("여행/모빌리티", r"(항공|여행|투어|모빌리티|렌터카)"),
    ("식음료/외식", r"(푸드|food|외식|F&B|식품)"),
    ("뷰티/패션", r"(코스메틱|뷰티|패션|화장품)"),
]


def _name_override(name: str) -> str:
    for bucket, pattern in NAME_OVERRIDES:
        if re.search(pattern, name, re.IGNORECASE):
            return bucket
    return ""


def classify_industry(company_name: str = "", intro: str = "", extra: str = "") -> str:
    """회사명, 소개문 앞부분, 나머지 텍스트에 서로 다른 가중치를 주어 업종을 판정한다."""
    name = clean(company_name)
    override = _name_override(name)
    if override:
        return override
    intro = clean(intro)
    head = intro[:HEAD_CHARS]
    body = intro[HEAD_CHARS:] + " " + clean(extra)

    scores: dict[str, int] = {}
    front: dict[str, int] = {}
    for bucket, (strong, weak) in BUCKETS.items():
        s = 0
        for text, wpos in ((name, W_NAME), (head, W_HEAD), (body, W_BODY)):
            if not text:
                continue
            s += wpos * (W_STRONG * _hits(strong, text) + W_WEAK * _hits(weak, text))
        if s:
            scores[bucket] = s
            front[bucket] = _hits(strong, name) + _hits(strong, head)
    if not scores:
        return "미분류"
    # 회사명이나 소개문 앞부분에 근거가 없는 버킷은 제외한다. 소개문 뒤쪽에서
    # 고객사나 복지 제도를 설명하며 스친 단어가 업종을 결정하는 것을 막는다.
    anchored = {b: s for b, s in scores.items() if front.get(b)}
    scores = anchored or scores
    best = max(scores.values())
    if best < MIN_SCORE:
        return "미분류"
    tied = [b for b, s in scores.items() if s == best]
    if len(tied) > 1:
        specific = [b for b in tied if b not in GENERIC_LAST]
        tied = specific or tied
    return sorted(tied)[0]


def industry_scores(company_name: str = "", intro: str = "", extra: str = "") -> dict[str, int]:
    """판정 근거 확인용. 디버깅과 검증에 쓴다."""
    name, head = company_name or "", (intro or "")[:HEAD_CHARS]
    body = (intro or "")[HEAD_CHARS:] + " " + (extra or "")
    out = {}
    for bucket, (strong, weak) in BUCKETS.items():
        s = sum(w * (W_STRONG * _hits(strong, t) + W_WEAK * _hits(weak, t))
                for t, w in ((name, W_NAME), (head, W_HEAD), (body, W_BODY)) if t)
        if s:
            out[bucket] = s
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------- 직무 신호

AI_ROLE_PATTERNS = [
    r"\bAI\b", r"인공지능", r"머신\s?러닝", r"machine\s?learning", r"\bML\b", r"\bMLOps\b",
    r"딥\s?러닝", r"deep\s?learning", r"\bLLM\b", r"생성형", r"generative",
    r"데이터\s?(엔지니어|사이언티스트|분석|플랫폼)", r"data\s?(engineer|scientist|analyst)",
    r"\bNLP\b", r"자연어", r"컴퓨터\s?비전", r"computer\s?vision", r"추천\s?시스템",
    r"프롬프트", r"prompt\s?engineer", r"\bRAG\b", r"에이전트\s?개발", r"agentic",
]

AUTOMATION_PATTERNS = [
    r"자동화", r"automation", r"\bRPA\b", r"워크\s?플로우", r"workflow",
    r"업무\s?효율", r"프로세스\s?(개선|자동)", r"\bn8n\b", r"make\.com", r"zapier",
    r"사내\s?시스템", r"백오피스", r"어드민\s?툴", r"내부\s?도구",
]

RESEARCH_PATTERNS = [
    r"리서치\s?사이언티스트", r"research\s?scientist", r"연구\s?위원", r"박사\s?(급|학위)",
    r"\bPhD\b", r"논문", r"모델\s?(학습|경량화)\s?최적화", r"파운데이션\s?모델",
]

# 비개발 직군. 대소문자를 구분하는 약어와 한국어 직군명을 분리한다.
# 소문자를 허용하면 PostgreSQL의 "po", Component의 "po"까지 걸려 오탐이 난다.
NONDEV_KO = [
    r"마케(터|팅)", r"기획자", r"기획\s?팀", r"영업", r"세일즈", r"인사\s?(담당|팀)",
    r"재무", r"회계", r"운영\s?(매니저|담당)", r"고객\s?(성공|지원)",
    r"콘텐츠\s?(에디터|매니저|마케)", r"디자이너", r"사업\s?개발", r"전략\s?기획",
]
NONDEV_ACRONYM = [r"\bPM\b", r"\bPO\b", r"\bMD\b", r"\bHR\b", r"\bCS\b", r"\bBD\b"]


def _found(patterns: list[str], text: str, flags=re.IGNORECASE) -> list[str]:
    out = []
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            out.append(m.group(0).strip())
    return out


def role_signals(title: str, context: str = "") -> dict[str, list[str]]:
    """공고에서 직무 신호를 뽑는다.

    title    공고 제목. 직군 판정은 제목만 본다.
    context  기술 스택과 직무 분류. AI 및 자동화 신호 판정에만 더한다.

    직군 판정에 소스 카테고리 이름을 넣으면 "마케팅/광고" 같은 목록 라벨이 그대로
    비개발 직군으로 잡히므로 분리한다.
    """
    t = title or ""
    wide = f"{t} {context or ''}"
    nondev = _found(NONDEV_KO, t) + _found(NONDEV_ACRONYM, t, flags=0)
    return {
        "ai": _found(AI_ROLE_PATTERNS, wide),
        "automation": _found(AUTOMATION_PATTERNS, wide),
        "research": _found(RESEARCH_PATTERNS, wide),
        "nondev": nondev,
    }


# AI 코딩 도구를 실무에 도입하려는 신호. QJC 상품과 가장 정확히 겹치는 지점이다.
# 공고에 이 이름들이 적혀 있다는 것은 도구는 정했고 사람을 못 구했다는 뜻이다.
AI_TOOL_PATTERNS = [
    r"Claude\s?Code", r"클로드\s?코드", r"Cursor", r"커서\s?(AI|에디터)",
    r"(GitHub\s?)?Copilot", r"코파일럿", r"Windsurf", r"Devin",
    r"바이브\s?코딩", r"vibe\s?coding", r"AI\s?코딩\s?(도구|툴)",
    r"AI\s?(개발|코딩)\s?(도구|툴)\s?활용", r"LLM\s?(도구|툴)\s?활용",
]

# 소개문에 적힌 임직원 수. "약 160명의 임직원이 함께하고 있습니다" 같은 문장을 잡는다.
HEADCOUNT_PATTERNS = [
    r"(?:약\s*)?([\d,]+)\s*명(?:의)?\s*(?:임직원|직원|구성원|팀원|멤버)",
    r"(?:임직원|직원|구성원|팀원)\s*(?:수\s*)?(?:약\s*)?([\d,]+)\s*명",
]


def ai_tool_signals(text: str) -> list[str]:
    return _found(AI_TOOL_PATTERNS, clean(text))


def extract_headcount(text: str) -> int | None:
    """소개문에서 임직원 수를 뽑는다. 없으면 None."""
    text = clean(text)
    for p in HEADCOUNT_PATTERNS:
        m = re.search(p, text)
        if m:
            try:
                n = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if 1 <= n <= 500000:
                return n
    return None


# ------------------------------------------------- 본업 판정 오버레이
# 업종과 별개로 판정한다. "물류 도메인이면서 AI가 본업"인 회사가 실제로 존재하므로
# 업종 버킷 하나로 두 성격을 동시에 표현할 수 없다.

AI_NATIVE_PHRASES = [
    r"AI\s?(서비스|솔루션|스타트업|전문|기술)\s?(기업|회사|스타트업)",
    r"인공지능\s?(전문|솔루션|기술)\s?(기업|회사)",
    r"생성형\s?AI\s?(기업|스타트업|전문)",
    r"AI\s?컴퍼니", r"\bAI\s?(Lab|랩)\b",
    r"(자체|자사)\s?(개발한?)?\s?(LLM|파운데이션\s?모델)",
    r"AI를\s?(개발|만드)", r"AI\s?제품을\s?만드",
]

SI_VENDOR_PHRASES = [
    r"SI\s?(기업|업체|전문)", r"시스템\s?통합\s?(기업|업체)", r"개발\s?용역",
    r"외주\s?개발", r"웹\s?에이전시", r"SI/?SM\s?사업",
]


def is_ai_native(company_name: str = "", intro: str = "") -> list[str]:
    """AI 자체가 본업이라고 스스로 밝힌 근거를 반환한다. 비면 해당 없음."""
    scope = f"{company_name or ''} {(intro or '')[:HEAD_CHARS]}"
    return _found(AI_NATIVE_PHRASES, scope)


def is_si_vendor(company_name: str = "", intro: str = "") -> list[str]:
    scope = f"{company_name or ''} {(intro or '')[:HEAD_CHARS]}"
    return _found(SI_VENDOR_PHRASES, scope)


# 비테크 업종. AI 인재를 직접 뽑기 시작했다면 내부 역량이 아직 얇다는 뜻이고,
# QJC의 교육과 구축 수요가 가장 크게 잡히는 구간이다.
NON_TECH_BUCKETS = {
    "교육/HRD", "이커머스/유통", "물류/공급망", "제조/건설/설비", "금융/투자",
    "헬스케어/바이오", "미디어/콘텐츠", "공공/기관", "부동산/프롭텍",
    "여행/모빌리티", "식음료/외식", "뷰티/패션",
}

NEGATIVE_BUCKETS = {"AI/ML 전문", "SI/외주개발"}
