#!/usr/bin/env python3
"""Turn collected videos into ranked topic candidates.

Every score keeps its raw numbers in the CSV, so a decision can be traced back
to the videos it came from. Five signals:

  demand      median views of the topic's videos
  velocity    views per day since upload (75th percentile)
  leverage    best views-to-subscriber multiple (small channel, big view count)
  freshness   share of videos uploaded inside the freshness window
  competition how many large channels already own the topic (penalty)

Rows tagged brightdata:mock are excluded by default. Synthetic numbers never
enter a real ranking unless --include-mock is passed on purpose.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics as stats
from collections import defaultdict
from datetime import date
from pathlib import Path

import report

HERE = Path(__file__).resolve().parent
STAMP = date.today().strftime("%Y%m%d")
TOPIC_FIELDS = [
    "topic", "topic_type", "score", "n_videos", "n_enriched", "median_views", "max_views",
    "p75_velocity_per_day", "max_velocity_per_day", "max_sub_multiple", "median_sub_multiple",
    "fresh_ratio_90d", "n_big_channels", "big_channel_ratio", "demand_score", "velocity_score",
    "leverage_score", "freshness_score", "competition_score", "top_video_title",
    "top_video_channel", "top_video_subs", "top_video_views", "top_video_days_since_upload",
    "top_video_url", "evidence_video_ids",
]
PARTICLES = ("으로", "에서", "에게", "까지", "부터", "이랑", "하는", "하기", "하고",
             "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만")
TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z가-힣]+")
HANGUL = re.compile(r"[가-힣]")


def to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def load_rows(path: Path, include_mock: bool) -> tuple[list[dict], int]:
    """Read the collected CSV and attach the two derived per-video ratios."""
    with path.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    mock = [r for r in rows if r["data_origin"].startswith("brightdata:mock")]
    if not include_mock:
        rows = [r for r in rows if not r["data_origin"].startswith("brightdata:mock")]
    for row in rows:
        for key in ("subscribers", "view_count", "like_count", "comment_count", "duration_sec"):
            row[key] = to_int(row[key])
        days = to_int(row["days_since_upload"]) if row["days_since_upload"] else None
        row["days_since_upload"] = days
        row["velocity"] = row["view_count"] / max(days, 1) if days is not None else None
        row["sub_multiple"] = row["view_count"] / row["subscribers"] if row["subscribers"] else None
    return rows, len(mock)


def unique_videos(rows: list[dict]) -> dict[str, dict]:
    """One record per video, preferring the enriched version."""
    best: dict[str, dict] = {}
    for row in rows:
        current = best.get(row["video_id"])
        if current is None or (current["days_since_upload"] is None
                               and row["days_since_upload"] is not None):
            best[row["video_id"]] = row
    return best


def normalise(title: str) -> str:
    return TOKEN_SPLIT.sub(" ", title.lower()).strip()


def strip_particle(token: str) -> str:
    for particle in PARTICLES:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    return token


def is_content_word(token: str, mining: dict, stop: set[str]) -> bool:
    """Keep nouns a person could film, drop grammar and format words.

    Tokens carrying latin letters or digits (mcp, n8n, gpt) are always kept.
    Pure Hangul tokens ending in a predicate ending are verb or adjective
    fragments such as '하는' or '쓰고', which are not topics.
    """
    if len(token) < mining["min_token_len"] or token in stop or token.isdigit():
        return False
    if re.search(r"[0-9a-z]", token):
        return True
    return not (len(token) <= 4 and token[-1] in mining["predicate_endings"])


def mine_phrases(videos: list[dict], cfg: dict) -> dict[str, set[str]]:
    """Unigrams and bigrams that repeat across enough different videos."""
    mining = cfg["phrase_mining"]
    stop = {word.lower() for word in mining["stopwords"]}
    # Generic nouns are not topics on their own ('코드'), but they are half of a
    # real one ('클로드 코드'), so they are blocked as unigrams only.
    alone = {word.lower() for word in mining.get("unigram_blocklist", [])}
    hits: dict[str, set[str]] = defaultdict(set)
    for video in videos:
        tokens = [strip_particle(t) for t in normalise(video["title"]).split()]
        tokens = [t for t in tokens if is_content_word(t, mining, stop)]
        for token in set(tokens):
            if token not in alone:
                hits[token].add(video["video_id"])
        for first, second in zip(tokens, tokens[1:]):
            hits[f"{first} {second}"].add(video["video_id"])
    return {phrase: ids for phrase, ids in hits.items()
            if len(ids) >= (mining["min_videos_bigram"] if " " in phrase
                            else mining["min_videos_unigram"])}


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))]


def _log_ratio(value: float, ceiling: float) -> float:
    return min(1.0, math.log1p(max(value, 0)) / math.log1p(ceiling))


def score_topic(name: str, kind: str, videos: list[dict], cfg: dict) -> dict | None:
    scoring = cfg["scoring"]
    enriched = [v for v in videos if v["days_since_upload"] is not None]
    if (len(videos) < scoring["min_videos_for_topic"]
            or len(enriched) < scoring["min_enriched_for_topic"]):
        return None
    views = [v["view_count"] for v in videos if v["view_count"] > 0] or [0]
    speeds = [v["velocity"] for v in enriched if v["velocity"] is not None]
    multiples = [v["sub_multiple"] for v in enriched if v["sub_multiple"] is not None]
    fresh = [v for v in enriched if v["days_since_upload"] <= scoring["freshness_window_days"]]
    big = {v["channel_id"] for v in enriched if v["subscribers"] >= scoring["big_channel_subscribers"]}
    parts = {
        "demand": _log_ratio(stats.median(views), scoring["demand_views_ceiling"]),
        "velocity": _log_ratio(percentile(speeds, 0.75), scoring["velocity_ceiling_per_day"]),
        "leverage": min(max(multiples, default=0.0), scoring["leverage_multiple_ceiling"])
        / scoring["leverage_multiple_ceiling"],
        "freshness": len(fresh) / len(enriched),
        "competition": 1 - (len(big) / max(len({v["channel_id"] for v in enriched}), 1)),
    }
    weights = scoring["weights"]
    top = max(videos, key=lambda v: v["view_count"])
    return {
        "topic": name, "topic_type": kind,
        "score": round(100 * sum(weights[k] * v for k, v in parts.items()), 1),
        "n_videos": len(videos), "n_enriched": len(enriched),
        "median_views": int(stats.median(views)), "max_views": max(views),
        "p75_velocity_per_day": round(percentile(speeds, 0.75), 1),
        "max_velocity_per_day": round(max(speeds, default=0.0), 1),
        "max_sub_multiple": round(max(multiples, default=0.0), 2),
        "median_sub_multiple": round(stats.median(multiples), 2) if multiples else 0.0,
        "fresh_ratio_90d": round(parts["freshness"], 2),
        "n_big_channels": len(big), "big_channel_ratio": round(1 - parts["competition"], 2),
        **{f"{key}_score": round(value, 3) for key, value in parts.items()},
        "top_video_title": top["title"], "top_video_channel": top["channel"],
        "top_video_subs": top["subscribers"], "top_video_views": top["view_count"],
        "top_video_days_since_upload": top["days_since_upload"]
        if top["days_since_upload"] is not None else "",
        "top_video_url": top["url"],
        "members": [v["video_id"] for v in sorted(videos, key=lambda v: v["view_count"], reverse=True)],
        "evidence_video_ids": " ".join(
            v["video_id"] for v in sorted(videos, key=lambda v: v["view_count"], reverse=True)[:5]),
    }


def deduplicate(topics: list[dict], overlap_limit: float) -> list[dict]:
    """Drop candidates that cover the same videos as a stronger candidate.

    '클로드 코드' and the fragment '코드' describe one topic, not two. Spacing
    variants ('클로드코드') collapse as well. The more specific phrase wins ties
    because it is the one an editor can act on.
    """
    ordered = sorted(topics, key=lambda t: (t["score"], len(t["topic"])), reverse=True)
    kept: list[dict] = []
    seen_names: set[str] = set()
    for topic in ordered:
        name_key = topic["topic"].lower().replace(" ", "")
        if name_key in seen_names:
            continue
        members = set(topic["members"])
        if any(len(members & set(c["members"])) / len(members | set(c["members"])) >= overlap_limit
               for c in kept if members | set(c["members"])):
            continue
        seen_names.add(name_key)
        kept.append(topic)
    return kept


def build_topics(rows: list[dict], cfg: dict) -> list[dict]:
    videos = unique_videos(rows)
    topics: list[dict] = []
    by_keyword: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["source_type"] == "search" and row["source_keyword"]:
            by_keyword[row["source_keyword"]].add(row["video_id"])
    for keyword, ids in by_keyword.items():
        scored = score_topic(keyword, "keyword", [videos[i] for i in ids if i in videos], cfg)
        if scored:
            topics.append(scored)
    if cfg["phrase_mining"]["enabled"]:
        keywords_of: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            if row["source_keyword"]:
                keywords_of[row["video_id"]].add(row["source_keyword"])
        min_queries = cfg["phrase_mining"].get("min_distinct_keywords", 1)
        for phrase, ids in mine_phrases(list(videos.values()), cfg).items():
            # A real topic surfaces under more than one search query.
            if len({k for i in ids for k in keywords_of.get(i, ())}) < min_queries:
                continue
            scored = score_topic(phrase, "phrase", [videos[i] for i in ids if i in videos], cfg)
            if scored:
                topics.append(scored)
    kept = deduplicate(topics, cfg["phrase_mining"].get("dedupe_overlap", 0.6))
    kept.sort(key=lambda t: t["score"], reverse=True)
    return kept[: cfg["phrase_mining"]["max_candidates"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank topic candidates from collected videos")
    parser.add_argument("--config", default=str(HERE / "radar_config.json"))
    parser.add_argument("--outdir", default=str(HERE / "results"))
    parser.add_argument("--videos", default="")
    parser.add_argument("--include-mock", action="store_true")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    outdir = Path(args.outdir)
    videos_csv = Path(args.videos) if args.videos else outdir / f"videos_{STAMP}.csv"
    rows, mock_count = load_rows(videos_csv, args.include_mock)
    print(f"loaded {len(rows)} row(s) from {videos_csv.name}, "
          f"mock rows {'included' if args.include_mock else 'excluded'}: {mock_count}")

    # QJC publishes in Korean, so the topics that matter are the ones competing
    # on Korean-language YouTube. Foreign-language hits stay in the raw CSV.
    non_korean = 0
    if cfg["scoring"].get("korean_titles_only"):
        keep = [r for r in rows if HANGUL.search(r["title"])]
        non_korean = len(rows) - len(keep)
        rows = keep
        print(f"korean_titles_only: dropped {non_korean} row(s) without Hangul in the title")

    topics = build_topics(rows, cfg)
    print(f"scored {len(topics)} topic candidate(s) after overlap dedupe")
    topics_csv = outdir / f"topics_{STAMP}.csv"
    with topics_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TOPIC_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(topics)
    print(f"wrote {topics_csv.name}")

    counts = {"videos": len({r["video_id"] for r in rows}),
              "keywords": len(cfg["keywords"]), "topics": len(topics),
              "mock_excluded": 0 if args.include_mock else mock_count,
              "non_korean_excluded": non_korean}
    markdown = report.render_shortlist(
        topics, unique_videos(rows),
        report.own_baseline(outdir / f"own_channel_{STAMP}.csv"), counts, args.top, STAMP)
    (outdir / "topic-shortlist.md").write_text(markdown, encoding="utf-8")
    print(f"wrote topic-shortlist.md with top {args.top} candidate(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
