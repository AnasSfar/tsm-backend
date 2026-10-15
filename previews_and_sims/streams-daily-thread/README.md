# Daily Spotify Streams thread

The normal finalize pipeline publishes one native X thread in this order:

1. Daily recap (root).
2. Top 20 songs (reply).
3. Top eras (reply).
4. Top 10 daily percentage gainers (reply).
5. Top 10 weekly percentage gainers (reply).
6. One grouped reply for secondary song records, when present (up to four images).
7. Qualifying Best Day Club era recaps, as replies after the global recap.

Era recaps reuse the existing eligibility rule (at least three qualifying
records), their verified album covers and era colors. Their existing slugged
locks and the thread receipt are written only after successful publication.
Covered ordinary individual records are suppressed; high-volume year records
can still use their remarkable individual slots. Early standalone era posts
remain disabled. A previously published thread missing new era replies remains
pending until its X URL is available; a second root is never created to fill it.

The record reply now uses the Spotify playlist design **The Best Day Club**,
with a horizontal cover/sidebar and track list, like the Gainers/Watch cards.
Exact record dates, daily streams and daily/weekly comparisons stay visible.
Version, feature and vault annotations use the album line to keep titles compact.
Twelve rows per image is the pagination target; pagination keeps at least ten
rows per image when at least ten records exist. All rows
remain present across at most four attachments in the same reply. The renderer
limits each PNG to 4096px and the real twelve-row example uses one attachment.
The horizontal image was inspected at 310px; nothing is clipped, while the
secondary metadata remains small at timeline size.

No empty gainers post is invented when no song qualifies. The full thread is
prepared before publishing. Active history must be complete for the target,
previous day and seven-day comparison. An invalid required value blocks the
thread; it does not silently disappear from the published data.

Individual song records are reserved for high-volume year records, gaps of
at least 90 days, or a verified editorial score of at least 90. Early and final
lanes share three individual song slots. Other exact records are grouped. Early
per-era recap posts and separate weekend daily-gainer song cards are disabled.
Albums, milestones and overtakes keep their existing posting rules.

Each component retains its existing lock filename, so legacy posting scripts
do not publish the same components again after the new thread succeeds. The
thread receipt records the evaluated components, including empty gainer lists.
No success lock is written before X confirmation. If confirmation is missing,
the pending receipt blocks blind retries; check X before resolving it. A legacy
recap already posted without its replies requires its X URL before any attachment
can be added; this implementation will not silently create a second root.

`--skip` component flags remain supported by finalize. An explicit
`--best-day-last` defers the grouped records and individual record work until
the end, as requested by that flag; the five daily recap components remain one thread.

## Verification

```powershell
python -B previews_and_sims/streams-daily-thread/test_thread.py
python -B collectors/spotify/streams/tools/scripts/post_daily_streams_thread.py 2026-10-06 --no-post --output-dir previews_and_sims/streams-daily-thread/2026-10-06
```

The real-data preview writes only to the specified output directory, with no X
publication or snapshot lock writes. All five existing image templates were
rendered and inspected at 310px width. Nothing runs outside the bitmap bounds;
the existing tables' fine text remains small in a phone timeline. This change
groups publications and does not redesign those templates.
