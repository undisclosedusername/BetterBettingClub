"""Thin ESPN public-API client with an on-disk cache, plus box-score parsing.

Everything comes from two endpoints that exist for both NBA and NFL:
  scoreboard?dates=YYYYMMDD[-YYYYMMDD]   -> which games happened / are scheduled
  summary?event=ID                       -> full box score for one game
Completed games never change, so their summaries are cached forever; re-runs
only fetch games played since the last run.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests

from .config import League

BASE = "https://site.api.espn.com/apis/site/v2/sports"
ET = ZoneInfo("America/New_York")


class Offline(RuntimeError):
    pass


class EspnClient:
    def __init__(self, league: League, cache_dir: Path, offline: bool = False, delay: float = 0.25):
        self.league = league
        self.cache_dir = Path(cache_dir) / league.key
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.delay = delay
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "streaks/1.0"

    # -- low level -------------------------------------------------------
    def _url(self, endpoint: str) -> str:
        return f"{BASE}/{self.league.sport}/{self.league.espn_league}/{endpoint}"

    def _cache_path(self, endpoint: str, params: dict) -> Path:
        key = endpoint + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        name = hashlib.sha1(key.encode()).hexdigest()[:16]
        return self.cache_dir / f"{endpoint}-{name}.json"

    def get(self, endpoint: str, params: dict, cache: bool = True) -> dict:
        path = self._cache_path(endpoint, params)
        if path.exists() and (cache or self.offline):
            return json.loads(path.read_text())
        if self.offline:
            raise Offline(f"not cached: {endpoint} {params}")
        for attempt in range(4):
            try:
                r = self.session.get(self._url(endpoint), params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(1.5 * 2 ** attempt)
        time.sleep(self.delay)
        if endpoint == "summary":
            # summaries carry full play-by-play; only the box score is used
            data = {"boxscore": data.get("boxscore", {})}
        path.write_text(json.dumps(data))
        return data

    # -- games -----------------------------------------------------------
    def scoreboard(self, d: date, cache: bool = True) -> list[dict]:
        data = self.get("scoreboard", {"dates": d.strftime("%Y%m%d"), "limit": 500}, cache=cache)
        return data.get("events", [])

    def scoreboard_range(self, start: date, end: date) -> list[dict]:
        """Events between start and end inclusive (past dates only -> cacheable)."""
        events: dict[str, dict] = {}
        cur = start
        while cur <= end:
            chunk_end = min(cur + timedelta(days=13), end)
            params = {"dates": f"{cur:%Y%m%d}-{chunk_end:%Y%m%d}", "limit": 1000}
            try:
                evs = self.get("scoreboard", params).get("events", [])
            except (requests.HTTPError, Offline):
                # Fallback: day by day (some ESPN leagues reject ranges)
                evs = []
                d = cur
                while d <= chunk_end:
                    evs += self.scoreboard(d)
                    d += timedelta(days=1)
            for e in evs:
                events[e["id"]] = e
            cur = chunk_end + timedelta(days=1)
        return sorted(events.values(), key=lambda e: e["date"])

    def summary(self, event_id: str) -> dict:
        return self.get("summary", {"event": event_id})


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class Team:
    id: str
    abbr: str
    name: str
    logo: str = ""
    color: str = "333333"


@dataclass
class PlayerGame:
    event_id: str
    game_date: date
    team_id: str
    opponent_id: str
    stats: dict[str, float]


@dataclass
class Player:
    id: str
    name: str
    short_name: str
    headshot: str = ""
    games: list[PlayerGame] = field(default_factory=list)  # most recent first

    @property
    def team_id(self) -> str:
        return self.games[0].team_id if self.games else ""


def is_completed(event: dict) -> bool:
    st = event.get("status") or event.get("competitions", [{}])[0].get("status", {})
    return bool(st.get("type", {}).get("completed"))


def event_date_et(event: dict) -> date:
    dt = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date()


def event_team_ids(event: dict) -> list[str]:
    comp = event.get("competitions", [{}])[0]
    return [c["team"]["id"] for c in comp.get("competitors", [])]


def _num(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip()
    if s in ("", "--", "-"):
        return None
    try:
        return float(s.replace("+", ""))
    except ValueError:
        return None


# ESPN NBA labels -> our keys
_NBA_LABELS = {"PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK",
               "TO": "TO", "OREB": "OREB", "DREB": "DREB", "PF": "PF", "MIN": "MIN"}
_MADE_ATT = {"FG": ("FGM", "FGA"), "3PT": ("3PM", "3PA"), "FT": ("FTM", "FTA")}


def _parse_group(league_key: str, group: dict, raw: list) -> dict[str, float]:
    labels = group.get("labels") or group.get("names") or []
    gname = (group.get("name") or "").lower()
    out: dict[str, float] = {}
    for label, val in zip(labels, raw):
        label = str(label).upper()
        if league_key == "nba":
            if label in _MADE_ATT and "-" in str(val):
                m, a = str(val).split("-", 1)
                k1, k2 = _MADE_ATT[label]
                out[k1], out[k2] = _num(m) or 0.0, _num(a) or 0.0
            elif label in _NBA_LABELS:
                n = _num(val)
                if n is not None:
                    out[_NBA_LABELS[label]] = n
        else:
            if label == "C/ATT" and "/" in str(val):
                c, a = str(val).split("/", 1)
                out[f"{gname}.CMP"], out[f"{gname}.ATT"] = _num(c) or 0.0, _num(a) or 0.0
            elif label == "SACKS" and gname == "passing" and "-" in str(val):
                s, y = str(val).split("-", 1)
                out[f"{gname}.SACKED"] = _num(s) or 0.0
            else:
                n = _num(val)
                if n is not None:
                    out[f"{gname}.{label}"] = n
    return out


def parse_summary(league_key: str, event: dict, summary: dict,
                  players: dict[str, Player], teams: dict[str, Team]) -> dict[str, dict[str, float]]:
    """Fold one game's box score into the players/teams dicts.

    Returns {team_id: summed box-score line} for the game (used for defense ranks).
    """
    totals: dict[str, dict[str, float]] = {}
    gdate = event_date_et(event)
    box = summary.get("boxscore", {})
    team_ids = [t["team"]["id"] for t in box.get("players", [])]
    for side in box.get("players", []):
        t = side["team"]
        teams.setdefault(t["id"], Team(
            id=t["id"], abbr=t.get("abbreviation", ""), name=t.get("displayName", ""),
            logo=t.get("logo") or (t.get("logos") or [{}])[0].get("href", ""),
            color=t.get("color") or "333333"))
        opp = next((i for i in team_ids if i != t["id"]), "")
        lines: dict[str, dict[str, float]] = {}
        meta: dict[str, dict] = {}
        for group in side.get("statistics", []):
            for a in group.get("athletes", []):
                if a.get("didNotPlay") or not a.get("stats"):
                    continue
                ath = a["athlete"]
                parsed = _parse_group(league_key, group, a["stats"])
                if league_key == "nba" and parsed.get("MIN", 1) == 0:
                    continue
                lines.setdefault(ath["id"], {}).update(parsed)
                meta[ath["id"]] = ath
        for pid, stats in lines.items():
            ath = meta[pid]
            p = players.get(pid)
            if p is None:
                p = players[pid] = Player(
                    id=pid, name=ath.get("displayName", ""),
                    short_name=ath.get("shortName") or ath.get("displayName", ""),
                    headshot=(ath.get("headshot") or {}).get("href", ""))
            p.games.append(PlayerGame(event["id"], gdate, t["id"], opp, stats))
            tot = totals.setdefault(t["id"], {})
            for k, v in stats.items():
                tot[k] = tot.get(k, 0.0) + v
    return totals


@dataclass
class TeamGame:
    event_id: str
    game_date: date
    opponent_id: str
    scored: dict[str, float]    # this team's summed box score
    allowed: dict[str, float]   # opponent's summed box score


def load_history(client: EspnClient, start: date, end: date, progress=None
                 ) -> tuple[dict[str, Player], dict[str, Team], dict[str, list[TeamGame]]]:
    players: dict[str, Player] = {}
    teams: dict[str, Team] = {}
    team_log: dict[str, list[TeamGame]] = {}
    seen_events: set[str] = set()
    events = [e for e in client.scoreboard_range(start, end) if is_completed(e)]
    for i, ev in enumerate(events, 1):
        if progress:
            progress(i, len(events))
        try:
            summ = client.summary(ev["id"])
        except Offline:
            continue
        if ev["id"] in seen_events:
            continue
        seen_events.add(ev["id"])
        totals = parse_summary(client.league.key, ev, summ, players, teams)
        if len(totals) == 2:
            a, b = totals
            for me, opp in ((a, b), (b, a)):
                team_log.setdefault(me, []).append(
                    TeamGame(ev["id"], event_date_et(ev), opp, totals[me], totals[opp]))
    for p in players.values():
        # dedupe (an event can appear twice across scoreboard chunks) and sort newest first
        seen, uniq = set(), []
        for g in sorted(p.games, key=lambda g: g.game_date, reverse=True):
            if g.event_id not in seen:
                seen.add(g.event_id)
                uniq.append(g)
        p.games = uniq
    for log in team_log.values():
        log.sort(key=lambda g: g.game_date, reverse=True)
    return players, teams, team_log


@dataclass
class Matchup:
    event_id: str
    team_id: str
    opponent_id: str
    home: bool
    start: str          # ISO kickoff / tip time (UTC)


@dataclass
class Slate:
    dates: list[date]
    events: list[dict]
    matchups: dict[str, Matchup]            # team_id -> matchup
    records: dict[str, tuple[int, int, int]]  # team_id -> (wins, losses, ties)

    @property
    def team_ids(self) -> set[str]:
        return set(self.matchups)


def parse_record(summary: str) -> tuple[int, int, int] | None:
    try:
        parts = [int(x) for x in summary.split("-")]
    except (ValueError, AttributeError):
        return None
    if len(parts) == 2:
        return parts[0], parts[1], 0
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return None


def team_from_competitor(c: dict) -> Team:
    t = c["team"]
    return Team(id=t["id"], abbr=t.get("abbreviation", ""), name=t.get("displayName", ""),
                logo=t.get("logo") or (t.get("logos") or [{}])[0].get("href", ""),
                color=t.get("color") or "333333")


def slate(client: EspnClient, d: date, days: int = 1, teams: dict[str, Team] | None = None) -> Slate:
    """Games from d through d+days-1 (unfinished ones only), who plays whom, and records.

    If a team plays twice in the window, its first game is used.
    """
    dates = [d + timedelta(days=i) for i in range(days)]
    events, matchups, records = [], {}, {}
    for day in dates:
        for e in client.scoreboard(day, cache=client.offline):
            if is_completed(e):
                continue
            comps = e.get("competitions", [{}])[0].get("competitors", [])
            if len(comps) != 2:
                continue
            events.append(e)
            for c, o in ((comps[0], comps[1]), (comps[1], comps[0])):
                tid = c["team"]["id"]
                if teams is not None and tid not in teams:
                    teams[tid] = team_from_competitor(c)
                if tid in matchups:
                    continue
                matchups[tid] = Matchup(e["id"], tid, o["team"]["id"], c.get("homeAway") == "home", e["date"])
                for r in c.get("records") or []:
                    if r.get("type") in (None, "total") or r.get("name") == "overall":
                        rec = parse_record(r.get("summary", ""))
                        if rec:
                            records[tid] = rec
                            break
    return Slate(dates, events, matchups, records)
