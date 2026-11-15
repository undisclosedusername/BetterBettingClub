import json
from pathlib import Path

from streaks.build import _carry_forward_book_lines


def test_carry_forward_keeps_existing_book_for_matching_rows(tmp_path):
    old_path = tmp_path / "nba.json"
    old_path.write_text(json.dumps({"stats": [{"key": "pts", "rows": [
        {"player": {"id": "p1"}, "book": {"point": 24.5, "over_price": -130}},
    ]}]}))
    new_result = {"stats": [{"key": "pts", "rows": [
        {"player": {"id": "p1"}},                 # still qualifies - should get the old line back
        {"player": {"id": "p2"}},                 # new player - no old line to carry
    ]}]}
    _carry_forward_book_lines(new_result, old_path)
    rows = new_result["stats"][0]["rows"]
    assert rows[0]["book"] == {"point": 24.5, "over_price": -130}
    assert "book" not in rows[1]


def test_carry_forward_does_not_overwrite_fresh_book(tmp_path):
    old_path = tmp_path / "nba.json"
    old_path.write_text(json.dumps({"stats": [{"key": "pts", "rows": [
        {"player": {"id": "p1"}, "book": {"point": 24.5, "over_price": -130}},
    ]}]}))
    fresh = {"point": 25.5, "over_price": -115}
    new_result = {"stats": [{"key": "pts", "rows": [
        {"player": {"id": "p1"}, "book": fresh},
    ]}]}
    _carry_forward_book_lines(new_result, old_path)
    assert new_result["stats"][0]["rows"][0]["book"] == fresh


def test_carry_forward_handles_missing_or_corrupt_file(tmp_path):
    new_result = {"stats": [{"key": "pts", "rows": [{"player": {"id": "p1"}}]}]}
    _carry_forward_book_lines(new_result, tmp_path / "missing.json")
    assert "book" not in new_result["stats"][0]["rows"][0]

    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    _carry_forward_book_lines(new_result, bad)
    assert "book" not in new_result["stats"][0]["rows"][0]
