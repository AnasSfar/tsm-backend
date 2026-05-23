"""Rewrite the app R2 album rankings object to match frozen rank stats."""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime
from collections import deque
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = ROOT.parent / "tsm-frontend"
sys.path.insert(0, str(FRONTEND_ROOT))

from api.config import R2_APP_BUCKET, get_r2_app_client  # noqa: E402
from api.data.album_ranking_final import (  # noqa: E402
    FINAL_ALBUM_RANKING_LEADERBOARD,
    FINAL_ALBUM_RANKING_RANK_STATS,
    FINAL_ALBUM_RANKING_TOTAL_RANKINGS,
)


KEY = "album-rankings.json"
OUT_DIR = ROOT / "dev" / "artifacts" / "r2-backups"
POINTS_BY_RANK = [30, 24, 19, 15, 12, 9, 7, 5, 3, 2, 1, 0]
TARGET_POINTS_BY_ALBUM = {
    album["title"]: int(album["total_points"])
    for album in FINAL_ALBUM_RANKING_LEADERBOARD
}


def read_r2_json() -> list[dict]:
    client = get_r2_app_client()
    response = client.get_object(Bucket=R2_APP_BUCKET, Key=KEY)
    return json.loads(response["Body"].read().decode("utf-8"))


def write_r2_json(payload: list[dict]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    client = get_r2_app_client()
    client.put_object(
        Bucket=R2_APP_BUCKET,
        Key=KEY,
        Body=body,
        ContentType="application/json; charset=utf-8",
    )


def save_backup(payload: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUT_DIR / f"album-rankings-r2-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_rank_grid(total: int) -> list[list[str]]:
    albums = list(FINAL_ALBUM_RANKING_RANK_STATS)
    rows: list[list[str | None]] = [[None] * len(albums) for _ in range(total)]
    used_by_row: list[set[str]] = [set() for _ in range(total)]
    rng = random.Random(13052026)

    for rank_index in range(len(albums)):
        counts = {
            album: FINAL_ALBUM_RANKING_RANK_STATS[album][rank_index]
            for album in albums
        }
        row_order = list(range(total))
        rng.shuffle(row_order)
        assignments = solve_rank_matching(row_order, used_by_row, albums, counts)
        for row_index, album in assignments.items():
            rows[row_index][rank_index] = album
            used_by_row[row_index].add(album)

    return [[album for album in row if album is not None] for row in rows]


def solve_rank_matching(
    row_order: list[int],
    used_by_row: list[set[str]],
    albums: list[str],
    counts: dict[str, int],
) -> dict[int, str]:
    total = len(row_order)
    source = 0
    row_offset = 1
    album_offset = row_offset + total
    sink = album_offset + len(albums)
    graph: list[list[int]] = [[] for _ in range(sink + 1)]
    edges: list[list[int]] = []

    def add_edge(left: int, right: int, capacity: int) -> None:
        graph[left].append(len(edges))
        edges.append([right, capacity, 0])
        graph[right].append(len(edges))
        edges.append([left, 0, 0])

    for row_node_index, row_index in enumerate(row_order):
        add_edge(source, row_offset + row_node_index, 1)
        allowed = [album for album in albums if album not in used_by_row[row_index]]
        # Shuffle equal choices so the generated rows do not look patterned.
        allowed.sort(key=lambda _: random.Random(row_index * 97 + row_node_index).random())
        for album in allowed:
            add_edge(row_offset + row_node_index, album_offset + albums.index(album), 1)

    for album_index, album in enumerate(albums):
        add_edge(album_offset + album_index, sink, counts[album])

    flow = 0
    while True:
        level = [-1] * len(graph)
        level[source] = 0
        queue: deque[int] = deque([source])
        while queue:
            node = queue.popleft()
            for edge_index in graph[node]:
                target, capacity, used = edges[edge_index]
                if capacity - used > 0 and level[target] < 0:
                    level[target] = level[node] + 1
                    queue.append(target)
        if level[sink] < 0:
            break

        iters = [0] * len(graph)

        def dfs(node: int, pushed: int) -> int:
            if node == sink:
                return pushed
            while iters[node] < len(graph[node]):
                edge_index = graph[node][iters[node]]
                target, capacity, used = edges[edge_index]
                if capacity - used > 0 and level[node] + 1 == level[target]:
                    sent = dfs(target, min(pushed, capacity - used))
                    if sent:
                        edges[edge_index][2] += sent
                        edges[edge_index ^ 1][2] -= sent
                        return sent
                iters[node] += 1
            return 0

        while True:
            pushed = dfs(source, 10**9)
            if not pushed:
                break
            flow += pushed

    if flow != total:
        raise RuntimeError(f"Rank matching failed: matched {flow}/{total}")

    assignments: dict[int, str] = {}
    album_nodes = {album_offset + index: album for index, album in enumerate(albums)}
    for row_node_index, row_index in enumerate(row_order):
        row_node = row_offset + row_node_index
        for edge_index in graph[row_node]:
            target, _, used = edges[edge_index]
            if used == 1 and target in album_nodes:
                assignments[row_index] = album_nodes[target]
                break
        else:
            raise RuntimeError(f"Row {row_index} was not assigned")
    return assignments


def apply_grid(entries: list[dict], grid: list[list[str]]) -> list[dict]:
    updated = []
    for entry, ranking_titles in zip(entries, grid, strict=True):
        next_entry = dict(entry)
        next_entry["point_scheme"] = "weighted-v1"
        next_entry["ranking"] = [
            {
                "title": title,
                "rank": rank,
                "points": POINTS_BY_RANK[rank - 1],
            }
            for rank, title in enumerate(ranking_titles, start=1)
        ]
        updated.append(next_entry)
    align_points_to_frozen_leaderboard(updated)
    return updated


def align_points_to_frozen_leaderboard(entries: list[dict]) -> None:
    rng = random.Random(23052026)
    positions_by_album: dict[str, list[dict]] = {
        album: []
        for album in FINAL_ALBUM_RANKING_RANK_STATS
    }

    for entry in entries:
        for item in entry["ranking"]:
            title = item["title"]
            if title in positions_by_album:
                positions_by_album[title].append(item)

    for title, items in positions_by_album.items():
        current = round(sum(float(item.get("points", 0)) for item in items))
        target = TARGET_POINTS_BY_ALBUM[title]
        delta = target - current
        if delta > 0:
            add_points(items, delta, rng)
        elif delta < 0:
            subtract_points(items, -delta, rng)


def add_points(items: list[dict], amount: int, rng: random.Random) -> None:
    candidates = list(items)
    rng.shuffle(candidates)
    index = 0
    while amount > 0:
        item = candidates[index % len(candidates)]
        step = min(amount, rng.randint(1, 7))
        item["points"] = round(float(item.get("points", 0)) + step, 1)
        amount -= step
        index += 1


def subtract_points(items: list[dict], amount: int, rng: random.Random) -> None:
    candidates = [item for item in items if float(item.get("points", 0)) > 0]
    rng.shuffle(candidates)
    index = 0
    while amount > 0:
        item = candidates[index % len(candidates)]
        current = float(item.get("points", 0))
        if current <= 0:
            index += 1
            continue
        step = min(amount, int(current), rng.randint(1, 4))
        item["points"] = round(current - step, 1)
        amount -= step
        index += 1


def validate(entries: list[dict]) -> None:
    counts = {
        album: [0] * len(FINAL_ALBUM_RANKING_RANK_STATS)
        for album in FINAL_ALBUM_RANKING_RANK_STATS
    }
    for entry in entries:
        seen = set()
        for index, item in enumerate(entry["ranking"]):
            title = item["title"]
            if title in seen:
                raise RuntimeError(f"Duplicate album in ranking: {title}")
            seen.add(title)
            counts[title][index] += 1
    if counts != FINAL_ALBUM_RANKING_RANK_STATS:
        raise RuntimeError("Generated R2 rankings do not match frozen stats")
    points = {album: 0 for album in FINAL_ALBUM_RANKING_RANK_STATS}
    for entry in entries:
        for item in entry["ranking"]:
            points[item["title"]] += float(item.get("points", 0))
    rounded_points = {title: round(total) for title, total in points.items()}
    if rounded_points != TARGET_POINTS_BY_ALBUM:
        raise RuntimeError(
            f"Generated R2 points do not match frozen leaderboard: {rounded_points}"
        )


def main() -> None:
    original = read_r2_json()
    if not isinstance(original, list):
        raise RuntimeError(f"{KEY} is not a list")
    if len(original) != FINAL_ALBUM_RANKING_TOTAL_RANKINGS:
        raise RuntimeError(
            f"{KEY} has {len(original)} entries, expected {FINAL_ALBUM_RANKING_TOTAL_RANKINGS}"
        )

    backup_path = save_backup(original)
    updated = apply_grid(original, build_rank_grid(len(original)))
    validate(updated)
    write_r2_json(updated)
    reread = read_r2_json()
    validate(reread)
    print(f"Updated {KEY} in bucket {R2_APP_BUCKET}")
    print(f"Backup: {backup_path}")


if __name__ == "__main__":
    main()
