from __future__ import annotations

import sys
from datetime import date

from .links import charts_url, song_url, streams_latest_url
from .prefixes import BEST_DAY_PREFIX, MOST_STREAMED_SONGS_TITLE, OVERTAKE_PREFIX, STREAMS_PREFIX, THREAD_PREFIX, SPOTIFY_CHART_PREFIX, with_prefix


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th'][n % 10]}"


def date_label(value: str | date, *, include_weekday: bool = False) -> str:
    day = date.fromisoformat(value) if isinstance(value, str) else value
    if include_weekday:
        return f"{day.strftime('%A')}, {day.strftime('%B')} {ordinal(day.day)}, {day.year}"
    day_fmt = "%#d" if sys.platform == "win32" else "%-d"
    return day.strftime(f"%B {day_fmt}, %Y")


def streams_update_tweet(*, top_n: int, stats_date: str) -> str:
    body = (
        f"{MOST_STREAMED_SONGS_TITLE}: top {top_n} "
        f"on {date_label(stats_date, include_weekday=True)}.\n\n"
        f"Full update: {streams_latest_url()}"
    )
    return with_prefix(body, STREAMS_PREFIX)


def song_overtake_tweet(group: dict, stats_date: str) -> str:
    events = group["events"]
    first_track_id = events[0]["overtaker"]["track_id"]
    footer = f"\n\nFull history: {song_url(first_track_id)}"

    def full_line(event: dict) -> str:
        overtaker = event["overtaker"]
        passed = event["passed"]
        rank = int(overtaker["rank"])
        return (
            f'"{overtaker["title"]}" has now surpassed "{passed["title"]}" '
            f"and is now Taylor Swift's {ordinal(rank)} most streamed song ever."
        )

    def compact_line(event: dict) -> str:
        overtaker = event["overtaker"]
        passed = event["passed"]
        rank = int(overtaker["rank"])
        return f'"{overtaker["title"]}" passed "{passed["title"]}" — now #{ordinal(rank)} all-time.'

    def build(lines: list[str]) -> str:
        return with_prefix("\n".join(lines) + footer, OVERTAKE_PREFIX)

    tweet = build([full_line(event) for event in events])
    if len(tweet) <= 280:
        return tweet

    # A bundled group (several overtakes close in rank posted as one tweet)
    # can blow past 280 chars with the full sentence per event — fall back to
    # a compact line per event, then to dropping the tail of the list. The
    # image always shows every overtake regardless of what the tweet text fits.
    compact_lines = [compact_line(event) for event in events]
    tweet = build(compact_lines)
    if len(tweet) <= 280:
        return tweet

    while len(compact_lines) > 1:
        compact_lines = compact_lines[:-1]
        remaining = len(events) - len(compact_lines)
        candidate = build(compact_lines + [f"+{remaining} more overtake(s) — see image."])
        if len(candidate) <= 280:
            return candidate

    return build(compact_lines)

def best_day_since_tweet(
    *,
    title: str,
    label: str,
    daily_streams: int,
    pct: str,
    track_id: str,
    repeat: bool = False,
) -> str:
    verb = "has once again earned" if repeat else "earned"
    body = (
        f'"{title}" {verb} its {label} with {int(daily_streams):,} streams [{pct}].\n\n'
        f"Full history: {song_url(track_id)}"
    )
    return with_prefix(body, BEST_DAY_PREFIX)


def best_day_since_recap_tweet(*, count: int, stats_date: str) -> str:
    plural = "s" if int(count) != 1 else ""
    body = f"{int(count)} song{plural} hit a best day since record on {date_label(stats_date)}. Full recap below."
    return with_prefix(body, BEST_DAY_PREFIX)


def best_day_since_era_recap_tweet(*, era: str, count: int, stats_date: str) -> str:
    plural = "s" if int(count) != 1 else ""
    body = (
        f"{int(count)} {era} song{plural} hit a best day since record on "
        f"{date_label(stats_date)}. Full {era} recap below."
    )
    return with_prefix(body, BEST_DAY_PREFIX)


def best_day_grower_tweet(
    *,
    title: str,
    artist: str,
    lines: list[str],
    label: str,
    prefix: str,
) -> str:
    body = (
        f'"{title}" by {artist} on Spotify :\n\n'
        + "\n".join(lines)
        + f"\n\nThe song once again earned its {label}."
    )
    return with_prefix(body, prefix)


def stream_milestone_tweet(
    *,
    title: str,
    milestone_streams: int,
    milestone_rank: int,
    next_title: str | None = None,
    next_expected_date: str | None = None,
    prefix: str,
    album_title: str | None = None,
    album_milestone_rank: int | None = None,
    album_first: bool = False,
    next_album_title: str | None = None,
    next_album_expected_date: str | None = None,
    album_next: bool = False,
) -> str:
    has_album_context = bool(album_title and album_milestone_rank is not None)
    if album_first and has_album_context:
        milestone_line = (
            f'"{title}" is now the {ordinal(int(album_milestone_rank))} song '
            f"from {album_title} to surpass {int(milestone_streams):,} streams.\n\n"
            f"It is Taylor Swift's {ordinal(int(milestone_rank))} song to do so."
        )
    else:
        milestone_line = (
            f'"{title}" has now surpassed {int(milestone_streams):,} streams.\n\n'
            f"It is Taylor Swift's {ordinal(int(milestone_rank))} song to do so."
        )
        if has_album_context:
            milestone_line += (
                f" The song is also the {ordinal(int(album_milestone_rank))} "
                f"song to do so from {album_title} album."
            )

    if album_next and album_title and next_album_title and next_album_expected_date:
        next_line = (
            f'The next song from {album_title} expected to surpass this milestone is '
            f'"{next_album_title}" on {date_label(next_album_expected_date)}.'
        )
    elif next_title and next_expected_date:
        next_line = (
            f'The next song expected to surpass this milestone is "{next_title}" '
            f"on {date_label(next_expected_date)}."
        )
    else:
        next_line = None

    body = milestone_line if next_line is None else f"{milestone_line}\n\n{next_line}"
    return with_prefix(body, prefix)

def track_history_line(track_id: str) -> str:
    return f"See full track's history here : {song_url(track_id)}"


def full_streams_update_line() -> str:
    return f"See the full update here : {streams_latest_url()}"


def full_charts_update_line(*, region: str = "global", view: str = "today", label: str = "See full update here") -> str:
    return f"{label} : {charts_url(region=region, view=view)}"


def weekend_streams_tweet(*, stats_date: str) -> str:
    when = date_label(stats_date, include_weekday=True)
    body = f"Taylor Swift's albums and songs on the Spotify counter {when}.\n{full_streams_update_line()}"
    return with_prefix(body, STREAMS_PREFIX)


def stream_highlights_combined_tweet(*, stats_date: str) -> str:
    body = f"Taylor Swift's biggest gainers on {date_label(stats_date, include_weekday=True)} - daily & weekly."
    return with_prefix(body, STREAMS_PREFIX)


def stream_gainers_table_tweet(*, period: str, count: int, stats_date: str) -> str:
    period_label = "daily" if period == "daily" else "weekly"
    compare_label = "vs the previous day" if period == "daily" else "vs last week"
    body = (
        f"Taylor Swift's top {int(count)} biggest {period_label} gainers by % "
        f"on {date_label(stats_date, include_weekday=True)} ({compare_label})."
    )
    return with_prefix(body, STREAMS_PREFIX)


def gainer_tweet_body(*, rank: int | None, title: str, period: str, stats_date: str, pct: str, daily_streams: str, gain: str, track_id: str) -> str:
    rank_text = f"#{rank} " if rank is not None else ""
    compare_label = "the previous day" if period == "daily" else "last week"
    return (
        f'{rank_text}"{title}" was one of Taylor Swift\'s biggest {period} gainers by % '
        f"on {date_label(stats_date, include_weekday=True)}.\n\n"
        f"It rose {pct} vs {compare_label}, with {daily_streams} streams (+{gain}).\n\n"
        f"{track_history_line(track_id)}"
    )


def song_gainer_tweet(*, title: str, daily_streams: str, pct: str, stats_date: str, track_id: str, prefix: str, style: str = "bracket") -> str:
    date_phrase = f"on {date_label(stats_date)}"
    if style == "direction":
        clean_pct = str(pct).lstrip("+")
        direction = "up" if not str(pct).startswith("-") else "down"
        body = f'"{title}" earned {daily_streams} streams, {direction} {clean_pct}, {date_phrase}'
    else:
        body = f'"{title}" earned {daily_streams} streams [{pct}] {date_phrase}'
    return with_prefix(f"{body}\n\n{track_history_line(track_id)}", prefix)


def spotify_chart_region_tweet(*, region_name: str, stats_date: str, region: str = "global", comment: str | None = None, prefix: str = SPOTIFY_CHART_PREFIX) -> str:
    body = f"Taylor Swift on Spotify {region_name} Charts on {date_label(stats_date, include_weekday=True)} :"
    if comment:
        body = f"{body}\n\n{comment}"
    return with_prefix(body, prefix)


def thread_intro_tweet(text: str) -> str:
    return with_prefix(text, THREAD_PREFIX)
