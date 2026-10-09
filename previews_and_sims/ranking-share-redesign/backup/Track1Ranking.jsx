import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Nav from "../components/Nav";
import LiveRefreshStatus from "../components/LiveRefreshStatus";
import {
  getTrack1RankingLeaderboard,
  getTrack1RankingNextPoll,
  submitTrack1Ranking,
  submitTrack1RankingNextPoll,
} from "../api/client";
import { useAdminSession } from "../utils/adminSession";
import { DEFAULT_THEME_MODE, resolveAlbumThemeMode } from "../utils/anniversaries";
import { useLeaderboardVisibility } from "../utils/leaderboardVisibility";
import { useStore } from "../store/useStore";
import "../styles/GamesPage.css";

const TAYLOR_TRACK_ONES = [
  { title: "Tim McGraw", album: "Taylor Swift", year: "2006", cover: "/covers/taylor swift.webp" },
  { title: "Fearless", album: "Fearless", year: "2008 / 2021", cover: "/covers/fearless.webp" },
  { title: "Mine", album: "Speak Now", year: "2010 / 2023", cover: "/covers/speak now.webp" },
  { title: "State of Grace", album: "Red", year: "2012 / 2021", cover: "/covers/red.webp" },
  { title: "Welcome To New York", album: "1989", year: "2014 / 2023", cover: "/covers/1989.webp" },
  { title: "...Ready For It?", album: "reputation", year: "2017", cover: "/covers/reputation.webp" },
  { title: "I Forgot That You Existed", album: "Lover", year: "2019", cover: "/covers/lover.webp" },
  { title: "the 1", album: "folklore", year: "2020", cover: "/covers/folklore.webp" },
  { title: "willow", album: "evermore", year: "2020", cover: "/covers/evermore.webp" },
  { title: "Lavender Haze", album: "Midnights", year: "2022", cover: "/covers/midnights.webp" },
  {
    title: "Fortnight",
    album: "The Tortured Poets Department",
    year: "2024",
    cover: "/covers/the tortured poets department.webp",
  },
  {
    title: "The Fate of Ophelia",
    album: "The Life of a Showgirl",
    year: "2025",
    cover: "/covers/the life of a showgirl the encore.webp",
  },
];

const POLL_INTERVAL_MS = 5 * 60_000;
const PARTICIPANT_STORAGE_KEY = "tsm:track1-ranking:participant-id";
const POINT_SCHEME = "weighted-v1";
const POINTS_BY_RANK = [30, 24, 19, 15, 12, 9, 7, 5, 3, 2, 1, 0];
const NEXT_RANKING_OPTIONS = [
  {
    label: "Rank by album",
    detail: "in the order of this ranking",
  },
  {
    label: "Rank by track number",
    detail: "track 1s, track 2s ...",
  },
  {
    label: "Rank by track genre",
    detail: "lead single, single, collab, title track ...",
  },
];
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
    pendingGroups: shuffleItems(TAYLOR_TRACK_ONES).map((track) => [track.title]),
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
  const orderedTitles = sorter.finished ? sorter.result : TAYLOR_TRACK_ONES.map((track) => track.title);
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
    ...TAYLOR_TRACK_ONES.find((track) => track.title === title),
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

export default function Track1Ranking() {
  const isAdmin = useAdminSession();
  const setUserTheme = useStore((state) => state.setUserTheme);
  const { leaderboards, frozen, loading: visibilityLoading } = useLeaderboardVisibility();
  const isFrozen = Boolean(frozen.track1_ranking);
  const showLeaderboard = isAdmin || (!visibilityLoading && leaderboards.track1_ranking);
  const [sorter, setSorter] = useState(createSorter);
  const [leaderboard, setLeaderboard] = useState([]);
  const [lbLoading, setLbLoading] = useState(true);
  const [lbError, setLbError] = useState(false);
  const [lbUpdatedAt, setLbUpdatedAt] = useState(null);
  const [lbRefreshing, setLbRefreshing] = useState(false);
  const [nextRankingPoll, setNextRankingPoll] = useState(null);
  const [voteLoading, setVoteLoading] = useState(true);
  const [voteError, setVoteError] = useState(false);
  const [voteSubmitting, setVoteSubmitting] = useState(false);
  const [selectedTitle, setSelectedTitle] = useState(null);
  const [resultsExpanded, setResultsExpanded] = useState(false);
  const [themeRevealActive, setThemeRevealActive] = useState(false);
  const [confettiTrack, setConfettiTrack] = useState(null);
  const intervalRef = useRef(null);
  const submittedRef = useRef(false);
  const themeAppliedForRef = useRef("");
  const participantIdRef = useRef(getParticipantId());
  const voteRequestRef = useRef(0);

  const currentTracks = useMemo(() => {
    if (!sorter.currentBattle) return [];
    return sorter.currentBattle.map((title) =>
      TAYLOR_TRACK_ONES.find((track) => track.title === title)
    );
  }, [sorter.currentBattle]);

  const rankedTracks = useMemo(() => getRankedTracks(sorter), [sorter]);
  const topTrack = sorter.finished
    ? rankedTracks.find((track) => track.rank === 1) || rankedTracks[0]
    : null;
  const progress = sorter.finished ? 100 : Math.min(95, (sorter.battleCount / 44) * 100);

  const fetchLeaderboard = useCallback(async ({ silent = false, fresh = false } = {}) => {
    if (silent) setLbRefreshing(true);
    try {
      const data = await getTrack1RankingLeaderboard({ fresh });
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

  const fetchNextRankingPoll = async ({ silent = false } = {}) => {
    try {
      const data = await getTrack1RankingNextPoll();
      setNextRankingPoll(data && typeof data === "object" ? data : null);
      setVoteError(false);
    } catch {
      setVoteError(true);
    } finally {
      if (!silent) setVoteLoading(false);
    }
  };

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
    fetchNextRankingPoll();
  }, []);

  useEffect(() => {
    if (!sorter.finished || submittedRef.current) return;
    submittedRef.current = true;

    const payload = rankedTracks.map(({ title, rank, points }) => ({
      title,
      rank,
      points,
    }));
    submitTrack1Ranking(payload, participantIdRef.current, POINT_SCHEME)
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

  const buildOptimisticPoll = (option) => {
    const currentOptions = nextRankingPoll?.options?.length
      ? nextRankingPoll.options
      : NEXT_RANKING_OPTIONS.map((option) => ({ label: option.label, votes: 0, percentage: 0 }));
    const previousVote = nextRankingPoll?.my_vote || "";
    const options = currentOptions.map((item) => {
      let votes = item.votes || 0;
      if (item.label === previousVote && previousVote !== option) votes = Math.max(0, votes - 1);
      if (item.label === option && previousVote !== option) votes += 1;
      return { ...item, votes };
    });
    const totalVotes = options.reduce((total, item) => total + (item.votes || 0), 0);

    return {
      ...nextRankingPoll,
      options: options.map((item) => ({
        ...item,
        percentage: totalVotes ? Math.round(((item.votes || 0) / totalVotes) * 1000) / 10 : 0,
      })),
      total_votes: totalVotes,
      my_vote: option,
    };
  };

  const voteForNextRanking = async (option) => {
    const requestId = voteRequestRef.current + 1;
    voteRequestRef.current = requestId;
    const previousPoll = nextRankingPoll;
    setNextRankingPoll(buildOptimisticPoll(option));
    setVoteSubmitting(true);
    try {
      const data = await submitTrack1RankingNextPoll(option);
      if (voteRequestRef.current !== requestId) return;
      setNextRankingPoll(data && typeof data === "object" ? data : null);
      setVoteError(false);
    } catch {
      if (voteRequestRef.current !== requestId) return;
      setNextRankingPoll(previousPoll);
      setVoteError(true);
    } finally {
      if (voteRequestRef.current !== requestId) return;
      setVoteSubmitting(false);
      setVoteLoading(false);
    }
  };

  const nextRankingVotes = nextRankingPoll?.options || [];
  const nextRankingVote = nextRankingPoll?.my_vote || "";
  const totalNextRankingVotes = nextRankingPoll?.total_votes || 0;

  return (
    <>
      <Nav />
      <main className={`page games-page swift-day-page track1-ranking-page${themeRevealActive ? " is-theme-revealing" : ""}`}>
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
          <p className="games-eyebrow">Eras Ranking</p>
          <h1 className="games-title">Track 1 Ranking</h1>
          <p className="games-subtitle">
            Pick your favorite in each battle to build your Taylor Swift track 1 ranking.
          </p>
          <a
            href="https://open.spotify.com/playlist/7pKNEIIXqO6oNLoqxn6ZiQ"
            target="_blank"
            rel="noopener noreferrer"
            className="games-special-link games-special-link--spotify"
          >
            Stream while playing
          </a>
          <Link to="/games" className="games-special-link games-special-link--secondary">
            Back to Games
          </Link>
        </header>

        <section className="swift-day-game">
          <div className="swift-day-game-head swift-day-game-head--centered">
            <p className="games-eyebrow">{sorter.finished ? "Results" : "Battle"}</p>
            <h2>Taylor Swift Track 1s</h2>
          </div>

          <div className="swift-day-progress" aria-hidden="true">
            <span style={{ width: `${progress}%` }} />
          </div>

          {sorter.finished ? (
            <>
              {!resultsExpanded && topTrack ? (
                <section className="track1-winner-reveal" aria-live="polite">
                  <p className="games-eyebrow">Your Track 1 Era</p>
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

        {/* <section className="album-ranking-vote-panel">
          <div className="album-ranking-vote-panel-head">
            <p className="games-eyebrow">Next ranking</p>
            <h2 id="track1-ranking-vote-title">Vote for next ranking</h2>
          </div>

          {voteLoading && <p className="album-ranking-vote-state">Loading votes...</p>}
          {!voteLoading && voteError && (
            <p className="album-ranking-vote-state album-ranking-vote-state--error">
              Vote unavailable right now.
            </p>
          )}

          <div className="album-ranking-vote-options">
            {NEXT_RANKING_OPTIONS.map((option) => (
              <button
                key={option.label}
                type="button"
                className={option.label === nextRankingVote ? "is-selected" : ""}
                aria-pressed={option.label === nextRankingVote}
                disabled={voteSubmitting || voteLoading}
                onClick={() => voteForNextRanking(option.label)}
              >
                <span className="album-ranking-vote-option-copy">
                  <span>{option.label}</span>
                  <small>{option.detail}</small>
                </span>
                <strong>
                  {nextRankingVotes.find((item) => item.label === option.label)?.votes || 0}
                </strong>
              </button>
            ))}
          </div>

          <div className="album-ranking-vote-footer">
            <p className="album-ranking-vote-confirmation">
              {nextRankingVote
                ? `Vote saved: ${nextRankingVote}`
                : "Only your latest choice counts."}
            </p>
            <p className="album-ranking-vote-total">
              {totalNextRankingVotes} vote{totalNextRankingVotes === 1 ? "" : "s"}
            </p>
          </div>
        </section> */}

        {showLeaderboard && (
        <section className="swift-day-leaderboard-section swift-day-leaderboard-section--full">
          <header className="swift-day-leaderboard-section-header">
            <h2 className="swift-day-leaderboard-section-title">
              {isFrozen ? "Final" : "Live"} Track 1 Leaderboard
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
                label="Refresh track 1 leaderboard"
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
                const trackDetails = TAYLOR_TRACK_ONES.find((item) => item.title === track.title);
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
                      <small>{trackDetails?.album || "Track 1"}</small>
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
