"""Matchup flags: why a streak is extra attractive tonight.

A streak is *preferable* when at least one flag fires:

  soft_d    The opponent gives up a lot of this stat, clearly. We sum each
            opponent's box score in its last `def_window` games ("allowed per
            game"), rank the league, and require BOTH the softest `soft_d_pct`
            share of the league AND allowing at least `soft_d_gap` x the
            league average - rank alone is too easy to clear when the league
            is bunched tightly. Ranks are per stat, so a team can be soft
            against rebounds and tough against 3PTM.
  hot       The player is in form: last-5 average >= `hot_ratio` x their average
            over the earlier games in the window.
  weak_opp  The opponent is clearly the worse team: win% at least `weak_opp_gap`
            below the player's team, and below `weak_opp_cap` outright.
  volume    The player commands a big share of their team's own production for
            this stat over the last `volume_window` games - "they just feed the
            ball to this guy" - so their volume holds up even on an average
            matchup. Only fires for stats with a meaningful team-share concept
            (see StatDef.volume_threshold).
  weather   Rain or wind at kickoff favors a run-heavier game script. Only
            fires for rushing stats (see StatDef.weather_eligible), and only
            for outdoor stadiums (see weather.py).

Rows with more flags sort above rows with fewer. Soft_d, hot, weak_opp and
volume are computed entirely from data we already load; weather is the one
flag that needs an extra (free, keyless) API call - see weather.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean

from .config import StatDef
from .engine import Settings, Streak
from .espn import Player, Slate, Team, TeamGame
from .weather import WeatherReading


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


def team_event_index(team_log: dict[str, list[TeamGame]]) -> dict[str, dict[str, TeamGame]]:
    """team_id -> {event_id: TeamGame}, for O(1) "what did my team do that game" lookups."""
    return {tid: {g.event_id: g for g in log} for tid, log in team_log.items()}


def workload_share(player: Player, stat: StatDef, team_index: dict[str, dict[str, TeamGame]],
                   window: int) -> float | None:
    """This player's share of their own team's total for `stat`, over their last
    `window` games (weighted by game, not averaged per-game, so a big team game
    doesn't get diluted by a teammate's blowout-garbage-time line)."""
    num = den = 0.0
    counted = 0
    for g in player.games[:window]:
        tg = team_index.get(g.team_id, {}).get(g.event_id)
        if not tg:
            continue
        team_total = stat.extract(tg.scored)
        if team_total <= 0:
            continue
        num += stat.extract(g.stats)
        den += team_total
        counted += 1
    if counted < 3 or den <= 0:
        return None
    return num / den


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
             teams: dict[str, Team], s: Settings,
             team_index: dict[str, dict[str, TeamGame]] | None = None,
             event_weather: dict[str, WeatherReading | None] | None = None) -> list[Flag]:
    flags: list[Flag] = []
    m = slate.matchups.get(streak.player.team_id) if slate else None

    if m:
        opp = teams.get(m.opponent_id)
        opp_abbr = opp.abbr if opp else "Opp"
        d = defense.get(m.opponent_id)
        if d and d.rank <= max(1, math.ceil(d.n_teams * s.soft_d_pct)):
            league_avg = mean(x.allowed for x in defense.values())
            if league_avg > 0 and d.allowed >= s.soft_d_gap * league_avg:
                most = "most" if d.rank == 1 else f"{ordinal(d.rank)}-most"
                flags.append(Flag("soft_d", f"{opp_abbr} allows {most} {streak.stat.label}"))

        mine = win_pct(slate.records.get(m.team_id), s.min_record_games)
        theirs = win_pct(slate.records.get(m.opponent_id), s.min_record_games)
        if (mine is not None and theirs is not None
                and theirs < s.weak_opp_cap and mine - theirs >= s.weak_opp_gap):
            w, l, t = slate.records[m.opponent_id]
            rec = f"{w}-{l}" + (f"-{t}" if t else "")
            flags.append(Flag("weak_opp", f"Weak opp {rec}"))

    l5, base = form(streak)
    if l5 is not None and base and l5 >= s.hot_ratio * base:
        flags.append(Flag("hot", f"L5 avg {l5:.1f} (+{(l5 / base - 1) * 100:.0f}%)"))

    thresh = streak.stat.volume_threshold
    if thresh is not None and team_index is not None:
        share = workload_share(streak.player, streak.stat, team_index, s.volume_window)
        if share is not None and share >= thresh:
            flags.append(Flag("volume", f"{share * 100:.0f}% of team {streak.stat.label} (L{s.volume_window})"))

    if streak.stat.weather_eligible and m and event_weather is not None:
        wx = event_weather.get(m.event_id)
        if wx and (wx.precip_prob >= s.rain_precip_prob or wx.wind_mph >= s.rain_wind_mph):
            bits = []
            if wx.precip_prob >= s.rain_precip_prob:
                bits.append(f"{wx.precip_prob:.0f}% rain")
            if wx.wind_mph >= s.rain_wind_mph:
                bits.append(f"{wx.wind_mph:.0f}mph wind")
            flags.append(Flag("weather", " + ".join(bits) + " at kickoff"))
    return flags
