# Album card fact selection

`album_page_facts.py` selects at most two complementary facts. Scores are
editorial weights; they never alter source statistics. The card may contain one
or no facts. A background fact never fills the second slot after an event.

Rules:

- Reached song milestones reuse `post_priority.song_milestone_importance`.
  No all-time rank bonus is supplied without an exact rank source.
- An exact release anniversary scores 95. Adjacent days score 35 or less.
- Entering Global top 10 scores 85. Chart entries require a complete previous
  top 200; upward moves of at least five places scale with the number gained.
- A new album high over the target date and 30 preceding complete days scores
  85. Increasing streaks and gains over the 30-day average scale with magnitude.
- A future milestone qualifies within 1% of its exact total. Its score scales
  with proximity, with a bonus for a billion. Only the exact remaining streams
  are displayed; no arrival date or pace forecast is produced.
- Individual gains require at least 5%, at least 1,000 additional streams,
  daily streams above the preceding 30-day average, and 30 complete history
  rows matching today's and yesterday's card values. Score combines relative
  gain, absolute gain and departure from that baseline.
- Era rank gains scale with positions gained; reaching #1 gets a bonus.
- Facts about the same track or topic cannot occupy both slots. Streaks,
  album average gains and all-tracks-up facts share the album-growth topic.
- Events below 35 are omitted. When none qualifies, one background fact of
  at least 25 may appear (for example all songs above a billion, with all names).

Records already shown alongside the daily album total and song rows remain
there; this selection does not repeat their existing best-day-since labels.
Unexpected builder errors propagate rather than silently dropping facts.

Checks: `python -B previews_and_sims/spotify-app-cards/test_album_facts.py`.
Real-data checks on 2026-10-06: 1989 (two complementary facts), folklore and
Midnights (one fact each). The 1989 preview is also rendered at 310 px.
