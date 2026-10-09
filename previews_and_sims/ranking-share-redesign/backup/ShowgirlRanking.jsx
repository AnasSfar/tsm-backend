import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Nav from "../components/Nav";
import AlbumRankingStanding from "../components/AlbumRankingStanding";
import LiveRefreshStatus from "../components/LiveRefreshStatus";
import { captureNodeToPng, dataUrlToFile, isTouchDevice } from "../components/imageTemplates/capture";
import { getShowgirlRankingLeaderboard, submitShowgirlRanking } from "../api/client";
import { SHOWGIRL_ENCORE_COVER, SHOWGIRL_TRACKS } from "../data/swiftDayGames";
import { useAdminSession } from "../utils/adminSession";
import { useLeaderboardVisibility } from "../utils/leaderboardVisibility";
import "../styles/GamesPage.css";

const MAX_EXPECTED_BATTLES = 50;
const POLL_INTERVAL_MS = 5 * 60_000;
const PARTICIPANT_STORAGE_KEY = "tsm:showgirl-ranking:participant-id";
// linear-v2 = 16-track Encore edition board (linear-v1 was the 12-track standard edition).
const POINT_SCHEME = "linear-v2";
const SKIPPED_BATTLE = "__skipped__";

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

function createSorter() {
  return advanceSorter({
    pendingGroups: shuffleItems(SHOWGIRL_TRACKS).map((track) => [track.title]),
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

    const leftTitle = next.leftGroup[0];
    const rightTitle = next.rightGroup[0];
    const battleKey = [leftTitle, rightTitle].sort().join("\0");
    const previous = next.seenBattles.find((battle) => battle.key === battleKey);
    if (previous) {
      if (previous.winner === leftTitle) {
        next.mergedGroup.push(next.leftGroup.shift());
      } else if (previous.winner === rightTitle) {
        next.mergedGroup.push(next.rightGroup.shift());
      } else if (previous.winner === SKIPPED_BATTLE) {
        next.mergedGroup.push(next.leftGroup.shift(), next.rightGroup.shift());
      } else {
        next.tiedPairs = [...next.tiedPairs, [leftTitle, rightTitle]];
        next.mergedGroup.push(next.leftGroup.shift(), next.rightGroup.shift());
      }
      continue;
    }

    next.currentBattle = [leftTitle, rightTitle];
  }

  return next;
}

function getRankedTracks(sorter) {
  const orderedTitles = sorter.finished ? sorter.result : SHOWGIRL_TRACKS.map((track) => track.title);
  const tiedPairs = sorter.tiedPairs || [];
  const parent = Object.fromEntries(orderedTitles.map((title) => [title, title]));
  const find = (title) => {
    if (parent[title] !== title) parent[title] = find(parent[title]);
    return parent[title];
  };

  for (const [leftTitle, rightTitle] of tiedPairs) {
    if (leftTitle in parent && rightTitle in parent) parent[find(leftTitle)] = find(rightTitle);
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
    const points = SHOWGIRL_TRACKS.length - (currentRank - 1);
    for (const item of group) {
      rankMap[item] = currentRank;
      pointsMap[item] = points;
    }
    currentRank += group.length;
  }

  return orderedTitles
    .map((title, order) => ({
      ...SHOWGIRL_TRACKS.find((track) => track.title === title),
      order,
      rank: rankMap[title],
      points: pointsMap[title],
    }))
    .sort((a, b) => (a.rank - b.rank) || (a.order - b.order));
}

function TrackChoice({ track, onChoose }) {
  return (
    <button
      type="button"
      className="track13-choice"
      onClick={() => onChoose(track.title)}
      aria-label={`Choose ${track.title}`}
    >
      <img src={track.cover} alt="" decoding="async" />
      <span>{track.title}</span>
      <small>{track.album}</small>
    </button>
  );
}

export default function ShowgirlRanking() {
  const isAdmin = useAdminSession();
  const { leaderboards, frozen, loading: visibilityLoading } = useLeaderboardVisibility();
  const isFrozen = Boolean(frozen.showgirl_ranking);
  const showLeaderboard = isAdmin || (!visibilityLoading && leaderboards.showgirl_ranking);
  const [sorter, setSorter] = useState(createSorter);
  const [leaderboard, setLeaderboard] = useState([]);
  const [lbLoading, setLbLoading] = useState(true);
  const [lbError, setLbError] = useState(false);
  const [lbUpdatedAt, setLbUpdatedAt] = useState(null);
  const [lbRefreshing, setLbRefreshing] = useState(false);
  const intervalRef = useRef(null);
  const submittedRef = useRef(false);
  const participantIdRef = useRef(getParticipantId());
  const resultCardRef = useRef(null);
  const [exportingAction, setExportingAction] = useState("");
  const [longPressUrl, setLongPressUrl] = useState("");
  const touchDevice = isTouchDevice();

  const currentTracks = useMemo(() => {
    if (!sorter.currentBattle) return [];
    return sorter.currentBattle.map((title) => SHOWGIRL_TRACKS.find((track) => track.title === title));
  }, [sorter.currentBattle]);

  const rankedTracks = useMemo(() => getRankedTracks(sorter), [sorter]);
  const progress = sorter.finished ? 100 : Math.min(95, (sorter.battleCount / MAX_EXPECTED_BATTLES) * 100);

  const fetchLeaderboard = useCallback(async ({ silent = false, fresh = false } = {}) => {
    if (!showLeaderboard) {
      setLeaderboard([]);
      setLbError(false);
      setLbLoading(false);
      return;
    }

    if (silent) setLbRefreshing(true);
    try {
      const data = await getShowgirlRankingLeaderboard({ fresh });
      setLeaderboard(Array.isArray(data) ? data : []);
      setLbError(false);
      setLbUpdatedAt(Date.now());
    } catch {
      setLbError(true);
    } finally {
      if (!silent) setLbLoading(false);
      setLbRefreshing(false);
    }
  }, [showLeaderboard]);

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
    submitShowgirlRanking(payload, participantIdRef.current, POINT_SCHEME)
      .then(() => {
        if (showLeaderboard) fetchLeaderboard({ silent: true, fresh: true });
      })
      .catch(() => {});
  }, [sorter.finished, rankedTracks, showLeaderboard, fetchLeaderboard]);

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
    setSorter(createSorter());
  };

  const generateResultImage = async (action) => {
    const node = resultCardRef.current;
    if (!node || exportingAction) return;

    const exportWidth = 720;
    setExportingAction(action);
    setLongPressUrl("");
    let exportNode = null;
    let exportHost = null;

    try {
      await document.fonts?.ready;
      exportHost = document.createElement("div");
      Object.assign(exportHost.style, {
        position: "fixed",
        left: "-100000px",
        top: "0",
        pointerEvents: "none",
        zIndex: "-1",
      });

      exportNode = node.cloneNode(true);
      exportNode.classList.add("is-folklore-card-capturing");
      Object.assign(exportNode.style, {
        width: `${exportWidth}px`,
        maxWidth: "none",
        overflow: "visible",
      });
      exportHost.appendChild(exportNode);
      document.body.appendChild(exportHost);

      const imgs = Array.from(exportNode.querySelectorAll("img"));
      const originalSrcs = imgs.map((img) => img.src);
      const srcToDataUrl = new Map();
      await Promise.all(
        [...new Set(originalSrcs.filter(Boolean))].map(async (src) => {
          try {
            const res = await fetch(src);
            const blob = await res.blob();
            const dataUri = await new Promise((resolve) => {
              const reader = new FileReader();
              reader.onload = () => resolve(reader.result);
              reader.readAsDataURL(blob);
            });
            srcToDataUrl.set(src, dataUri);
          } catch {
            // Keep the original source if the browser can already render it.
          }
        })
      );
      imgs.forEach((img) => {
        if (srcToDataUrl.has(img.src)) img.src = srcToDataUrl.get(img.src);
      });

      await new Promise((resolve) => requestAnimationFrame(resolve));

      const dataUrl = await captureNodeToPng(exportNode, {
        pixelRatio: 2,
        backgroundColor: getComputedStyle(document.body).getPropertyValue("--bg").trim() || "#f5f8f5",
        margin: 0,
      });
      const fileName = "my-showgirl-ranking.png";
      const file = dataUrlToFile(dataUrl, fileName);

      if ((action === "share" || isTouchDevice()) && navigator.canShare?.({ files: [file] })) {
        await navigator.share({
          files: [file],
          title: "My Showgirl ranking",
          text: "My The Life of a Showgirl: The Encore ranking on The Taylor Swift Museum",
        });
        return;
      }

      if (isTouchDevice()) {
        setLongPressUrl(dataUrl);
        return;
      }

      const link = document.createElement("a");
      link.href = dataUrl;
      link.download = fileName;
      link.click();
    } catch (err) {
      console.error("Unable to export Showgirl ranking card", err);
    } finally {
      exportHost?.remove();
      setExportingAction("");
    }
  };

  return (
    <>
      <Nav />
      <main className="page games-page swift-day-page folklore-ranking-page">
        <header className="games-header swift-day-header">
          <p className="games-eyebrow">The Life of a Showgirl: The Encore</p>
          <h1 className="games-title">Showgirl ranking</h1>
          <p className="games-subtitle">
            Pick your favorite in each battle to build your personal Showgirl track ranking.
          </p>
          <a
            href="https://open.spotify.com/album/4hF2gTGuPYlykYuphDxi8J"
            target="_blank"
            rel="noopener noreferrer"
            className="games-special-link games-special-link--spotify"
          >
            Stream while playing
          </a>
          <Link to="/games" className="games-special-link games-special-link--secondary">
            Back to Games
          </Link>
          <AlbumRankingStanding album="The Life of a Showgirl" cover={SHOWGIRL_ENCORE_COVER} />
        </header>

        <section className="swift-day-game">
          <div className="swift-day-game-head swift-day-game-head--centered">
            <p className="games-eyebrow">{sorter.finished ? "Results" : "Battle"}</p>
            <h2>{sorter.finished ? "Your Showgirl ranking" : "Choose a track"}</h2>
          </div>

          <div className="swift-day-progress" aria-hidden="true">
            <span style={{ width: `${progress}%` }} />
          </div>

          {sorter.finished ? (
            <>
              <article className="folklore-result-card" ref={resultCardRef}>
                <header className="folklore-result-card-head">
                  <img src={SHOWGIRL_ENCORE_COVER} alt="" decoding="async" />
                  <div>
                    <p>TSM Showgirl ranking</p>
                    <h3>My Showgirl Ranking</h3>
                    <span>thetsmuseum.app/showgirl-ranking</span>
                  </div>
                </header>

                <div className="track13-results-table folklore-result-table">
                  <div className="track13-results-head">
                    <span>Rank</span>
                    <span>Song</span>
                    <span>Points</span>
                  </div>
                  {rankedTracks.map((track) => (
                    <div className="track13-results-row" key={`${track.album}-${track.title}`}>
                      <strong>{track.rank}</strong>
                      <div className="track13-results-song">
                        <img src={track.cover} alt="" loading="lazy" decoding="async" />
                        <span>{track.title}</span>
                      </div>
                      <span>{track.points}</span>
                    </div>
                  ))}
                </div>
              </article>

              <div className="folklore-result-actions">
                <button
                  type="button"
                  onClick={() => generateResultImage("download")}
                  disabled={Boolean(exportingAction)}
                >
                  {exportingAction === "download" ? "Generating..." : touchDevice ? "Save / share image" : "Download PNG"}
                </button>
                {!touchDevice && (
                  <button
                    type="button"
                    onClick={() => generateResultImage("share")}
                    disabled={Boolean(exportingAction)}
                  >
                    {exportingAction === "share" ? "Generating..." : "Share"}
                  </button>
                )}
              </div>

              <button type="button" onClick={restart} className="swift-day-next-button">
                Play again
              </button>
            </>
          ) : (
            <>
              <div className="track13-battle">
                {currentTracks.map((track) => (
                  <TrackChoice key={`${track.album}-${track.title}`} track={track} onChoose={recordBattle} />
                ))}
              </div>

              <div className="track13-neutral-actions">
                <button type="button" onClick={() => recordBattle(null)}>Both</button>
                <button type="button" onClick={() => recordBattle(SKIPPED_BATTLE)}>Skip</button>
              </div>
            </>
          )}
        </section>

        {longPressUrl && (
          <div className="folklore-image-modal" onClick={() => setLongPressUrl("")}>
            <div className="folklore-image-modal-inner" onClick={(event) => event.stopPropagation()}>
              <p>Hold the image to save it</p>
              <img src={longPressUrl} alt="Your Showgirl ranking card" />
              <button type="button" onClick={() => setLongPressUrl("")}>
                Close
              </button>
            </div>
          </div>
        )}

        {showLeaderboard && (
        <section className="swift-day-leaderboard-section swift-day-leaderboard-section--full">
          <header className="swift-day-leaderboard-section-header">
            <h2 className="swift-day-leaderboard-section-title">
              {isFrozen ? "Final" : "Live"} Showgirl Leaderboard
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
                label="Refresh Showgirl leaderboard"
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

          {!visibilityLoading && !lbLoading && lbError && (
            <p className="swift-day-lb-state swift-day-lb-state--error">
              Leaderboard unavailable right now.
            </p>
          )}

          {!visibilityLoading && !lbLoading && !lbError && leaderboard.length === 0 && (
            <p className="swift-day-lb-state">
              No Showgirl rankings yet. Finish a ranking to set the first board.
            </p>
          )}

          {!visibilityLoading && !lbLoading && !lbError && leaderboard.length > 0 && (
            <div className="swift-day-song-lb">
              {leaderboard.map((track, index) => (
                <div key={track.title} className="swift-day-song-row">
                  <span className="swift-day-song-rank">{index + 1}</span>
                  <DeltaBadge delta={track.delta} />
                  <img
                    src={track.cover}
                    alt=""
                    className="swift-day-song-cover"
                    loading="lazy"
                    decoding="async"
                  />
                  <div className="swift-day-song-meta">
                    <strong>{track.title}</strong>
                    <small>{track.entries} ranking{track.entries === 1 ? "" : "s"}</small>
                  </div>
                  <span className="swift-day-song-pts">
                    {formatLeaderboardPoints(track.total_points)} pts
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
        )}
      </main>
    </>
  );
}

function formatLeaderboardPoints(value) {
  const points = Number(value);
  if (!Number.isFinite(points)) return 0;
  return Math.round(points).toLocaleString("en-US");
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
