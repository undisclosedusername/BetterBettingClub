"""Streak detection.

For every player and stat we look at the player's game log (most recent game
first) and, for each ladder threshold, measure:

  * the perfect streak: consecutive most-recent games at or above the threshold
  * the near streak: the longest recent window with at most `max_misses` misses
    (this is what produces rows like "10/11 Games")

Low thresholds trivially give endless streaks, so a threshold is only a
candidate when it's at least `floor_ratio` x the player's recent average (and
at least the stat's `min_line`). Among the candidates we keep the longest
streak, breaking ties by the higher threshold.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Sequence

from .config import StatDef
from .espn import Player


@dataclass
class Streak:
    player: Player
    stat: StatDef
    threshold: float
    hits: int
    games: int

    @property
    def perfect(self) -> bool:
        return self.hits == self.games

    @property
    def threshold_label(self) -> str:
        t = self.threshold
        return f"{int(t) if float(t).is_integer() else t}+"


@dataclass
class Settings:
    min_games: int = 4          # shortest perfect streak worth reporting
    min_near_games: int = 8     # shortest window for a near (one-miss) streak
    max_misses: int = 1
    floor_ratio: float = 0.5    # threshold must be >= this x recent average
    avg_window: int = 15        # games used for the recent average
    top: int = 10               # rows per card
    # matchup flags (see matchup.py)
    def_window: int = 15        # opponent's last N games used for "allowed" averages
    soft_d_pct: float = 0.15    # opponent in the softest X of the league vs this stat
    soft_d_gap: float = 1.15    # ...and allows at least this x the league-average
    hot_ratio: float = 1.35     # last-5 average >= this x the player's earlier average
    weak_opp_gap: float = 0.30  # opponent win% at least this far below player's team
    weak_opp_cap: float = 0.40  # ...and below this win% outright
    min_record_games: int = 4   # need this many games played before trusting records
    volume_window: int = 6      # recent games used for the team-share ("volume") check
    rain_precip_prob: float = 55   # % chance of precipitation at kickoff to flag
    rain_wind_mph: float = 20      # ...or sustained wind at kickoff to flag
    preferable_first: bool = True


def perfect_streak(values: Sequence[float], t: float) -> int:
    n = 0
    for v in values:
        if v >= t:
            n += 1
        else:
            break
    return n


def near_streak(values: Sequence[float], t: float, max_misses: int) -> tuple[int, int]:
    """(hits, games) for the longest most-recent window with <= max_misses misses.

    The window never ends (on its oldest side) with a miss - "9/10" means the
    10th game back was a hit, otherwise it would just be "9/9" plus noise.
    """
    misses = games = 0
    best = (0, 0)
    for v in values:
        if v >= t:
            games += 1
            best = (games - misses, games)
        else:
            misses += 1
            games += 1
            if misses > max_misses:
                break
    return best


def candidate_thresholds(stat: StatDef, values: Sequence[float], s: Settings) -> list[float]:
    recent = values[: s.avg_window]
    if not recent:
        return []
    avg = mean(recent)
    if avg < stat.min_avg:
        return []
    floor = max(stat.min_line, s.floor_ratio * avg)
    return [t for t in stat.ladder if t >= floor]


def best_streak(player: Player, stat: StatDef, s: Settings) -> Streak | None:
    values = [stat.extract(g.stats) for g in player.games]
    cands = candidate_thresholds(stat, values, s)
    if not cands:
        return None

    best: tuple[int, float] | None = None
    for t in cands:
        n = perfect_streak(values, t)
        if n >= s.min_games and (best is None or (n, t) > best):
            best = (n, t)
    if best:
        return Streak(player, stat, best[1], best[0], best[0])

    near: tuple[int, int, float] | None = None
    for t in cands:
        hits, games = near_streak(values, t, s.max_misses)
        if games >= s.min_near_games and hits < games:
            key = (hits, -games, t)
            if near is None or key > (near[0], -near[1], near[2]):
                near = (hits, games, t)
    if near:
        return Streak(player, stat, near[2], near[0], near[1])
    return None


def rank(streaks: list[Streak]) -> list[Streak]:
    """Perfect streaks first (longest first), then near streaks by hits."""
    return sorted(streaks, key=lambda x: (not x.perfect, -x.hits, x.games, -x.threshold, x.player.name))


def find_streaks(players: dict[str, Player], stat: StatDef, s: Settings,
                 team_filter: set[str] | None = None) -> list[Streak]:
    """All qualifying streaks for one stat, ranked (not truncated)."""
    out = []
    for p in players.values():
        if not p.games or (team_filter is not None and p.team_id not in team_filter):
            continue
        st = best_streak(p, stat, s)
        if st:
            out.append(st)
    return rank(out)
