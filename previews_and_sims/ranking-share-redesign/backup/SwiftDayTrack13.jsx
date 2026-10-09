import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Nav from "../components/Nav";
import { TRACK_THIRTEENS } from "../data/swiftDayGames";
import { submitTrack13Ranking } from "../api/client";
import "../styles/GamesPage.css";

function shuffleItems(items) {
  return [...items].sort(() => Math.random() - 0.5);
}

function createSorter() {
  return advanceSorter({
    pendingGroups: shuffleItems(TRACK_THIRTEENS).map((track) => [track.title]),
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

function getRankedTracks(sorter) {
  const orderedTitles = sorter.finished ? sorter.result : TRACK_THIRTEENS.map((t) => t.title);
  const tiedPairs = sorter.tiedPairs || [];

  // Union-Find: group tied songs together
  const parent = Object.fromEntries(orderedTitles.map((t) => [t, t]));
  const find = (t) => { if (parent[t] !== t) parent[t] = find(parent[t]); return parent[t]; };
  for (const [a, b] of tiedPairs) {
    if (a in parent && b in parent) parent[find(a)] = find(b);
  }

  // Walk orderedTitles in order; assign rank + points per tie group
  const rankMap = {};
  const pointsMap = {};
  let currentRank = 1;
  const seen = new Set();

  for (const title of orderedTitles) {
    const root = find(title);
    if (seen.has(root)) continue;
    seen.add(root);
    const group = orderedTitles.filter((t) => find(t) === root);
    const pts = TRACK_THIRTEENS.length - (currentRank - 1);
    for (const t of group) {
      rankMap[t] = currentRank;
      pointsMap[t] = pts;
    }
    currentRank += group.length;
  }

  return orderedTitles.map((title) => ({
    ...TRACK_THIRTEENS.find((item) => item.title === title),
    rank: rankMap[title],
    points: pointsMap[title],
  }));
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

export default function SwiftDayTrack13() {
  const [sorter, setSorter] = useState(createSorter);
  const submittedRef = useRef(false);

  const currentTracks = useMemo(() => {
    if (!sorter.currentBattle) return [];
    return sorter.currentBattle.map((title) => TRACK_THIRTEENS.find((track) => track.title === title));
  }, [sorter.currentBattle]);
  const rankedTracks = useMemo(() => getRankedTracks(sorter), [sorter]);
  const progress = sorter.finished ? 100 : Math.min(95, (sorter.battleCount / 32) * 100);

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

  useEffect(() => {
    if (!sorter.finished || submittedRef.current) return;
    submittedRef.current = true;
    const payload = rankedTracks.map(({ title, album, points }) => ({ title, album, points }));
    submitTrack13Ranking(payload).catch(() => {});
  }, [sorter.finished, rankedTracks]);

  const restart = () => {
    submittedRef.current = false;
    setSorter(createSorter());
  };

  return (
    <>
      <Nav />
      <main className="page games-page swift-day-page">
        <header className="games-header swift-day-header">
          <p className="games-eyebrow">Swift Day Game 2</p>
          <h1 className="games-title">Ranking Track 13's</h1>
          <p className="games-subtitle">
            Pick your favorite in each battle to build your Track 13 ranking.
          </p>
          <Link to="/swift-day" className="games-special-link games-special-link--secondary">
            Back to Swift Day
          </Link>
        </header>

        <section className="swift-day-game">
          <div className="swift-day-game-head swift-day-game-head--centered">
            <p className="games-eyebrow">{sorter.finished ? "Results" : "Battle"}</p>
            <h2>Track 13 Ranking</h2>
          </div>

          <div className="swift-day-progress" aria-hidden="true">
            <span style={{ width: `${progress}%` }} />
          </div>

          {sorter.finished ? (
            <>
              <div className="track13-results-table">
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
                <button type="button" onClick={() => recordBattle(null)}>Skip</button>
              </div>
            </>
          )}
        </section>
      </main>
    </>
  );
}
