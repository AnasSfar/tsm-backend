import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Nav from "../components/Nav";
import LiveRefreshStatus from "../components/LiveRefreshStatus";
import { getNumberOnesRankingLeaderboard, submitNumberOnesRanking } from "../api/client";
import { useAdminSession } from "../utils/adminSession";
import { DEFAULT_THEME_MODE, resolveAlbumThemeMode } from "../utils/anniversaries";
import { useLeaderboardVisibility } from "../utils/leaderboardVisibility";
import { useStore } from "../store/useStore";
import "../styles/GamesPage.css";

// Taylor Swift's Billboard Hot 100 #1 hits.
const NUMBER_ONES = [
  { title: "We Are Never Ever Getting Back Together", album: "Red", cover: "/covers/red.webp" },
  { title: "Shake It Off", album: "1989", cover: "/covers/1989.webp" },
  { title: "Blank Space", album: "1989", cover: "/covers/1989.webp" },
  { title: "Bad Blood", album: "1989", cover: "/covers/1989.webp" },
  { title: "Look What You Made Me Do", album: "reputation", cover: "/covers/reputation.webp" },
  { title: "cardigan", album: "folklore", cover: "/covers/folklore.webp" },
  { title: "willow", album: "evermore", cover: "/covers/evermore.webp" },
  { title: "All Too Well", album: "Red (Taylor's Version)", cover: "/covers/red.webp" },
  { title: "Anti-Hero", album: "Midnights", cover: "/covers/midnights.webp" },
  { title: "Cruel Summer", album: "Lover", cover: "/covers/lover.webp" },
  { title: "Is It Over Now?", album: "1989 (Taylor's Version)", cover: "/covers/1989.webp" },
  {
    title: "Fortnight",
    album: "The Tortured Poets Department",
    cover: "/covers/the tortured poets department.webp",
  },
  {
    title: "The Fate of Ophelia",
    album: "The Life of a Showgirl",
    cover: "/covers/the life of a showgirl the encore.webp",
  },
  {
    title: "Opalite",
    album: "The Life of a Showgirl",
    cover: "/covers/the life of a showgirl the encore.webp",
  },
  {
    title: "I Knew It, I Knew You",
    album: "Toy Story 5",
    cover: "/covers/soundtracks/i-knew-it-i-knew-you.jpg",
  },
  {
    title: "Patient Zero",
    album: "The Life of a Showgirl",
    cover: "/covers/the life of a showgirl the encore.webp",
  },
];

const POLL_INTERVAL_MS = 5 * 60_000;
const PARTICIPANT_STORAGE_KEY = "tsm:number-ones-ranking:participant-id";
const POINT_SCHEME = "weighted-v1";
const POINTS_BY_RANK = [40, 32, 26, 21, 17, 14, 12, 10, 8, 6, 5, 4, 3, 2, 1, 0];
const BATTLE_RESOLVE_DELAY_MS = 280;
const WINNER_REVEAL_SETTLE_MS = 850;
const THEME_REVEAL_DELAY_MS = 520;
const CONFETTI_DELAY_MS = 180;
const SKIPPED_BATTLE = "__skipped__";

function getTrackThemeKey(track) {
  if (!track?.album) return null;
  return (
    resolveAlbumThemeMode(track.album) ||
    (track.album === "The Life of a Showgirl" ? DEFAULT_THEME_MODE : null)
  );
}

function getThemeLabel(track) {
  if (!track?.album) return "";
  return track.album === "The Life of a Showgirl" ? "Showgirl" : track.album;
}

function getConfettiPieces(track) {
  const colorsByTheme = {
    "theme-taylor-swift": ["#5b8db8", "#d9ecdb", "#f7f1d5"],
    "theme-fearless": ["#c4a255", "#f3dc91", "#fff8d6"],
    "theme-speak-now": ["#7c3aed", "#c084fc", "#f0d9ff"],
    "theme-red": ["#b91c1c", "#ef4444", "#f8d7d7"],
    "theme-1989": ["#90aec4", "#d7edf7", "#f7f1df"],
    "theme-reputation": ["#1f2937", "#555555", "#d1d5db"],
    "theme-lover": ["#d978a0", "#f8b1cf", "#9ccae2"],
    "theme-folklore": ["#8b9eb7", "#d1d5db", "#f5f5f4"],
    "theme-evermore": ["#a07850", "#d6a66b", "#ead7b7"],
    "theme-midnights": ["#1a1a3e", "#6366f1", "#c4b5fd"],
    "theme-ttpd": ["#c8bfb8", "#8b827c", "#f4eee8"],
    "theme-showgirl": ["#f97316", "#facc15", "#ec4899"],
  };
  const colors = colorsByTheme[getTrackThemeKey(track)] || colorsByTheme[DEFAULT_THEME_MODE];
  return Array.from({ length: 34 }, (_, index) => ({
    color: colors[index % colors.length],
    left: `${(index * 29) % 100}%`,
    delay: `${(index % 9) * 0.07}s`,
    duration: `${1.7 + (index % 5) * 0.16}s`,
    size: `${7 + (index % 4) * 3}px`,
    drift: `${((index % 7) - 3) * 18}px`,
    rotate: `${90 + (index % 6) * 42}deg`,
  }));
}

function getParticipantId() {
  if (typeof window === "undefined") return "";

  try {
    const existing = window.localStorage.getItem(PARTICIPANT_STORAGE_KEY);
    if (existing) return existing;

    const id =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    window.localStorage.setItem(PARTICIPANT_STORAGE_KEY, id);
    return id;
  } catch {
    return "";
  }
}

function shuffleItems(items) {
  return [...items].sort(() => Math.random() - 0.5);
}

function formatLeaderboardPoints(value) {
  const points = Number(value);
  if (!Number.isFinite(points)) return 0;
  return Math.round(points).toLocaleString("en-US");
}

function createSorter() {
  return advanceSorter({
    pendingGroups: shuffleItems(NUMBER_ONES).map((track) => [track.title]),
    nextGroups: [],
    leftGroup: [],
    rightGroup: [],
    mergedGroup: [],
    currentBattle: null,
    battleCount: 0,
    tiedPairs: [],
    seenBattles: [],
    result: [],
    finished: false,
  });
}

function advanceSorter(sorter) {
  const next = {
    ...sorter,
    pendingGroups: [...sorter.pendingGroups],
    nextGroups: [...sorter.nextGroups],
    leftGroup: [...sorter.leftGroup],
    rightGroup: [...sorter.rightGroup],
    mergedGroup: [...sorter.mergedGroup],
    seenBattles: sorter.seenBattles || [],
  };

  while (!next.currentBattle && !next.finished) {
    if (!next.leftGroup.length || !next.rightGroup.length) {
      if (next.mergedGroup.length || next.leftGroup.length || next.rightGroup.length) {
        next.nextGroups.push([...next.mergedGroup, ...next.leftGroup, ...next.rightGroup]);
        next.leftGroup = [];
        next.rightGroup = [];
        next.mergedGroup = [];
        continue;
      }

      if (next.pendingGroups.length === 0) {
        if (next.nextGroups.length === 1) {
          next.result = next.nextGroups[0];
          next.finished = true;
          break;
        }

        next.pendingGroups = next.nextGroups;
        next.nextGroups = [];
        continue;
      }

      if (next.pendingGroups.length === 1) {
        next.nextGroups.push(next.pendingGroups.shift());
        continue;
      }

      next.leftGroup = next.pendingGroups.shift();
      next.rightGroup = next.pendingGroups.shift();
      next.mergedGroup = [];
    }

    const left = next.leftGroup[0];
    const right = next.rightGroup[0];
    const battleKey = [left, right].sort().join("\0");
    const previous = next.seenBattles.find((b) => b.key === battleKey);
    if (previous) {
      // Replay the user's original answer
      if (previous.winner === left) {
        next.mergedGroup.push(next.leftGroup.shift());
      } else if (previous.winner === right) {
        next.mergedGroup.push(next.rightGroup.shift());
      } else if (previous.winner === SKIPPED_BATTLE) {
        next.mergedGroup.push(next.leftGroup.shift(), next.rightGroup.shift());
      } else {
        next.tiedPairs = [...next.tiedPairs, [left, right]];
        next.mergedGroup.push(next.leftGroup.shift(), next.rightGroup.shift());
      }
      continue;
    }
    next.currentBattle = [left, right];
  }

  return next;
}

function getRankedTracks(sorter) {
  const orderedTitles = sorter.finished ? sorter.result : NUMBER_ONES.map((track) => track.title);
  const tiedPairs = sorter.tiedPairs || [];

  const parent = Object.fromEntries(orderedTitles.map((title) => [title, title]));
  const find = (title) => {
    if (parent[title] !== title) parent[title] = find(parent[title]);
    return parent[title];
  };

  for (const [a, b] of tiedPairs) {
    if (a in parent && b in parent) parent[find(a)] = find(b);
  }

  const rankMap = {};
  const pointsMap = {};
  let currentRank = 1;
  const seen = new Set();

  for (const title of orderedTitles) {
    const root = find(title);
    if (seen.has(root)) continue;
    seen.add(root);
    const group = orderedTitles.filter((item) => find(item) === root);
    const groupPoints = group.map((_, index) => POINTS_BY_RANK[currentRank + index - 1] ?? 0);
    const points =
      Math.round((groupPoints.reduce((total, value) => total + value, 0) / groupPoints.length) * 10) / 10;
    for (const item of group) {
      rankMap[item] = currentRank;
      pointsMap[item] = points;
    }
    currentRank += group.length;
  }

  return orderedTitles.map((title) => ({
    ...NUMBER_ONES.find((track) => track.title === title),
    rank: rankMap[title],
    points: pointsMap[title],
  }));
}

function TrackChoice({ track, onChoose, selected, dimmed, disabled }) {
  return (
    <button
      type="button"
      className={`track13-choice${selected ? " is-selected" : ""}${dimmed ? " is-dimmed" : ""}`}
      onClick={() => onChoose(track.title)}
      disabled={disabled}
      aria-label={`Choose ${track.title}`}
    >
      <img src={track.cover} alt="" decoding="async" />
      <span>{track.title}</span>
      <small>{track.album}</small>
    </button>
  );
}

export default function NumberOnesRanking() {
  const isAdmin = useAdminSession();
  const setUserTheme = useStore((state) => state.setUserTheme);
  const { leaderboards, frozen, loading: visibilityLoading } = useLeaderboardVisibility();
  const isFrozen = Boolean(frozen.number_ones_ranking);
  const showLeaderboard = isAdmin || (!visibilityLoading && leaderboards.number_ones_ranking);
  const [sorter, setSorter] = useState(createSorter);
  const [leaderboard, setLeaderboard] = useState([]);
  const [lbLoading, setLbLoading] = useState(true);
  const [lbError, setLbError] = useState(false);
  const [lbUpdatedAt, setLbUpdatedAt] = useState(null);
  const [lbRefreshing, setLbRefreshing] = useState(false);
  const [selectedTitle, setSelectedTitle] = useState(null);
  const [resultsExpanded, setResultsExpanded] = useState(false);
  const [themeRevealActive, setThemeRevealActive] = useState(false);
  const [confettiTrack, setConfettiTrack] = useState(null);
  const intervalRef = useRef(null);
  const submittedRef = useRef(false);
  const themeAppliedForRef = useRef("");
  const participantIdRef = useRef(getParticipantId());

  const currentTracks = useMemo(() => {
    if (!sorter.currentBattle) return [];
    return sorter.currentBattle.map((title) =>
      NUMBER_ONES.find((track) => track.title === title)
    );
  }, [sorter.currentBattle]);

  const rankedTracks = useMemo(() => getRankedTracks(sorter), [sorter]);
  const topTrack = sorter.finished
    ? rankedTracks.find((track) => track.rank === 1) || rankedTracks[0]
    : null;
  const progress = sorter.finished ? 100 : Math.min(95, (sorter.battleCount / 49) * 100);

  const fetchLeaderboard = useCallback(async ({ silent = false, fresh = false } = {}) => {
    if (silent) setLbRefreshing(true);
    try {
      const data = await getNumberOnesRankingLeaderboard({ fresh });
      setLeaderboard(Array.isArray(data) ? data : []);
      setLbError(false);
      setLbUpdatedAt(Date.now());
    } catch {
      setLbError(true);
    } finally {
      if (!silent) setLbLoading(false);
      setLbRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (visibilityLoading) return undefined;
    if (!showLeaderboard) {
      setLbLoading(false);
      return undefined;
    }
    let cancelled = false;

    const load = () => {
      if (!cancelled) fetchLeaderboard();
    };

    load();
    intervalRef.current = setInterval(() => {
      if (!cancelled) fetchLeaderboard({ silent: true });
    }, POLL_INTERVAL_MS);

    const onVisibility = () => {
      if (!cancelled && document.visibilityState === "visible") {
        fetchLeaderboard({ silent: true, fresh: true });
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelled = true;
      clearInterval(intervalRef.current);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [fetchLeaderboard, showLeaderboard, visibilityLoading]);

  useEffect(() => {
    if (!sorter.finished || submittedRef.current) return;
    submittedRef.current = true;

    const payload = rankedTracks.map(({ title, rank, points }) => ({
      title,
      rank,
      points,
    }));
    submitNumberOnesRanking(payload, participantIdRef.current, POINT_SCHEME)
      .then(() => {
        if (showLeaderboard) fetchLeaderboard({ silent: true, fresh: true });
      })
      .catch(() => {});
  }, [sorter.finished, rankedTracks, showLeaderboard, fetchLeaderboard]);

  useEffect(() => {
    if (!sorter.finished) return;

    if (!topTrack?.album) return;
    if (themeAppliedForRef.current === topTrack.title) return;
    themeAppliedForRef.current = topTrack.title;

    const topTrackTheme = getTrackThemeKey(topTrack);
    setResultsExpanded(false);
    setConfettiTrack(null);
    setThemeRevealActive(false);

    const washTimer = window.setTimeout(() => {
      setThemeRevealActive(true);
    }, WINNER_REVEAL_SETTLE_MS);
    const themeTimer = window.setTimeout(() => {
      if (topTrackTheme) setUserTheme(topTrackTheme);
    }, WINNER_REVEAL_SETTLE_MS + THEME_REVEAL_DELAY_MS);
    const confettiStartTimer = window.setTimeout(() => {
      setConfettiTrack(topTrack);
    }, WINNER_REVEAL_SETTLE_MS + THEME_REVEAL_DELAY_MS + CONFETTI_DELAY_MS);
    const revealTimer = window.setTimeout(() => {
      setThemeRevealActive(false);
    }, WINNER_REVEAL_SETTLE_MS + THEME_REVEAL_DELAY_MS + 980);
    const confettiTimer = window.setTimeout(() => {
      setConfettiTrack(null);
    }, WINNER_REVEAL_SETTLE_MS + THEME_REVEAL_DELAY_MS + CONFETTI_DELAY_MS + 2200);

    return () => {
      window.clearTimeout(washTimer);
      window.clearTimeout(themeTimer);
      window.clearTimeout(confettiStartTimer);
      window.clearTimeout(revealTimer);
      window.clearTimeout(confettiTimer);
    };
  }, [sorter.finished, topTrack, setUserTheme]);

  const recordBattle = (winnerTitle) => {
    if (sorter.finished || !sorter.currentBattle) return;

    setSorter((currentSorter) => {
      const [leftTitle, rightTitle] = currentSorter.currentBattle;
      const battleKey = [leftTitle, rightTitle].sort().join("\0");
      const nextSorter = {
        ...currentSorter,
        leftGroup: [...currentSorter.leftGroup],
        rightGroup: [...currentSorter.rightGroup],
        mergedGroup: [...currentSorter.mergedGroup],
        tiedPairs: [...currentSorter.tiedPairs],
        seenBattles: [...(currentSorter.seenBattles || []), { key: battleKey, winner: winnerTitle }],
        currentBattle: null,
        battleCount: currentSorter.battleCount + 1,
      };

      if (winnerTitle === leftTitle) {
        nextSorter.mergedGroup.push(nextSorter.leftGroup.shift());
      } else if (winnerTitle === rightTitle) {
        nextSorter.mergedGroup.push(nextSorter.rightGroup.shift());
      } else if (winnerTitle === SKIPPED_BATTLE) {
        nextSorter.mergedGroup.push(nextSorter.leftGroup.shift(), nextSorter.rightGroup.shift());
      } else {
        nextSorter.tiedPairs.push([leftTitle, rightTitle]);
        nextSorter.mergedGroup.push(nextSorter.leftGroup.shift(), nextSorter.rightGroup.shift());
      }

      return advanceSorter(nextSorter);
    });
  };

  const restart = () => {
    submittedRef.current = false;
    themeAppliedForRef.current = "";
    setSelectedTitle(null);
    setResultsExpanded(false);
    setThemeRevealActive(false);
    setConfettiTrack(null);
    setSorter(createSorter());
  };

  const chooseBattle = (winnerTitle) => {
    if (sorter.finished || !sorter.currentBattle || selectedTitle !== null) return;

    setSelectedTitle(winnerTitle || "tie");
    window.setTimeout(() => {
      recordBattle(winnerTitle);
      setSelectedTitle(null);
    }, BATTLE_RESOLVE_DELAY_MS);
  };

  return (
    <>
      <Nav />
      <main className={`page games-page swift-day-page track1-ranking-page number-ones-ranking-page${themeRevealActive ? " is-theme-revealing" : ""}`}>
        {themeRevealActive && topTrack && (
          <div className="track1-theme-wash" aria-hidden="true">
            <img src={topTrack.cover} alt="" />
          </div>
        )}
        {confettiTrack && (
          <div className="track1-confetti" aria-hidden="true">
            {getConfettiPieces(confettiTrack).map((piece, index) => (
              <span
                key={index}
                style={{
                  "--confetti-color": piece.color,
                  "--confetti-left": piece.left,
                  "--confetti-delay": piece.delay,
                  "--confetti-duration": piece.duration,
                  "--confetti-size": piece.size,
                  "--confetti-drift": piece.drift,
                  "--confetti-rotate": piece.rotate,
                }}
              />
            ))}
          </div>
        )}
        <header className="games-header swift-day-header">
          <p className="games-eyebrow">Billboard Hot 100</p>
          <h1 className="games-title">Hot 100 #1s Ranking</h1>
          <p className="games-subtitle">
            Pick your favorite in each battle to rank all of Taylor Swift's #1 songs on the Hot 100.
          </p>
          <Link to="/games" className="games-special-link games-special-link--secondary">
            Back to Games
          </Link>
        </header>

        <section className="swift-day-game">
          <div className="swift-day-game-head swift-day-game-head--centered">
            <p className="games-eyebrow">{sorter.finished ? "Results" : "Battle"}</p>
            <h2>Taylor Swift Hot 100 #1s</h2>
          </div>

          <div className="swift-day-progress" aria-hidden="true">
            <span style={{ width: `${progress}%` }} />
          </div>

          {sorter.finished ? (
            <>
              {!resultsExpanded && topTrack ? (
                <section className="track1-winner-reveal" aria-live="polite">
                  <p className="games-eyebrow">Your #1 Hit</p>
                  <img src={topTrack.cover} alt="" decoding="async" />
                  <h3>{topTrack.title}</h3>
                  <p>{getThemeLabel(topTrack)}</p>
                  <button
                    type="button"
                    onClick={() => setResultsExpanded(true)}
                    className="swift-day-next-button"
                  >
                    Reveal full ranking
                  </button>
                </section>
              ) : (
                <div className="track13-results-table track1-results-table">
                  <div className="track13-results-head">
                    <span>Rank</span>
                    <span>Song</span>
                    <span>Points</span>
                  </div>
                  {rankedTracks.map((track) => (
                    <div className="track13-results-row" key={track.title}>
                      <strong>{track.rank}</strong>
                      <div className="track13-results-song">
                        <img src={track.cover} alt="" loading="lazy" decoding="async" />
                        <span>{track.title}</span>
                      </div>
                      <span>{track.points}</span>
                    </div>
                  ))}
                </div>
              )}

              <button type="button" onClick={restart} className="swift-day-next-button">
                Play again
              </button>
            </>
          ) : (
            <>
              <div className="track13-battle">
                {currentTracks.map((track) => (
                  <TrackChoice
                    key={track.title}
                    track={track}
                    onChoose={chooseBattle}
                    selected={selectedTitle === track.title}
                    dimmed={
                      selectedTitle !== null &&
                      selectedTitle !== track.title &&
                      selectedTitle !== "tie" &&
                      selectedTitle !== SKIPPED_BATTLE
                    }
                    disabled={selectedTitle !== null}
                  />
                ))}
              </div>

              <div className="track13-neutral-actions">
                <button
                  type="button"
                  onClick={() => chooseBattle(null)}
                  disabled={selectedTitle !== null}
                  className={selectedTitle === "tie" ? "is-selected" : ""}
                >
                  Tie
                </button>
                <button
                  type="button"
                  onClick={() => chooseBattle(SKIPPED_BATTLE)}
                  disabled={selectedTitle !== null}
                  className={selectedTitle === SKIPPED_BATTLE ? "is-selected" : ""}
                >
                  Skip
                </button>
              </div>
            </>
          )}
        </section>

        {showLeaderboard && (
        <section className="swift-day-leaderboard-section swift-day-leaderboard-section--full">
          <header className="swift-day-leaderboard-section-header">
            <h2 className="swift-day-leaderboard-section-title">
              {isFrozen ? "Final" : "Live"} Hot 100 #1s Leaderboard
              {leaderboard.length > 0 && (
                <span className="swift-day-lb-participants">
                  {leaderboard[0].entries} participant{leaderboard[0].entries === 1 ? "" : "s"}
                </span>
              )}
            </h2>
            <div className="swift-day-live-tools">
              <span className="swift-day-live-badge" aria-label="Live, updates every 5 minutes and on tab focus">
                <span className="swift-day-live-dot" aria-hidden="true" />
                Live
              </span>
              <LiveRefreshStatus
                updatedAt={lbUpdatedAt}
                refreshing={lbRefreshing}
                onRefresh={() => fetchLeaderboard({ silent: true, fresh: true })}
                label="Refresh Hot 100 #1s leaderboard"
              />
            </div>
            {isFrozen && <span className="swift-day-frozen-badge">Frozen</span>}
          </header>

          {isFrozen && (
            <p className="swift-day-lb-state">
              This leaderboard is frozen. New rankings are no longer counted.
            </p>
          )}

          {(visibilityLoading || lbLoading) && <p className="swift-day-lb-state">Loading...</p>}

          {!lbLoading && lbError && (
            <p className="swift-day-lb-state swift-day-lb-state--error">
              Leaderboard unavailable right now.
            </p>
          )}

          {!lbLoading && !lbError && leaderboard.length === 0 && (
            <p className="swift-day-lb-state">No rankings yet - finish the game to be the first.</p>
          )}

          {!lbLoading && !lbError && leaderboard.length > 0 && (
            <div className="swift-day-song-lb">
              {leaderboard.map((track, index) => {
                const trackDetails = NUMBER_ONES.find((item) => item.title === track.title);
                return (
                  <div key={track.title} className="swift-day-song-row">
                    <span className="swift-day-song-rank">{index + 1}</span>
                    <DeltaBadge delta={track.delta} />
                    <img
                      src={track.cover}
                      alt={trackDetails?.album || track.title}
                      className="swift-day-song-cover"
                      loading="lazy"
                      decoding="async"
                    />
                    <div className="swift-day-song-meta">
                      <strong>{track.title}</strong>
                      <small>{trackDetails?.album || "Hot 100 #1"}</small>
                    </div>
                    <span className="swift-day-song-pts">
                      {formatLeaderboardPoints(track.total_points)} pts
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </section>
        )}
      </main>
    </>
  );
}

function DeltaBadge({ delta }) {
  if (delta === "new") {
    return <span className="swift-day-delta swift-day-delta--new">NEW</span>;
  }
  if (delta === null || delta === undefined || delta === 0) {
    return <span className="swift-day-delta swift-day-delta--flat">-</span>;
  }
  if (delta > 0) {
    return <span className="swift-day-delta swift-day-delta--up">+{delta}</span>;
  }
  return <span className="swift-day-delta swift-day-delta--down">-{Math.abs(delta)}</span>;
}
