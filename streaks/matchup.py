"""Matchup flags: why a streak is extra attractive tonight.

A streak is *preferable* when at least one flag fires:

  soft_d    The opponent gives up a lot of this stat. We sum each opponent's box
            score in its last `def_window` games ("allowed per game"), rank the
            league, and flag the softest `soft_d_pct` share. Ranks are per stat,
            so a team can be soft against rebounds and tough against 3PTM.
  hot       The player is in form: last-5 average >= `hot_ratio` x their average
            over the earlier games in the window.
  weak_opp  The opponent is clearly the worse team: win% at least `weak_opp_gap`
            below the player's team, and below .500.

Rows with more flags sort above rows with fewer. Everything is computed from data
we already load, so no extra API calls are needed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean

from .config import StatDef
from .engine import Settings, Streak
from .espn import Slate, Team, TeamGame


@dataclass
class Flag:
    kind: str   # soft_d | hot | weak_opp
    text: str   # short human label for the card


@dataclass
class DefenseRow:
    allowed: float
    rank: int       # 1 = allows the most
    n_teams: int


def ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def defense_table(team_log: dict[str, list[TeamGame]], stat: StatDef, window: int,
                  min_games: int = 3) -> dict[str, DefenseRow]:
    avgs = {tid: mean(stat.extract(g.allowed) for g in log[:window])
            for tid, log in team_log.items() if len(log) >= min_games}
    order = sorted(avgs, key=lambda t: avgs[t], reverse=True)
    return {tid: DefenseRow(avgs[tid], i + 1, len(order)) for i, tid in enumerate(order)}


def win_pct(rec: tuple[int, int, int] | None, min_games: int) -> float | None:
    if not rec:
        return None
    w, l, t = rec
    gp = w + l + t
    return (w + 0.5 * t) / gp if gp >= min_games else None


def form(streak: Streak) -> tuple[float | None, float | None]:
    """(last-5 average, average of the earlier games in the window)."""
    vals = [streak.stat.extract(g.stats) for g in streak.player.games[:15]]
    if len(vals) < 8:
        return (mean(vals[:5]) if len(vals) >= 5 else None), None
    return mean(vals[:5]), mean(vals[5:])


def evaluate(streak: Streak, slate: Slate | None, defense: dict[str, DefenseRow],
             teams: dict[str, Team], s: Settings) -> list[Flag]:
    flags: list[Flag] = []
    m = slate.matchups.get(streak.player.team_id) if slate else None

    if m:
        opp = teams.get(m.opponent_id)
        opp_abbr = opp.abbr if opp else "Opp"
        d = defense.get(m.opponent_id)
        if d and d.rank <= max(1, math.ceil(d.n_teams * s.soft_d_pct)):
            most = "most" if d.rank == 1 else f"{ordinal(d.rank)}-most"
            flags.append(Flag("soft_d", f"{opp_abbr} allows {most} {streak.stat.label}"))

        mine = win_pct(slate.records.get(m.team_id), s.min_record_games)
        theirs = win_pct(slate.records.get(m.opponent_id), s.min_record_games)
        if mine is not None and theirs is not None and theirs < 0.5 and mine - theirs >= s.weak_opp_gap:
            w, l, t = slate.records[m.opponent_id]
            rec = f"{w}-{l}" + (f"-{t}" if t else "")
            flags.append(Flag("weak_opp", f"Weak opp {rec}"))

    l5, base = form(streak)
    if l5 is not None and base and l5 >= s.hot_ratio * base:
        flags.append(Flag("hot", f"L5 avg {l5:.1f} (+{(l5 / base - 1) * 100:.0f}%)"))
    return flags
