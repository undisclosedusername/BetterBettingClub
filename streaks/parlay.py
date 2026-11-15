"""Parlay combination search over sportsbook-matched streak picks.

  python -m streaks.parlay nba --target +400
  python -m streaks.parlay nba --target +400 --legs 5
  python -m streaks.parlay nfl --target -200 --legs 3 --count 5

Pure and offline: reads an already-built league JSON (public/data/<league>.json,
produced by `streaks.build --with-odds`) and searches combinations of its
odds-matched rows ("legs") for the one(s) whose combined American odds land
closest to a target, honoring an optional exact leg count.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_POOL = 25        # cap combinations: C(25, 6) ~= 177k, still fast
DEFAULT_LEG_RANGE = range(2, 7)


def american_to_decimal(price: int) -> float:
    return 1 + price / 100 if price > 0 else 1 + 100 / abs(price)


def decimal_to_american(d: float) -> int:
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


def parse_american(s: str) -> int:
    s = s.strip()
    if not s.startswith(("+", "-")):
        raise ValueError(f"odds must start with + or -, e.g. +400 or -150 (got {s!r})")
    return int(s)


@dataclass
class Leg:
    player: str
    team: str
    stat_label: str
    point: float
    price: int
    hits: int | None
    games: int | None

    @property
    def decimal(self) -> float:
        return american_to_decimal(self.price)

    @property
    def confidence(self) -> float:
        if not self.games:
            return 0.5
        return self.hits / self.games

    @property
    def label(self) -> str:
        rate = f" ({self.hits}/{self.games})" if self.games else ""
        return f"{self.player} ({self.team}) {self.point}+ {self.stat_label} @ {self.price:+d}{rate}"


def legs_from_result(result: dict) -> list[Leg]:
    teams = result.get("teams", {})
    out = []
    seen = set()
    for card in result.get("stats", []):
        for row in card.get("rows", []):
            book = row.get("book")
            if not book or book.get("over_price") is None:
                continue
            key = (row["player"]["id"], card["key"])
            if key in seen:
                continue
            seen.add(key)
            out.append(Leg(
                player=row["player"]["short"],
                team=(teams.get(row["team"]) or {}).get("abbr", ""),
                stat_label=card["label"],
                point=book["point"],
                price=book["over_price"],
                hits=book.get("hits"),
                games=book.get("games"),
            ))
    return out


@dataclass
class Parlay:
    legs: list[Leg]

    @property
    def decimal(self) -> float:
        d = 1.0
        for leg in self.legs:
            d *= leg.decimal
        return d

    @property
    def american(self) -> int:
        return decimal_to_american(self.decimal)

    @property
    def confidence(self) -> float:
        p = 1.0
        for leg in self.legs:
            p *= leg.confidence
        return p


def best_parlays(legs: list[Leg], target_price: int, num_legs: int | None = None,
                 count: int = 3, pool_size: int = MAX_POOL) -> list[Parlay]:
    """Combinations of `legs` whose combined American odds land closest to target_price.

    One leg per player. Pool is trimmed to the `pool_size` highest-confidence legs
    before searching, since parlay odds grow quickly with leg count.
    """
    pool = sorted(legs, key=lambda l: -l.confidence)[:pool_size]
    target_log = math.log(american_to_decimal(target_price))
    leg_counts = [num_legs] if num_legs else list(DEFAULT_LEG_RANGE)

    scored: list[tuple[float, Parlay]] = []
    for k in leg_counts:
        if k < 1 or k > len(pool):
            continue
        for combo in itertools.combinations(pool, k):
            if len({leg.player for leg in combo}) != k:
                continue  # one leg per player
            p = Parlay(list(combo))
            dist = abs(math.log(p.decimal) - target_log)
            scored.append((dist, p))
    scored.sort(key=lambda x: (x[0], -x[1].confidence))
    return [p for _, p in scored[:count]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="streaks.parlay",
                                 description="Recommend parlays from a built league's odds-matched picks.")
    ap.add_argument("league", choices=["nba", "nfl"])
    ap.add_argument("--target", required=True, help="target combined American odds, e.g. +400 or -150")
    ap.add_argument("--legs", type=int, default=None, help="exact number of legs (default: search 2-6)")
    ap.add_argument("--count", type=int, default=3, help="how many recommendations to print")
    ap.add_argument("--json", type=Path, default=Path("public/data"), help="dir holding <league>.json")
    a = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    path = a.json / f"{a.league}.json"
    if not path.exists():
        print(f"no data at {path} - run streaks.build --with-odds first", file=sys.stderr)
        return 1
    result = json.loads(path.read_text())
    legs = legs_from_result(result)
    if not legs:
        print("no sportsbook-matched picks in this file (build with --with-odds)", file=sys.stderr)
        return 1

    try:
        target = parse_american(a.target)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    parlays = best_parlays(legs, target, num_legs=a.legs, count=a.count)
    if not parlays:
        print("no combination found (try a different --legs count)", file=sys.stderr)
        return 1

    print(f"{len(legs)} available legs. Target {target:+d}:\n")
    for i, p in enumerate(parlays, 1):
        print(f"#{i}  {p.american:+d}  ({len(p.legs)} legs, implied win% ~{p.confidence * 100:.1f})")
        for leg in p.legs:
            print(f"     {leg.label}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
