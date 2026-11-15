"""The Odds API client: matches today's streak picks to real sportsbook player-prop lines.

Credit-conscious by design (free tier is 500 credits/month, cost = markets x regions):
  * only the /events endpoint (free) is used to find today's game ids
  * per-event odds are only fetched for the markets that actually appear in that
    day's top streak picks, de-duplicated by (event, market) - not every market
    for every game
  * responses are cached to disk per UTC day, so re-running the same day's build
    (e.g. a manual retry) never re-spends credits
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import requests

BASE = "https://api.the-odds-api.com/v4"

SPORT_KEYS = {"nba": "basketball_nba", "nfl": "americanfootball_nfl"}

# stat key -> (market key, kind). kind "ou" = Over/Under with a point line,
# "yes" = binary yes/no market (only threshold==1 can be matched against it).
MARKETS: dict[str, dict[str, tuple[str, str]]] = {
    "nba": {
        "pts": ("player_points", "ou"),
        "reb": ("player_rebounds", "ou"),
        "ast": ("player_assists", "ou"),
        "3pm": ("player_threes", "ou"),
        "pra": ("player_points_rebounds_assists", "ou"),
        "pr": ("player_points_rebounds", "ou"),
        "pa": ("player_points_assists", "ou"),
        "ra": ("player_rebounds_assists", "ou"),
        "stl": ("player_steals", "ou"),
        "blk": ("player_blocks", "ou"),
    },
    "nfl": {
        "pass_yds": ("player_pass_yds", "ou"),
        "pass_td": ("player_pass_tds", "ou"),
        "pass_cmp": ("player_pass_completions", "ou"),
        "rush_yds": ("player_rush_yds", "ou"),
        "rush_att": ("player_rush_attempts", "ou"),
        "rec_yds": ("player_reception_yds", "ou"),
        "rec": ("player_receptions", "ou"),
        "rush_rec_yds": ("player_rush_reception_yds", "ou"),
        "anytime_td": ("player_anytime_td", "yes"),
        "int": ("player_defensive_interceptions", "ou"),
        "sacks": ("player_sacks", "ou"),
        "tackles": ("player_tackles_assists", "ou"),
    },
}


class OddsError(RuntimeError):
    pass


def _normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", name.lower())
    return re.sub(r"[^a-z]", "", name)


def _team_key(name: str) -> str:
    """Last significant word of a team's display name (its nickname), e.g.
    'Los Angeles Lakers' -> 'lakers'. Nicknames are unique within a league,
    unlike full city names which can differ slightly between ESPN and the odds feed."""
    words = re.sub(r"[^a-zA-Z ]", "", name).split()
    return (words[-1] if words else name).lower()


@dataclass
class Line:
    bookmaker: str
    point: float
    over_price: int | None
    under_price: int | None


class OddsClient:
    def __init__(self, api_key: str, cache_dir: Path, regions: str = "us"):
        self.api_key = api_key
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.regions = regions
        self.session = requests.Session()
        self.last_headers: dict[str, str] = {}

    def _cache_path(self, kind: str, key: str, day: date) -> Path:
        name = hashlib.sha1(key.encode()).hexdigest()[:16]
        return self.cache_dir / f"{day:%Y%m%d}-{kind}-{name}.json"

    def _get(self, path: str, params: dict, kind: str, cache_key: str, day: date) -> dict | list:
        cpath = self._cache_path(kind, cache_key, day)
        if cpath.exists():
            return json.loads(cpath.read_text())
        r = self.session.get(f"{BASE}{path}", params={**params, "apiKey": self.api_key}, timeout=20)
        self.last_headers = {k: v for k, v in r.headers.items() if k.lower().startswith("x-requests")}
        if not r.ok:
            raise OddsError(f"{path} -> {r.status_code}: {r.text[:300]}")
        data = r.json()
        cpath.write_text(json.dumps(data))
        return data

    def events(self, league_key: str, day: date) -> list[dict]:
        sport = SPORT_KEYS[league_key]
        return self._get(f"/sports/{sport}/events", {}, "events", sport, day)

    def event_odds(self, league_key: str, event_id: str, markets: list[str], day: date) -> dict:
        sport = SPORT_KEYS[league_key]
        key = f"{event_id}:{','.join(sorted(markets))}"
        return self._get(
            f"/sports/{sport}/events/{event_id}/odds",
            {"regions": self.regions, "markets": ",".join(markets), "oddsFormat": "american"},
            "eventodds", key, day,
        )


def match_event(events: list[dict], home_name: str, away_name: str) -> dict | None:
    h, a = _team_key(home_name), _team_key(away_name)
    for e in events:
        if _team_key(e.get("home_team", "")) == h and _team_key(e.get("away_team", "")) == a:
            return e
    return None


def best_line(event_odds: dict, market_key: str, player_name: str, kind: str) -> Line | None:
    """Across all bookmakers, the line with the worst (safest/shortest) 'over' price
    - i.e. the most conservative number a bettor could actually get, not a cherry-picked one."""
    target = _normalize_name(player_name)
    best: Line | None = None
    for bm in event_odds.get("bookmakers", []):
        for mk in bm.get("markets", []):
            if mk.get("key") != market_key:
                continue
            by_point: dict[float, dict[str, int]] = {}
            for out in mk.get("outcomes", []):
                if _normalize_name(out.get("description", "")) != target:
                    continue
                point = out.get("point", 0.5 if kind == "yes" else None)
                if point is None:
                    continue
                side = "over" if out["name"] in ("Over", "Yes") else "under"
                by_point.setdefault(point, {})[side] = out["price"]
            for point, sides in by_point.items():
                cand = Line(bm["key"], point, sides.get("over"), sides.get("under"))
                if cand.over_price is None:
                    continue
                if best is None or cand.over_price < best.over_price:
                    best = cand
    return best


def recent_hit_rate(values: list[float], point: float, window: int = 15) -> tuple[int, int]:
    """(hits, games) of the player's last `window` games against a sportsbook line.

    This is deliberately separate from the ladder streak math in engine.py: the
    book's point for today can be a completely different number from the ladder
    threshold our headline streak was built on, so it needs its own hit-rate."""
    recent = values[:window]
    return sum(1 for v in recent if v >= point), len(recent)


def attach_lines(result: dict, client: OddsClient, league_key: str,
                 players: dict, stats_by_key: dict, top_n: int = 12, log=print) -> dict:
    """Mutates result's card rows in place, adding a 'book' field (current sportsbook
    line + price + hit-rate AT THAT LINE) to the rows that got a match. Returns usage stats."""
    day = datetime.now(timezone.utc).date()
    market_map = MARKETS.get(league_key, {})
    teams = result["teams"]

    candidates = []
    for card in result["stats"]:
        mk = market_map.get(card["key"])
        if not mk:
            continue
        market_key, kind = mk
        for row in card["rows"][:top_n]:
            if kind == "yes" and row["threshold"] != 1:
                continue  # no book market for e.g. "2+ Anytime TD"
            candidates.append((card, mk, row))

    try:
        events = client.events(league_key, day)
    except OddsError as e:
        log(f"  odds: could not list events ({e})")
        return {"matched": 0, "checked": 0}

    needed: dict[str, set[str]] = {}
    event_for_row: dict[int, dict] = {}
    for i, (card, (market_key, kind), row) in enumerate(candidates):
        home_id = row["team"] if row["home"] else row["opponent"]
        away_id = row["opponent"] if row["home"] else row["team"]
        if home_id is None or away_id is None:
            continue
        home_name = (teams.get(home_id) or {}).get("name", "")
        away_name = (teams.get(away_id) or {}).get("name", "")
        ev = match_event(events, home_name, away_name)
        if not ev:
            continue
        event_for_row[i] = ev
        needed.setdefault(ev["id"], set()).add(market_key)

    odds_by_event: dict[str, dict] = {}
    for event_id, markets in needed.items():
        try:
            odds_by_event[event_id] = client.event_odds(league_key, event_id, sorted(markets), day)
        except OddsError as e:
            log(f"  odds: event {event_id} failed ({e})")

    matched = checked = 0
    for i, (card, (market_key, kind), row) in enumerate(candidates):
        checked += 1
        ev = event_for_row.get(i)
        if not ev or ev["id"] not in odds_by_event:
            continue
        line = best_line(odds_by_event[ev["id"]], market_key, row["player"]["name"], kind)
        if not line:
            continue
        player = players.get(row["player"]["id"])
        stat = stats_by_key.get(card["key"])
        hits = games = None
        if player and stat:
            values = [stat.extract(g.stats) for g in player.games]
            hits, games = recent_hit_rate(values, line.point)
        row["book"] = {
            "bookmaker": line.bookmaker, "market": market_key,
            "point": line.point, "over_price": line.over_price, "under_price": line.under_price,
            "hits": hits, "games": games,
        }
        matched += 1

    log(f"  odds: {matched}/{checked} picks matched to a sportsbook line "
        f"(credits used: {client.last_headers.get('x-requests-last', '?')}, "
        f"remaining: {client.last_headers.get('x-requests-remaining', '?')})")
    return {"matched": matched, "checked": checked}
