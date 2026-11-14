"""Command line entry point.

  python -m streaks nba                      # today's slate, all stats
  python -m streaks nfl                      # this week's NFL slate
  python -m streaks nba --stats pts,reb,ast,3pm --min-games 5 --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from .config import LEAGUES
from .engine import Settings
from .espn import ET, EspnClient
from .pipeline import run
from .render import card_html, write_cards


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="streaks", description="Find 100%% player-prop streaks for NBA/NFL.")
    ap.add_argument("league", choices=sorted(LEAGUES))
    ap.add_argument("--date", type=date.fromisoformat, default=None,
                    help="slate date (YYYY-MM-DD, US/Eastern). Default: today")
    ap.add_argument("--slate-days", type=int, default=None,
                    help="days of upcoming games to include (default: NBA 1, NFL 7)")
    ap.add_argument("--stats", default=None, help="comma-separated stat keys (default: all). Use --list-stats")
    ap.add_argument("--list-stats", action="store_true")
    ap.add_argument("--since", type=date.fromisoformat, default=None,
                    help="earliest game date to load (use to reach back into last season)")
    ap.add_argument("--lookback-days", type=int, default=None)
    ap.add_argument("--all-teams", action="store_true", help="don't restrict to teams on the slate")
    d = Settings()
    ap.add_argument("--min-games", type=int, default=d.min_games)
    ap.add_argument("--min-near-games", type=int, default=d.min_near_games)
    ap.add_argument("--max-misses", type=int, default=d.max_misses)
    ap.add_argument("--floor-ratio", type=float, default=d.floor_ratio)
    ap.add_argument("--top", type=int, default=d.top)
    ap.add_argument("--soft-d-pct", type=float, default=d.soft_d_pct,
                    help="flag opponents in the softest X share of the league vs the stat")
    ap.add_argument("--hot-ratio", type=float, default=d.hot_ratio)
    ap.add_argument("--weak-opp-gap", type=float, default=d.weak_opp_gap)
    ap.add_argument("--no-preferable-first", action="store_true",
                    help="keep pure streak-length order (still highlights)")
    ap.add_argument("--out", type=Path, default=Path("output"))
    ap.add_argument("--cache", type=Path, default=Path(".cache"))
    ap.add_argument("--offline", action="store_true", help="use only cached data")
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--json", type=Path, default=None, help="also dump results as JSON")
    return ap.parse_args(argv)


def settings_from_args(a) -> Settings:
    return Settings(min_games=a.min_games, min_near_games=a.min_near_games, max_misses=a.max_misses,
                    floor_ratio=a.floor_ratio, top=a.top, soft_d_pct=a.soft_d_pct,
                    hot_ratio=a.hot_ratio, weak_opp_gap=a.weak_opp_gap,
                    preferable_first=not a.no_preferable_first)


def print_result(result: dict) -> None:
    teams = result["teams"]
    for card in result["stats"]:
        print(f"\n100% {card['title']} Streaks")
        for r in card["rows"]:
            t = (teams.get(r["team"]) or {}).get("abbr", "")
            star = "★" if r["preferable"] else " "
            flags = "; ".join(f["text"] for f in r["flags"])
            print(f" {star} {t:4} {r['player']['short']:22} {r['threshold_label']:>5} {card['label']:14} "
                  f"{r['hits']:>2}/{r['games']:<2}  {flags}")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = parse_args(argv)
    league = LEAGUES[a.league]
    if a.list_stats:
        for s in league.stats:
            print(f"{s.key:14} {s.title:18} ladder={list(s.ladder)}")
        return 0

    stats = None
    if a.stats:
        wanted = [k.strip() for k in a.stats.split(",")]
        unknown = set(wanted) - {s.key for s in league.stats}
        if unknown:
            print(f"unknown stats: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        stats = [s for s in league.stats if s.key in wanted]

    target = a.date or datetime.now(ET).date()
    client = EspnClient(league, a.cache, offline=a.offline)
    result = run(league, client, target, settings_from_args(a), stats=stats, since=a.since,
                 lookback_days=a.lookback_days, slate_days=a.slate_days, all_teams=a.all_teams,
                 progress=lambda i, n: print(f"\r  game {i}/{n}", end="", flush=True))
    print()
    if not result["stats"]:
        print("No upcoming games in that window - try --all-teams or another --date.")
        return 1
    print_result(result)

    out_dir = a.out / f"{league.key}-{target}"
    cards = [(f"{league.key}-{target}-{c['key']}", card_html(result, c)) for c in result["stats"]]
    written = write_cards(cards, out_dir, png=not a.no_png)
    if a.json:
        a.json.write_text(json.dumps(result, indent=2))
    print(f"\nWrote {len(written)} files to {out_dir}/")
    return 0
