"""Build the website's data files.

  python -m streaks.build --out public/data

Writes, per league:
  public/data/<league>.json           latest slate (what the site loads)
  public/data/<league>-<date>.json    archive copy
and public/data/index.json listing what exists.

A file is only rewritten when its content changed (ignoring the timestamp), so
a scheduled job can commit the folder without creating empty deploys.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

from .config import LEAGUES
from .engine import Settings
from .espn import ET, EspnClient
from .odds import OddsClient, attach_lines
from .pipeline import run


def _same(path: Path, data: dict) -> bool:
    if not path.exists():
        return False
    try:
        old = json.loads(path.read_text())
    except ValueError:
        return False
    strip = lambda d: {k: v for k, v in d.items() if k != "generated_at"}
    return strip(old) == strip(data)


def write_if_changed(path: Path, data: dict) -> bool:
    if _same(path, data):
        return False
    path.write_text(json.dumps(data, separators=(",", ":")))
    return True


def _carry_forward_book_lines(result: dict, old_path: Path) -> None:
    """A plain (non --with-odds) rebuild has no sportsbook data of its own - without
    this, the ~30min site rebuild would erase the odds workflow's once-a-day fetch
    within minutes. Carries forward any existing 'book' field for rows that still
    qualify, by (stat, player) - it's naturally dropped once a player/stat falls out
    of the picks or the odds workflow attaches a fresher line."""
    if not old_path.exists():
        return
    try:
        old = json.loads(old_path.read_text())
    except ValueError:
        return
    old_books = {(c["key"], r["player"]["id"]): r["book"]
                for c in old.get("stats", []) for r in c.get("rows", []) if r.get("book")}
    for card in result["stats"]:
        for row in card["rows"]:
            if "book" not in row:
                b = old_books.get((card["key"], row["player"]["id"]))
                if b:
                    row["book"] = b


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="streaks.build")
    ap.add_argument("--out", type=Path, default=Path("public/data"))
    ap.add_argument("--cache", type=Path, default=Path(".cache"))
    ap.add_argument("--leagues", default="nba,nfl")
    ap.add_argument("--date", type=date.fromisoformat, default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--with-odds", action="store_true",
                    help="attach sportsbook lines for today's top picks (needs ODDS_API_KEY)")
    ap.add_argument("--odds-top-n", type=int, default=12,
                    help="top N rows per stat card to fetch a sportsbook line for")
    a = ap.parse_args(argv)

    odds_key = os.environ.get("ODDS_API_KEY")
    if a.with_odds and not odds_key:
        print("--with-odds given but ODDS_API_KEY is not set; skipping odds", file=sys.stderr)
        a.with_odds = False
    odds_client = OddsClient(odds_key, a.cache / "odds") if a.with_odds else None

    a.out.mkdir(parents=True, exist_ok=True)
    target = a.date or datetime.now(ET).date()
    index_path = a.out / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"leagues": {}}
    failures = 0
    for key in a.leagues.split(","):
        league = LEAGUES[key.strip()]
        try:
            client = EspnClient(league, a.cache, offline=a.offline)
            if odds_client:
                result, players = run(league, client, target, Settings(), want_players=True)
                attach_lines(result, odds_client, league.key, players,
                            {s.key: s for s in league.stats}, top_n=a.odds_top_n)
            else:
                result = run(league, client, target, Settings())
        except Exception:
            traceback.print_exc()
            failures += 1
            continue   # keep the last good file for this league
        if not odds_client:
            _carry_forward_book_lines(result, a.out / f"{league.key}.json")
        changed = write_if_changed(a.out / f"{league.key}.json", result)
        write_if_changed(a.out / f"{league.key}-{target}.json", result)
        entry = index["leagues"].setdefault(league.key, {"name": league.name, "archive": []})
        entry["latest"] = str(target)
        if str(target) not in entry["archive"]:
            entry["archive"] = sorted(entry["archive"] + [str(target)], reverse=True)[:60]
        print(f"{league.name}: {'updated' if changed else 'unchanged'}")
    index["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not index_path.exists() or json.loads(index_path.read_text()).get("leagues") != index["leagues"]:
        index_path.write_text(json.dumps(index, indent=1))
    return 1 if failures == len(a.leagues.split(",")) else 0


if __name__ == "__main__":
    sys.exit(main())
