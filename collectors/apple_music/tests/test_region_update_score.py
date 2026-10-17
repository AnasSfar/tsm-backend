import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.region_update_score import SongMove, score_update  # noqa: E402

THRESHOLD = 10  # post_new_release_progression.SCORE_CARD_MIN_SCORE default


def test_out_is_eliminatory():
    upd = score_update([SongMove("A", 1, 5)], ["B"])
    assert upd.veto and not upd.interesting


def test_debut_alone_clears_threshold_anywhere_on_chart():
    assert score_update([SongMove("New", 199, None, badge="NEW")], []).score >= THRESHOLD


def test_bottom_reentry_is_small():
    assert score_update([SongMove("Old", 192, None)], []).score < 10


def test_net_positive_run_posts():
    # Owner 2026-10-16: "si on a un (+3) et un (-1) dans le meme run on peut poster".
    assert score_update([SongMove("A", 47, 50), SongMove("B", 81, 80)], []).score > 0


def test_net_negative_run_does_not_post():
    assert score_update([SongMove("A", 49, 50), SongMove("B", 90, 80)], []).score < 0


def test_number_one_and_top10_entry():
    assert score_update([SongMove("A", 1, 3)], []).score >= THRESHOLD
    top10 = score_update([SongMove("A", 6, 26)], [])
    assert top10.events[0]["kind"] == "top10_entry" and top10.score >= THRESHOLD


def test_drops_cost_and_best_news_first():
    upd = score_update([SongMove("Up", 20, 30), SongMove("Down", 50, 30), SongMove("Back", 15, None)], [])
    assert [e["title"] for e in upd.events] == ["Back", "Up", "Down"]
    assert upd.events[-1]["points"] < 0
