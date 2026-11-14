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
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

from .config import LEAGUES
from .engine import Settings
from .espn import ET, EspnClient
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="streaks.build")
    ap.add_argument("--out", type=Path, default=Path("public/data"))
    ap.add_argument("--cache", type=Path, default=Path(".cache"))
    ap.add_argument("--leagues", default="nba,nfl")
    ap.add_argument("--date", type=date.fromisoformat, default=None)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)

    a.out.mkdir(parents=True, exist_ok=True)
    target = a.date or datetime.now(ET).date()
    index_path = a.out / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"leagues": {}}
    failures = 0
    for key in a.leagues.split(","):
        league = LEAGUES[key.strip()]
        try:
            client = EspnClient(league, a.cache, offline=a.offline)
            result = run(league, client, target, Settings())
        except Exception:
            traceback.print_exc()
            failures += 1
            continue   # keep the last good file for this league
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
