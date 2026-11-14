"""Stat definitions and alt-line ladders for each league.

Each StatDef says how to pull a number out of a player's box-score line and
which thresholds ("1+", "3+", "10+" ...) are candidates for a streak. Ladders
mirror the alt lines sportsbooks typically post; edit freely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

Line = dict[str, float]  # normalized box-score line, e.g. {"PTS": 21, "REB": 7, ...}


@dataclass(frozen=True)
class StatDef:
    key: str                 # id used on the command line
    label: str               # shown on the card: "1+ 3PTM", "7+ Rebounds"
    title: str               # card title: "100% {title} Streaks"
    ladder: tuple[float, ...]
    extract: Callable[[Line], float]
    min_line: float = 0      # never report a threshold below this
    min_avg: float = 0       # skip players who average below this


def _f(*keys: str) -> Callable[[Line], float]:
    return lambda line: sum(line.get(k, 0.0) for k in keys)


NBA_STATS: list[StatDef] = [
    StatDef("pts", "Points", "Points", (5, 8, 10, 12, 15, 18, 20, 22, 25, 28, 30, 35, 40, 45, 50),
            _f("PTS"), min_line=8, min_avg=6),
    StatDef("reb", "Rebounds", "Rebounds", (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20),
            _f("REB"), min_line=3, min_avg=3),
    StatDef("ast", "Assists", "Assists", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16),
            _f("AST"), min_line=2, min_avg=2),
    StatDef("3pm", "3PTM", "3PTM", (1, 2, 3, 4, 5, 6, 7, 8),
            _f("3PM"), min_line=1, min_avg=0.8),
    StatDef("pra", "Pts+Reb+Ast", "PRA", (10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60),
            _f("PTS", "REB", "AST"), min_line=15, min_avg=12),
    StatDef("pr", "Pts+Reb", "Pts + Reb", (10, 15, 20, 25, 30, 35, 40, 45),
            _f("PTS", "REB"), min_line=12, min_avg=10),
    StatDef("pa", "Pts+Ast", "Pts + Ast", (10, 15, 20, 25, 30, 35, 40, 45),
            _f("PTS", "AST"), min_line=12, min_avg=10),
    StatDef("ra", "Reb+Ast", "Reb + Ast", (4, 6, 8, 10, 12, 14, 16, 18, 20),
            _f("REB", "AST"), min_line=5, min_avg=5),
    StatDef("stl", "Steals", "Steals", (1, 2, 3, 4), _f("STL"), min_line=1, min_avg=0.9),
    StatDef("blk", "Blocks", "Blocks", (1, 2, 3, 4, 5), _f("BLK"), min_line=1, min_avg=0.9),
]

NFL_STATS: list[StatDef] = [
    StatDef("pass_yds", "Pass Yds", "Passing Yards",
            (125, 150, 175, 200, 225, 250, 275, 300, 325, 350, 400),
            _f("passing.YDS"), min_line=150, min_avg=140),
    StatDef("pass_td", "Pass TD", "Passing TD", (1, 2, 3, 4, 5),
            _f("passing.TD"), min_line=1, min_avg=0.8),
    StatDef("pass_cmp", "Completions", "Completions", (12, 15, 18, 20, 22, 25, 28, 30, 35),
            _f("passing.CMP"), min_line=15, min_avg=12),
    StatDef("rush_yds", "Rush Yds", "Rushing Yards", (15, 25, 40, 50, 60, 70, 80, 90, 100, 125, 150),
            _f("rushing.YDS"), min_line=25, min_avg=20),
    StatDef("rush_att", "Rush Att", "Rush Attempts", (5, 8, 10, 12, 15, 18, 20, 22, 25),
            _f("rushing.CAR"), min_line=8, min_avg=6),
    StatDef("rec_yds", "Rec Yds", "Receiving Yards", (15, 25, 40, 50, 60, 70, 80, 90, 100, 125, 150),
            _f("receiving.YDS"), min_line=25, min_avg=20),
    StatDef("rec", "Receptions", "Receptions", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12),
            _f("receiving.REC"), min_line=2, min_avg=2),
    StatDef("rush_rec_yds", "Rush+Rec Yds", "Rush + Rec Yards",
            (25, 40, 50, 60, 75, 90, 100, 125, 150, 175),
            _f("rushing.YDS", "receiving.YDS"), min_line=40, min_avg=35),
    StatDef("anytime_td", "Anytime TD", "Anytime TD", (1, 2, 3),
            _f("rushing.TD", "receiving.TD"), min_line=1, min_avg=0.3),
    StatDef("int", "Interceptions", "Defensive INT", (1,),
            _f("interceptions.INT"), min_line=1, min_avg=0.3),
    StatDef("sacks", "Sacks", "Sacks", (0.5, 1, 1.5, 2),
            _f("defensive.SACKS"), min_line=0.5, min_avg=0.4),
    StatDef("tackles", "Tackles", "Tackles", (3, 4, 5, 6, 7, 8, 9, 10, 12),
            _f("defensive.TOT"), min_line=4, min_avg=4),
]


@dataclass(frozen=True)
class League:
    key: str
    name: str
    sport: str              # ESPN sport path segment
    espn_league: str        # ESPN league path segment
    stats: list[StatDef] = field(default_factory=list)
    default_lookback_days: int = 45
    slate_days: int = 1     # NBA: one night. NFL: the coming week (Thu-Mon)


LEAGUES: dict[str, League] = {
    "nba": League("nba", "NBA", "basketball", "nba", NBA_STATS, default_lookback_days=45),
    "nfl": League("nfl", "NFL", "football", "nfl", NFL_STATS, default_lookback_days=100, slate_days=7),
}
