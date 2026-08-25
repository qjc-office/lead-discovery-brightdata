"""Render the ranked topics into a shortlist an editor can act on.

Nothing here invents a number. Angles and draft titles are chosen by rules that
read the collected metrics, and every claim in the text points back to a video
in videos_YYYYMMDD.csv.
"""

from __future__ import annotations

import csv
import re
import statistics as stats
from datetime import date
from pathlib import Path

FORMAT_WORDS = ["완전정복", "총정리", "정리", "가이드", "실전", "입문", "기초", "비교",
                "후기", "리뷰", "활용법", "세팅", "설치", "튜토리얼", "강의"]
TIME_CUE = re.compile(r"(\d+\s*(분|시간|초|일))|(분|시간|초)\s*(만에|안에|컷)")


def own_baseline(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    views = [int(r["views"] or 0) for r in rows]
    if not views:
        return {}
    return {"n": len(views), "median": int(stats.median(views)), "max": max(views),
            "rows": sorted(rows, key=lambda r: int(r["views"] or 0), reverse=True)[:5]}


def title_patterns(videos: list[dict]) -> dict:
    titles = [v["title"] for v in videos if v["title"]]
    if not titles:
        return {"n": 0, "words": []}
    words = [w for w in FORMAT_WORDS if sum(1 for t in titles if w in t) >= 1]
    durations = [v["duration_sec"] for v in videos if v.get("duration_sec")]
    return {"n": len(titles),
            "number": sum(1 for t in titles if re.search(r"\d", t)),
            "time": sum(1 for t in titles if TIME_CUE.search(t)),
            "question": sum(1 for t in titles if "?" in t),
            "avg_len": round(sum(len(t) for t in titles) / len(titles)),
            "words": words[:3],
            "median_minutes": round(stats.median(durations) / 60) if durations else 0}


def angle_lines(topic: dict, members: list[dict]) -> list[str]:
    """Two sentences at most, each triggered by a number in the data."""
    notes: list[str] = []
    if topic["fresh_ratio_90d"] < 0.5 and topic["median_views"] >= 10000:
        notes.append(f"상위 영상의 {100 - topic['fresh_ratio_90d'] * 100:.0f}%가 90일보다 오래됐습니다. "
                     "최신 버전 기준으로 다시 정리하면 검색 상위를 가져올 자리가 있습니다.")
    leader = max(members, key=lambda v: v["sub_multiple"] or 0, default=None)
    if topic["max_sub_multiple"] >= 2 and leader and leader["sub_multiple"]:
        notes.append(f"구독자 {leader['subscribers']:,}명 채널이 {leader['view_count']:,}회를 냈습니다. "
                     "채널 규모가 아니라 주제가 끌어온 조회수입니다.")
    if len(notes) < 2 and topic["p75_velocity_per_day"] >= 2000:
        notes.append(f"상위 영상이 하루 {topic['p75_velocity_per_day']:,.0f}회로 오르는 중입니다. "
                     "지금 올리면 상승 구간에 붙습니다.")
    if len(notes) < 2 and topic["n_big_channels"] == 0:
        notes.append("10만 구독자 이상 채널이 아직 이 주제를 잡지 않았습니다.")
    if topic["big_channel_ratio"] >= 0.5:
        notes.append(f"다만 상위 결과의 {topic['big_channel_ratio']:.0%}를 큰 채널이 차지하고 있어 "
                     "같은 각도로는 밀립니다. 대상이나 사례를 좁히는 편이 안전합니다.")
    return notes[:2] if notes else ["수요는 확인됐지만 폭발 신호는 약합니다. 단독 편보다 시리즈 보조 편에 맞습니다."]


DIFFERENTIATION = {
    "입문": "상위 영상이 입문 설명에 몰려 있습니다. 실무에 적용한 결과를 앞세우면 겹치지 않습니다.",
    "기초": "상위 영상이 기초 설명에 몰려 있습니다. 실무에 적용한 결과를 앞세우면 겹치지 않습니다.",
    "완전정복": "상위 영상이 전 범위를 훑는 형식입니다. 업무 하나로 좁히면 같은 자리를 두고 겹치지 않습니다.",
    "총정리": "상위 영상이 전 범위를 훑는 형식입니다. 업무 하나로 좁히면 같은 자리를 두고 겹치지 않습니다.",
    "리뷰": "상위 영상이 기능 소개에 머뭅니다. 도입 후 숫자로 남은 변화를 보여주면 차이가 납니다.",
    "비교": "상위 영상이 도구 비교에 머뭅니다. 하나를 골라 끝까지 쓴 기록이 비어 있습니다.",
    "설치": "상위 영상이 설치와 세팅에 머뭅니다. 세팅 이후 실제 업무 화면이 비어 있습니다.",
    "실전": "상위 영상이 이미 실전 워크플로를 다룹니다. 같은 도구로 다른 직무의 업무를 잡으면 겹치지 않습니다.",
}


def draft_titles(topic: dict, pattern: dict) -> tuple[str, str]:
    """One draft title built from the formats winning titles actually use,
    plus the gap those same titles leave open."""
    words = pattern.get("words") or ["실전"]
    draft = f"{topic['topic']} {words[0]}"
    gap = next((DIFFERENTIATION[w] for w in words if w in DIFFERENTIATION),
               "상위 영상과 형식이 겹칩니다. 대상 독자를 한 단계 좁혀 잡는 편이 안전합니다.")
    return draft, gap


def render_shortlist(topics: list[dict], videos: dict[str, dict], baseline: dict,
                     counts: dict, top_n: int, stamp: str) -> str:
    lines = [f"# 다음 영상 주제 후보 ({date.today().isoformat()})", "",
             f"수집한 영상 {counts['videos']}건, 검색 키워드 {counts['keywords']}개에서 "
             f"주제 후보 {counts['topics']}개를 뽑았고 그중 상위 {min(top_n, len(topics))}개입니다. "
             "점수는 수요(조회수), 속도(하루 조회수), 레버리지(구독자 대비 배수), "
             "신선도(90일 이내 비중), 경쟁 밀도를 가중 합산한 값입니다.", ""]
    if counts.get("mock_excluded"):
        lines += [f"합성 예시 행 {counts['mock_excluded']}건은 순위에서 제외했습니다. "
                  "실제 수집값만 점수에 들어갑니다.", ""]
    if counts.get("non_korean_excluded"):
        lines += [f"제목에 한글이 없는 영상 {counts['non_korean_excluded']}건은 순위에서 뺐습니다. "
                  "QJC가 겨루는 자리는 한국어 검색 결과라서 그렇습니다. "
                  "원본 CSV에는 그대로 남아 있습니다.", ""]
    lines += [f"원천 수치는 `topics_{stamp}.csv`와 `videos_{stamp}.csv`에 그대로 남아 있습니다.", ""]
    lines += _summary_table(topics[:top_n])
    if baseline:
        lines += _own_section(baseline)
    for rank, topic in enumerate(topics[:top_n], start=1):
        members = [videos[i] for i in topic["evidence_video_ids"].split() if i in videos]
        lines += _topic_block(rank, topic, members, baseline)
    lines += ["## 읽는 법", "",
              "- 레버리지 배수가 1.0을 넘으면 그 채널 구독자 수보다 많은 사람이 그 영상을 봤다는 뜻입니다. "
              "채널 힘이 아니라 주제 힘으로 나온 조회수라 후발 주자에게 자리가 있습니다.",
              "- 경쟁 밀도가 높으면 큰 채널이 이미 자리를 잡았다는 뜻이라 점수에서 감점됩니다. "
              "수요가 크면 대상을 좁혀 진입합니다.",
              "- 제목 초안은 상위 영상들이 실제로 쓰는 형식을 조합한 출발점입니다. 확정본은 편집자가 씁니다.", ""]
    return "\n".join(lines)


def _summary_table(topics: list[dict]) -> list[str]:
    rows = ["| 순위 | 주제 | 점수 | 조회수 중앙값 | 하루 조회수(상위 25%) | 최고 구독자 대비 | 90일 이내 비중 |",
            "|---|---|---|---|---|---|---|"]
    for rank, topic in enumerate(topics, start=1):
        rows.append(f"| {rank} | {topic['topic']} | {topic['score']} | {topic['median_views']:,} | "
                    f"{topic['p75_velocity_per_day']:,.0f} | {topic['max_sub_multiple']}배 | "
                    f"{topic['fresh_ratio_90d']:.0%} |")
    return rows + [""]


def _own_section(baseline: dict) -> list[str]:
    lines = ["## 자사 채널 기준선", "",
             f"최근 180일 상위 {baseline['n']}개 영상의 조회수 중앙값 {baseline['median']:,}회, "
             f"최고 {baseline['max']:,}회입니다. 후보 주제의 수요를 이 값과 비교해서 읽습니다.", "",
             "| 자사 상위 영상 | 조회수 | 평균 시청 | 공개일 |", "|---|---|---|---|"]
    for row in baseline["rows"]:
        lines.append(f"| {row['title']} | {int(row['views']):,} | "
                     f"{row['average_view_seconds']}초 | {row['published_at']} |")
    return lines + [""]


def _topic_block(rank: int, topic: dict, members: list[dict], baseline: dict) -> list[str]:
    pattern = title_patterns(members)
    lines = [f"## {rank}. {topic['topic']} (점수 {topic['score']})", "",
             f"- 수요: 영상 {topic['n_videos']}건, 조회수 중앙값 {topic['median_views']:,}회, "
             f"최고 {topic['max_views']:,}회",
             f"- 속도: 상위 25% 영상이 하루 {topic['p75_velocity_per_day']:,.0f}회, "
             f"최고 하루 {topic['max_velocity_per_day']:,.0f}회",
             f"- 레버리지: 구독자 대비 최고 {topic['max_sub_multiple']}배, "
             f"중앙값 {topic['median_sub_multiple']}배",
             f"- 신선도: 90일 이내 영상 비중 {topic['fresh_ratio_90d']:.0%}",
             f"- 경쟁: 10만 구독자 이상 채널 {topic['n_big_channels']}개 "
             f"(상위 결과의 {topic['big_channel_ratio']:.0%})"]
    if baseline.get("median") and topic["median_views"]:
        lines.append(f"- 자사 기준선 대비: 조회수 중앙값이 {topic['median_views'] / baseline['median']:.2f}배")
    lines += ["", "왜 이 주제인가", ""]
    lines += [f"{line}" for line in angle_lines(topic, members)]
    lines += ["", "근거 영상", ""]
    for video in members[:3]:
        detail = [f"조회 {video['view_count']:,}회"]
        if video["velocity"] is not None:
            detail.append(f"하루 {video['velocity']:,.0f}회")
        if video["sub_multiple"] is not None:
            detail.append(f"구독자 대비 {video['sub_multiple']:.2f}배")
        if video["days_since_upload"] is not None:
            detail.append(f"업로드 {video['days_since_upload']}일 전")
        lines.append(f"- {video['title']} / {video['channel']} "
                     f"({video['subscribers']:,} 구독) / {', '.join(detail)} / {video['url']}")
    if pattern.get("n"):
        draft, gap = draft_titles(topic, pattern)
        lines += ["", f"제목 패턴: 상위 {pattern['n']}개 중 숫자 포함 {pattern['number']}개, "
                      f"시간 표현 {pattern['time']}개, 질문형 {pattern['question']}개, "
                      f"제목 평균 {pattern['avg_len']}자, 영상 길이 중앙값 {pattern['median_minutes']}분"
                      + (f", 자주 쓰는 형식어 {', '.join(pattern['words'])}" if pattern["words"] else ""),
                  "", f"제목 초안: {draft}", "", f"각도: {gap}"]
    return lines + [""]
