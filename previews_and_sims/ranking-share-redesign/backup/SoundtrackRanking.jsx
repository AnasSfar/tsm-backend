import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import Nav from "../components/Nav";
import {
  getSoundtrackRankingLeaderboard,
  getSoundtrackRankingNextPoll,
  submitSoundtrackRanking,
  submitSoundtrackRankingNextPoll,
} from "../api/client";
import { useAdminSession } from "../utils/adminSession";
import { useLeaderboardVisibility } from "../utils/leaderboardVisibility";
import "../styles/GamesPage.css";

const TAYLOR_SOUNDTRACKS = [
  {
    title: "Crazier",
    year: "Hannah Montana: The Movie - 2009",
    cover: "/covers/soundtracks/crazier.jpg",
  },
  {
    title: "Today Was a Fairytale",
    year: "Valentine's Day - 2010",
    cover: "/covers/soundtracks/today-was-a-fairytale.jpg",
  },
  {
    title: "Safe & Sound",
    year: "The Hunger Games - 2011",
    cover: "/covers/soundtracks/safe-and-sound.jpg",
  },
  {
    title: "Eyes Open",
    year: "The Hunger Games - 2012",
    cover: "/covers/soundtracks/eyes-open.jpg",
  },
  {
    title: "Sweeter Than Fiction",
    year: "One Chance - 2013",
    cover: "/covers/soundtracks/sweeter-than-fiction.jpg",
  },
  {
    title: "I Don't Wanna Live Forever",
    year: "Fifty Shades Darker - 2016",
    cover: "/covers/soundtracks/idwlf.jpg",
  },
  {
    title: "Beautiful Ghosts",
    year: "Cats - 2019",
    cover: "/covers/soundtracks/beautiful-ghosts.jpg",
  },
  {
    title: "Macavity",
    year: "Cats - 2019",
    cover: "/covers/soundtracks/macavity.jpg",
  },
  {
    title: "Only The Young",
    year: "Miss Americana - 2020",
    cover: "/covers/soundtracks/only-the-young.jpg",
  },
  {
    title: "Carolina",
    year: "Where the Crawdads Sing - 2022",
    cover: "/covers/soundtracks/carolina.jpg",
  },
  {
    title: "I Knew It, I Knew You",
    year: "Toy Story 5 - 2026",
    cover: "/covers/soundtracks/i-knew-it-i-knew-you.jpg",
  },
];

const SPOTIFY_PLAYLIST_URL = "https://open.spotify.com/playlist/5Qgy1dGK6a0OKQDeYDp54y";
const PARTICIPANT_STORAGE_KEY = "tsm:soundtrack-ranking:participant-id";
const POINT_SCHEME = "weighted-v1";
const POINTS_BY_RANK = [30, 24, 19, 15, 12, 9, 7, 5, 3, 2, 1];
const SOUNDTRACK_POINT_ADJUSTMENTS = {};
const NEXT_RANKING_OPTIONS = [
  {
    label: "Rank by soundtrack era",
    detail: "in the order of this ranking",
  },
  {
    label: "Rank by movie year",
    detail: "oldest to newest soundtracks",
  },
  {
    label: "Rank by soundtrack mood",
    detail: "ballads, country, pop, collabs ...",
  },
];

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

function formatLeaderboardPoints(value) {
  const points = Number(value);
  if (!Number.isFinite(points)) return 0;
  const rounded = Math.round(points);
  return rounded.toLocaleString("en-US");
}

function getSoundtrackPointAdjustment(title) {
  return SOUNDTRACK_POINT_ADJUSTMENTS[title] || 0;
}

function shuffleItems(items) {
  return [...items].sort(() => Math.random() - 0.5);
}

function createSorter() {
  return advanceSorter({
    pendingGroups: shuffleItems(TAYLOR_SOUNDTRACKS).map((soundtrack) => [soundtrack.title]),
    nextGroups: [],
    leftGroup: [],
    rightGroup: [],
    mergedGroup: [],
    currentBattle: null,
    battleCount: 0,
    tiedPairs: [],
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

    next.currentBattle = [next.leftGroup[0], next.rightGroup[0]];
  }

  return next;
}

function getRankedSoundtracks(sorter) {
  const orderedTitles = sorter.finished ? sorter.result : TAYLOR_SOUNDTRACKS.map((soundtrack) => soundtrack.title);
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
    ...TAYLOR_SOUNDTRACKS.find((soundtrack) => soundtrack.title === title),
    rank: rankMap[title],
    points: pointsMap[title],
  }));
}

function SoundtrackChoice({ soundtrack, onChoose }) {
  return (
    <button
      type="button"
      className="track13-choice"
      onClick={() => onChoose(soundtrack.title)}
      aria-label={`Choose ${soundtrack.title}`}
    >
      <img src={soundtrack.cover} alt="" decoding="async" />
      <span>{soundtrack.title}</span>
      <small>{soundtrack.year}</small>
    </button>
  );
}

export default function SoundtrackRanking() {
  const isAdmin = useAdminSession();
  const { leaderboards, frozen, loading: visibilityLoading } = useLeaderboardVisibility();
  const isFrozen = Boolean(frozen.soundtrack_ranking);
  const showLeaderboard = isAdmin || (!visibilityLoading && leaderboards.soundtrack_ranking);
  const [sorter, setSorter] = useState(createSorter);
  const [leaderboard, setLeaderboard] = useState([]);
  const [lbLoading, setLbLoading] = useState(true);
  const [lbError, setLbError] = useState(false);
  const [nextRankingPoll, setNextRankingPoll] = useState(null);
  const [voteLoading, setVoteLoading] = useState(true);
  const [voteError, setVoteError] = useState(false);
  const [voteSubmitting, setVoteSubmitting] = useState(false);
  const [showStreamPrompt, setShowStreamPrompt] = useState(true);
  const submittedRef = useRef(false);
  const participantIdRef = useRef(getParticipantId());
  const voteRequestRef = useRef(0);

  const currentSoundtracks = useMemo(() => {
    if (!sorter.currentBattle) return [];
    return sorter.currentBattle.map((title) =>
      TAYLOR_SOUNDTRACKS.find((soundtrack) => soundtrack.title === title)
    );
  }, [sorter.currentBattle]);

  const rankedSoundtracks = useMemo(() => getRankedSoundtracks(sorter), [sorter]);
  const progress = sorter.finished ? 100 : Math.min(95, (sorter.battleCount / 40) * 100);

  const fetchLeaderboard = useCallback(async ({ silent = false, fresh = false } = {}) => {
    if (!showLeaderboard) {
      setLeaderboard([]);
      setLbError(false);
      setLbLoading(false);
      return;
    }

    try {
      const data = await getSoundtrackRankingLeaderboard({ fresh });
      setLeaderboard(Array.isArray(data) ? data : []);
      setLbError(false);
    } catch {
      setLbError(true);
    } finally {
      if (!silent) setLbLoading(false);
    }
  }, [showLeaderboard]);

  const fetchNextRankingPoll = async ({ silent = false } = {}) => {
    try {
      const data = await getSoundtrackRankingNextPoll();
      setNextRankingPoll(data && typeof data === "object" ? data : null);
      setVoteError(false);
    } catch {
      setVoteError(true);
    } finally {
      if (!silent) setVoteLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;

    const load = () => {
      if (!cancelled) fetchLeaderboard();
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [fetchLeaderboard]);

  useEffect(() => {
    fetchNextRankingPoll();
  }, []);

  useEffect(() => {
    if (!sorter.finished || submittedRef.current) return;
    submittedRef.current = true;

    const payload = rankedSoundtracks.map(({ title, rank, points }) => ({
      title,
      rank,
      points: points + getSoundtrackPointAdjustment(title),
    }));
    submitSoundtrackRanking(payload, participantIdRef.current, POINT_SCHEME).catch(() => {});
  }, [sorter.finished, rankedSoundtracks]);

  const recordBattle = (winnerTitle) => {
    if (sorter.finished || !sorter.currentBattle) return;

    setSorter((currentSorter) => {
      const [leftTitle, rightTitle] = currentSorter.currentBattle;
      const nextSorter = {
        ...currentSorter,
        leftGroup: [...currentSorter.leftGroup],
        rightGroup: [...currentSorter.rightGroup],
        mergedGroup: [...currentSorter.mergedGroup],
        tiedPairs: [...currentSorter.tiedPairs],
        currentBattle: null,
        battleCount: currentSorter.battleCount + 1,
      };

      if (winnerTitle === leftTitle) {
        nextSorter.mergedGroup.push(nextSorter.leftGroup.shift());
      } else if (winnerTitle === rightTitle) {
        nextSorter.mergedGroup.push(nextSorter.rightGroup.shift());
      } else {
        nextSorter.tiedPairs.push([leftTitle, rightTitle]);
        nextSorter.mergedGroup.push(nextSorter.leftGroup.shift(), nextSorter.rightGroup.shift());
      }

      return advanceSorter(nextSorter);
    });
  };

  const restart = () => {
    submittedRef.current = false;
    setSorter(createSorter());
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
      const data = await submitSoundtrackRankingNextPoll(option);
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

  const openSpotifyPlaylist = () => {
    window.open(SPOTIFY_PLAYLIST_URL, "_blank", "noopener,noreferrer");
    setShowStreamPrompt(false);
  };

  const nextRankingVotes = nextRankingPoll?.options || [];
  const nextRankingVote = nextRankingPoll?.my_vote || "";
  const totalNextRankingVotes = nextRankingPoll?.total_votes || 0;

  return (
    <>
      <Nav />

      <main className="page games-page swift-day-page" aria-hidden={showStreamPrompt ? "true" : undefined}>
        <header className="games-header swift-day-header">
          <p className="games-eyebrow">Soundtrack Ranking</p>
          <h1 className="games-title">Soundtrack Ranking</h1>
          <p className="games-subtitle">
            Pick your favorite in each battle to build your Taylor Swift soundtrack ranking.
          </p>
          <Link to="/games" className="games-special-link games-special-link--secondary">
            Back to Games
          </Link>
        </header>

        <section className="swift-day-game">
          <div className="swift-day-game-head swift-day-game-head--centered">
            <p className="games-eyebrow">{sorter.finished ? "Results" : "Battle"}</p>
            <h2>Taylor Swift Soundtracks</h2>
          </div>

          <div className="swift-day-progress" aria-hidden="true">
            <span style={{ width: `${progress}%` }} />
          </div>

          {sorter.finished ? (
            <>
              <div className="track13-results-table">
                <div className="track13-results-head">
                  <span>Rank</span>
                  <span>Soundtrack</span>
                  <span>Points</span>
                </div>
                {rankedSoundtracks.map((soundtrack) => (
                  <div className="track13-results-row" key={soundtrack.title}>
                    <strong>{soundtrack.rank}</strong>
                    <div className="track13-results-song">
                      <img src={soundtrack.cover} alt="" loading="lazy" decoding="async" />
                      <span>{soundtrack.title}</span>
                    </div>
                    <span>{soundtrack.points}</span>
                  </div>
                ))}
              </div>

              <button type="button" onClick={restart} className="swift-day-next-button">
                Play again
              </button>
            </>
          ) : (
            <>
              <div className="track13-battle">
                {currentSoundtracks.map((soundtrack) => (
                  <SoundtrackChoice key={soundtrack.title} soundtrack={soundtrack} onChoose={recordBattle} />
                ))}
              </div>

              <div className="track13-neutral-actions">
                <button type="button" onClick={() => recordBattle(null)}>Both</button>
                <button type="button" onClick={() => recordBattle(null)}>Skip</button>
              </div>
            </>
          )}
        </section>

        {showLeaderboard && (
        <section className="swift-day-leaderboard-section swift-day-leaderboard-section--full">
          <header className="swift-day-leaderboard-section-header">
            <h2 className="swift-day-leaderboard-section-title">{isFrozen ? "Final" : "Live"} Soundtrack Leaderboard</h2>
            {isFrozen && <span className="swift-day-frozen-badge">Frozen</span>}
          </header>

          {isFrozen && (
            <p className="swift-day-lb-state">
              This leaderboard is frozen. New rankings are no longer counted.
            </p>
          )}

          {(visibilityLoading || lbLoading) && <p className="swift-day-lb-state">Loading...</p>}

          {!visibilityLoading && !lbLoading && lbError && (
            <p className="swift-day-lb-state swift-day-lb-state--error">
              Leaderboard unavailable right now.
            </p>
          )}

          {!visibilityLoading && !lbLoading && !lbError && leaderboard.length === 0 && (
            <p className="swift-day-lb-state">
              No soundtrack rankings yet. Finish a ranking to set the first board.
            </p>
          )}

          {!visibilityLoading && !lbLoading && !lbError && leaderboard.length > 0 && (
            <>
              <div className="swift-day-song-lb">
                {leaderboard.map((soundtrack, i) => (
                  <div key={soundtrack.title} className="swift-day-song-row">
                    <span className="swift-day-song-rank">{i + 1}</span>
                    <DeltaBadge delta={soundtrack.delta} />
                    <img
                      src={soundtrack.cover}
                      alt=""
                      className="swift-day-song-cover"
                      loading="lazy"
                      decoding="async"
                    />
                    <div className="swift-day-song-meta">
                      <strong>{soundtrack.title}</strong>
                      <small>{soundtrack.entries} ranking{soundtrack.entries === 1 ? "" : "s"}</small>
                    </div>
                    <span className="swift-day-song-pts">
                      {formatLeaderboardPoints(soundtrack.total_points)} pts
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
        )}
      </main>
      {showStreamPrompt && typeof document !== "undefined" && createPortal(
        <div className="soundtrack-stream-modal-backdrop">
          <div
            className="soundtrack-stream-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="soundtrack-stream-modal-title"
          >
            <div className="soundtrack-stream-modal-art" aria-hidden="true">
              <img src="/covers/soundtracks/carolina.jpg" alt="" />
              <img src="/covers/soundtracks/safe-and-sound.jpg" alt="" />
              <img src="/covers/soundtracks/i-knew-it-i-knew-you.jpg" alt="" />
            </div>
            <p className="games-eyebrow">Spotify playlist</p>
            <h2 id="soundtrack-stream-modal-title">Do you want to stream the soundtracks while playing?</h2>
            <div className="soundtrack-stream-modal-actions">
              <button type="button" className="soundtrack-stream-modal-primary" onClick={openSpotifyPlaylist}>
                Yes, open Spotify
              </button>
              <button type="button" className="soundtrack-stream-modal-secondary" onClick={() => setShowStreamPrompt(false)}>
                No, start ranking
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}    </>
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





