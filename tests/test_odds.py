"""Offline unit tests for the odds matching/attach logic. No network calls."""
from datetime import date, timedelta

from streaks.espn import Player, PlayerGame
from streaks.odds import (
    Line, OddsClient, _normalize_name, _team_key, attach_lines, best_line,
    match_event, recent_hit_rate,
)


def test_normalize_name_strips_accents_and_suffix():
    assert _normalize_name("Nikola Jokić") == _normalize_name("Nikola Jokic")
    assert _normalize_name("Gary Trent Jr.") == _normalize_name("gary trent")


def test_team_key_uses_nickname():
    assert _team_key("Los Angeles Lakers") == "lakers"
    assert _team_key("LA Clippers") == "clippers"


def test_match_event_by_nickname():
    events = [
        {"id": "e1", "home_team": "Los Angeles Lakers", "away_team": "Denver Nuggets"},
        {"id": "e2", "home_team": "Chicago Bulls", "away_team": "Minnesota Timberwolves"},
    ]
    assert match_event(events, "LA Lakers", "Denver Nuggets")["id"] == "e1"
    assert match_event(events, "Chicago Bulls", "Minnesota Timberwolves")["id"] == "e2"
    assert match_event(events, "Miami Heat", "Boston Celtics") is None


EVENT_ODDS = {
    "bookmakers": [
        {"key": "draftkings", "markets": [
            {"key": "player_points", "outcomes": [
                {"name": "Over", "description": "Nikola Jokic", "price": -115, "point": 24.5},
                {"name": "Under", "description": "Nikola Jokic", "price": -105, "point": 24.5},
            ]},
        ]},
        {"key": "fanduel", "markets": [
            {"key": "player_points", "outcomes": [
                {"name": "Over", "description": "Nikola Jokic", "price": -130, "point": 24.5},
                {"name": "Under", "description": "Nikola Jokic", "price": 105, "point": 24.5},
            ]},
        ]},
    ]
}


def test_best_line_picks_worst_over_price_across_books():
    line = best_line(EVENT_ODDS, "player_points", "Nikola Jokić", "ou")
    assert line.bookmaker == "fanduel" and line.over_price == -130 and line.point == 24.5


def test_best_line_no_match_for_unknown_player():
    assert best_line(EVENT_ODDS, "player_points", "Some Rando", "ou") is None


def test_recent_hit_rate_windows_and_counts():
    assert recent_hit_rate([30, 10, 25, 25, 1], 24.5, window=3) == (2, 3)
    assert recent_hit_rate([10, 10], 24.5) == (0, 2)


class FakeOddsClient(OddsClient):
    def __init__(self, events, odds_by_event):
        self._events = events
        self._odds_by_event = odds_by_event
        self.last_headers = {}

    def events(self, league_key, day):
        return self._events

    def event_odds(self, league_key, event_id, markets, day):
        return self._odds_by_event[event_id]


def _game(i, value):
    return PlayerGame(str(i), date(2026, 11, 14) - timedelta(days=i), "4", "9", {"PTS": value})


def test_attach_lines_end_to_end():
    result = {
        "teams": {"4": {"name": "Denver Nuggets", "abbr": "DEN"}, "9": {"name": "Memphis Grizzlies", "abbr": "MEM"}},
        "games": [{"id": "e1", "home": "4", "away": "9"}],
        "stats": [{
            "key": "pts", "label": "Points",
            "rows": [{"player": {"id": "p1", "name": "Nikola Jokic", "short": "N. Jokic"},
                      "team": "4", "opponent": "9", "home": True,
                      "threshold": 12, "hits": 11, "games": 11}],
        }],
    }
    players = {"p1": Player("p1", "Nikola Jokic", "N. Jokic",
                            games=[_game(i, v) for i, v in enumerate([30, 28, 10, 26, 25])])}
    stats_by_key = {"pts": type("S", (), {"extract": staticmethod(lambda line: line["PTS"])})()}
    events = [{"id": "e1", "home_team": "Denver Nuggets", "away_team": "Memphis Grizzlies"}]
    client = FakeOddsClient(events, {"e1": EVENT_ODDS})

    stats = attach_lines(result, client, "nba", players, stats_by_key, log=lambda *a: None)

    assert stats == {"matched": 1, "checked": 1}
    book = result["stats"][0]["rows"][0]["book"]
    assert book == {"bookmaker": "fanduel", "market": "player_points", "point": 24.5,
                    "over_price": -130, "under_price": 105, "hits": 4, "games": 5}
