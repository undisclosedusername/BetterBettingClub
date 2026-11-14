"""One run = load data, find streaks, score matchups -> a JSON-ready dict.

The CLI, the PNG renderer and the website all consume this same structure, so a
web front end never needs to know anything about ESPN or the streak math.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .config import League, StatDef
from .engine import Settings, Streak, find_streaks
from .espn import EspnClient, Slate, Team, load_history, slate as load_slate
from .matchup import defense_table, evaluate

SCHEMA_VERSION = 1


def _team_json(t: Team | None) -> dict | None:
    if not t:
        return None
    return {"id": t.id, "abbr": t.abbr, "name": t.name, "logo": t.logo, "color": t.color}


def _row(st: Streak, flags, sl: Slate | None, teams: dict[str, Team]) -> dict:
    p = st.player
    m = sl.matchups.get(p.team_id) if sl else None
    return {
        "player": {"id": p.id, "name": p.name, "short": p.short_name, "headshot": p.headshot},
        "team": p.team_id,
        "opponent": m.opponent_id if m else None,
        "home": m.home if m else None,
        "start": m.start if m else None,
        "threshold": st.threshold,
        "threshold_label": st.threshold_label,
        "hits": st.hits,
        "games": st.games,
        "perfect": st.perfect,
        "preferable": bool(flags),
        "flags": [{"kind": f.kind, "text": f.text} for f in flags],
        "recent": [st.stat.extract(g.stats) for g in p.games[:10]],  # newest first, for sparklines
    }


def run(league: League, client: EspnClient, target: date, settings: Settings,
        stats: list[StatDef] | None = None, since: date | None = None,
        lookback_days: int | None = None, slate_days: int | None = None,
        all_teams: bool = False, progress=None, log=print) -> dict:
    stats = stats or league.stats
    teams: dict[str, Team] = {}
    sl = load_slate(client, target, slate_days or league.slate_days, teams)
    log(f"{league.name} {target}: {len(sl.events)} upcoming games on the slate")
    if not sl.events and not all_teams:
        return _result(league, target, sl, teams, [], settings)

    start = since or target - timedelta(days=lookback_days or league.default_lookback_days)
    log(f"Loading box scores {start} -> {target - timedelta(days=1)} ...")
    players, hist_teams, team_log = load_history(client, start, target - timedelta(days=1), progress)
    teams.update(hist_teams)   # box-score team data wins over scoreboard data
    log(f"  {len(players)} players, {len(team_log)} teams")

    team_filter = None if all_teams else sl.team_ids
    cards = []
    for stat in stats:
        defense = defense_table(team_log, stat, settings.def_window)
        scored = [(st, evaluate(st, sl, defense, teams, settings))
                  for st in find_streaks(players, stat, settings, team_filter)]
        if settings.preferable_first:
            # stable sort keeps the streak ranking inside each group
            scored.sort(key=lambda x: -len(x[1]))
        cards.append({
            "key": stat.key, "title": stat.title, "label": stat.label,
            "rows": [_row(st, fl, sl, teams) for st, fl in scored[: settings.top]],
            "defense": {tid: {"allowed": round(d.allowed, 1), "rank": d.rank}
                        for tid, d in defense.items()},
        })
    return _result(league, target, sl, teams, cards, settings)


def _result(league, target, sl, teams, cards, settings) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "league": league.key,
        "league_name": league.name,
        "date": str(target),
        "slate_dates": [str(d) for d in sl.dates],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "games": [{"id": m.event_id, "home": tid, "away": m.opponent_id, "start": m.start}
                  for tid, m in sl.matchups.items() if m.home],
        "records": {tid: list(r) for tid, r in sl.records.items()},
        "teams": {tid: _team_json(t) for tid, t in teams.items()},
        "settings": {k: getattr(settings, k) for k in settings.__dataclass_fields__},
        "stats": cards,
    }
