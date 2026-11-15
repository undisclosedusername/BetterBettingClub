"""Unit tests for the streak engine plus an offline end-to-end run.

The end-to-end test writes synthetic responses shaped like ESPN's scoreboard
and summary payloads into a cache dir, then runs the CLI with --offline.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from streaks.config import LEAGUES, NBA_STATS, NFL_STATS
from streaks.engine import Settings, best_streak, near_streak, perfect_streak
from streaks.espn import EspnClient, Player, PlayerGame, parse_summary

PTS = next(s for s in NBA_STATS if s.key == "pts")
REB = next(s for s in NBA_STATS if s.key == "reb")


def mk_player(key, values):
    games = [PlayerGame(str(i), date(2026, 11, 13) - timedelta(days=i), "1", "2", {key: v})
             for i, v in enumerate(values)]
    return Player("p", "Test Player", "T. Player", games=games)


def test_perfect_streak():
    assert perfect_streak([12, 15, 11, 9, 20], 10) == 3
    assert perfect_streak([], 10) == 0


def test_near_streak_trims_trailing_miss():
    # hit hit miss hit hit hit miss -> best window with 1 miss is 5/6
    assert near_streak([1, 1, 0, 1, 1, 1, 0, 1], 1, 1) == (5, 6)
    # window can't end on a miss
    assert near_streak([1, 1, 1, 0, 0], 1, 1) == (3, 3)


def test_best_streak_prefers_longest_then_highest():
    # avg ~14.5; 10+ for all 15 games, 15+ only the last 3
    vals = [16, 17, 15] + [12] * 12
    s = best_streak(mk_player("PTS", vals), PTS, Settings())
    assert (s.threshold, s.hits, s.games) == (12, 15, 15)


def test_floor_ratio_blocks_trivial_lines():
    vals = [30] * 10
    s = best_streak(mk_player("PTS", vals), PTS, Settings())
    assert s.threshold == 30  # 15+ (0.5x avg) is the floor; 30 is the longest & highest


def test_near_streak_used_when_no_perfect():
    vals = [8, 9, 3, 8, 8, 9, 8, 8, 9, 8, 8, 2]
    s = best_streak(mk_player("REB", vals), REB, Settings(min_games=4))
    assert not s.perfect and (s.hits, s.games) == (10, 11)


def test_nfl_parse_and_stats():
    ev = {"id": "e1", "date": "2026-11-09T18:00Z"}
    summ = {"boxscore": {"players": [
        {"team": {"id": "1", "abbreviation": "KC", "color": "e31837"},
         "statistics": [
             {"name": "passing", "labels": ["C/ATT", "YDS", "AVG", "TD", "INT", "SACKS", "QBR", "RTG"],
              "athletes": [{"athlete": {"id": "10", "displayName": "Q Back", "shortName": "Q. Back"},
                            "stats": ["24/35", "281", "8.0", "2", "0", "1-7", "70.1", "110.2"]}]},
             {"name": "rushing", "labels": ["CAR", "YDS", "AVG", "TD", "LONG"],
              "athletes": [{"athlete": {"id": "11", "displayName": "R Back", "shortName": "R. Back"},
                            "stats": ["18", "92", "5.1", "1", "22"]}]},
             {"name": "receiving", "labels": ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"],
              "athletes": [{"athlete": {"id": "11", "displayName": "R Back", "shortName": "R. Back"},
                            "stats": ["3", "21", "7.0", "1", "12", "4"]}]}]},
        {"team": {"id": "2", "abbreviation": "BUF"}, "statistics": []}]}}
    players, teams = {}, {}
    parse_summary("nfl", ev, summ, players, teams)
    qb, rb = players["10"].games[0].stats, players["11"].games[0].stats
    assert qb["passing.CMP"] == 24 and qb["passing.YDS"] == 281
    stat = {s.key: s for s in NFL_STATS}
    assert stat["rush_rec_yds"].extract(rb) == 113
    assert stat["anytime_td"].extract(rb) == 2


# ---------------------------------------------------------------- end-to-end

def nba_line(pts, reb, ast, tpm):
    return [ "30", f"{pts//2}-{pts}", f"{tpm}-{tpm+2}", "0-0", "1", str(reb-1), str(reb),
             str(ast), "1", "0", "2", "2", "+3", str(pts)]


LABELS = ["MIN", "FG", "3PT", "FT", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TO", "PF", "+/-", "PTS"]


def write_fixtures(cache: Path, target: date, n_games: int):
    client = EspnClient(LEAGUES["nba"], cache)
    events = []
    for i in range(1, n_games + 1):
        d = target - timedelta(days=i)
        eid = f"g{i}"
        ev = {"id": eid, "date": f"{d}T23:30Z", "status": {"type": {"completed": True}},
              "competitions": [{"competitors": [{"team": {"id": "4"}}, {"team": {"id": "9"}}]}]}
        events.append(ev)
        a_pts = 20 if i <= 7 else 6          # star: 18+ pts in last 7 games
        b_reb = 9 if i != 3 else 4           # big: 10/11-ish rebound near streak
        summ = {"boxscore": {"players": [
            {"team": {"id": "4", "abbreviation": "CHI", "displayName": "Chicago Bulls", "color": "ce1141",
                      "logo": ""},
             "statistics": [{"labels": LABELS, "athletes": [
                 {"athlete": {"id": "100", "displayName": "Alpha Star", "shortName": "A. Star"},
                  "stats": nba_line(a_pts, 4, 5, 3)},
                 {"athlete": {"id": "101", "displayName": "Bravo Big", "shortName": "B. Big"},
                  "stats": nba_line(10, b_reb, 2, 0)},
                 {"athlete": {"id": "102", "displayName": "Charlie Bench", "shortName": "C. Bench"},
                  "didNotPlay": True, "stats": []}]}]},
            {"team": {"id": "9", "abbreviation": "MEM", "displayName": "Memphis Grizzlies",
                      "color": "5d76a9"}, "statistics": []}]}}
        client._cache_path("summary", {"event": eid}).write_text(json.dumps(summ))
    start = target - timedelta(days=45)
    end = target - timedelta(days=1)
    cur = start
    while cur <= end:  # mirror scoreboard_range chunking
        ce = min(cur + timedelta(days=13), end)
        evs = [e for e in events if cur <= date.fromisoformat(e["date"][:10]) <= ce]
        client._cache_path("scoreboard", {"dates": f"{cur:%Y%m%d}-{ce:%Y%m%d}", "limit": 1000}) \
            .write_text(json.dumps({"events": evs}))
        cur = ce + timedelta(days=1)
    today = {"id": "t", "date": f"{target}T23:00Z", "status": {"type": {"completed": False}},
             "competitions": [{"competitors": [
                 {"homeAway": "home", "team": {"id": "4", "abbreviation": "CHI"},
                  "records": [{"type": "total", "summary": "9-3"}]},
                 {"homeAway": "away", "team": {"id": "9", "abbreviation": "MEM"},
                  "records": [{"type": "total", "summary": "2-10"}]}]}]}
    client._cache_path("scoreboard", {"dates": target.strftime("%Y%m%d"), "limit": 500}) \
        .write_text(json.dumps({"events": [today]}))


def test_end_to_end_offline(tmp_path):
    from streaks.cli import main
    target = date(2026, 11, 14)
    write_fixtures(tmp_path / "cache", target, n_games=12)
    out_json = tmp_path / "out.json"
    rc = main(["nba", "--date", str(target), "--offline", "--cache", str(tmp_path / "cache"),
               "--out", str(tmp_path / "out"), "--no-png", "--json", str(out_json)])
    assert rc == 0
    result = json.loads(out_json.read_text())
    cards = {c["key"]: {r["player"]["name"]: r for r in c["rows"]} for c in result["stats"]}
    star = cards["pts"]["Alpha Star"]
    assert star["threshold"] == 20 and star["games"] == 7
    assert cards["reb"]["Bravo Big"]["hits"] == 11 and cards["reb"]["Bravo Big"]["games"] == 12
    assert "Charlie Bench" not in cards["pts"]
    # matchup: CHI (9-3) hosts MEM (2-10) -> weak opponent flag on every CHI row
    assert star["opponent"] == "9" and star["home"] is True and star["preferable"]
    assert any(f["kind"] == "weak_opp" and f["text"] == "Weak opp 2-10" for f in star["flags"])
    # Alpha jumped from 6 to 20 points -> hot
    assert any(f["kind"] == "hot" for f in star["flags"])
    assert (tmp_path / "out" / f"nba-{target}" / f"nba-{target}-pts.html").exists()


# ---------------------------------------------------------------- matchups

from streaks.engine import Streak
from streaks.espn import Matchup, Slate, Team, TeamGame
from streaks.matchup import defense_table, evaluate, workload_share
from streaks.weather import WeatherReading

RUSH_YDS = next(s for s in NFL_STATS if s.key == "rush_yds")
PASS_YDS = next(s for s in NFL_STATS if s.key == "pass_yds")


def _slate(rec_me=(5, 5, 0), rec_opp=(5, 5, 0)):
    return Slate([date(2026, 11, 14)], [{}], {"1": Matchup("e", "1", "2", True, "")},
                 {"1": rec_me, "2": rec_opp})


def _log(allowed_by_team):
    return {tid: [TeamGame(str(i), date(2026, 11, 1) + timedelta(days=i), "x", {}, {"REB": v})
                  for i in range(5)] for tid, v in allowed_by_team.items()}


TEAMS = {"1": Team("1", "AAA", "A"), "2": Team("2", "BBB", "B")}


def test_soft_defense_flag_and_rank():
    defense = defense_table(_log({"2": 55, "3": 44, "4": 40, "5": 38}), REB, 15)
    assert defense["2"].rank == 1 and defense["5"].rank == 4
    st = Streak(mk_player("REB", [8] * 10), REB, 6, 10, 10)
    flags = evaluate(st, _slate(), defense, TEAMS, Settings())
    assert [f.kind for f in flags] == ["soft_d"] and "BBB allows most Rebounds" == flags[0].text


def test_tough_defense_no_flag():
    defense = defense_table(_log({"2": 30, "3": 44, "4": 40, "5": 38}), REB, 15)
    st = Streak(mk_player("REB", [8] * 10), REB, 6, 10, 10)
    assert evaluate(st, _slate(), defense, TEAMS, Settings()) == []


def test_soft_defense_needs_a_meaningful_gap_not_just_rank():
    # "2" is technically rank 1 (allows the most), but the league is bunched
    # tightly around ~40 - that's not a clearly-bad defense, just a noisy #1.
    defense = defense_table(_log({"2": 42, "3": 41, "4": 40, "5": 39}), REB, 15)
    assert defense["2"].rank == 1
    st = Streak(mk_player("REB", [8] * 10), REB, 6, 10, 10)
    assert evaluate(st, _slate(), defense, TEAMS, Settings()) == []


def test_weak_opponent_needs_gap_and_losing_record():
    st = Streak(mk_player("REB", [8] * 10), REB, 6, 10, 10)
    assert [f.kind for f in evaluate(st, _slate((8, 2, 0), (3, 7, 0)), {}, TEAMS, Settings())] == ["weak_opp"]
    assert evaluate(st, _slate((8, 2, 0), (6, 4, 0)), {}, TEAMS, Settings()) == []   # opp above .500
    assert evaluate(st, _slate((4, 6, 0), (3, 7, 0)), {}, TEAMS, Settings()) == []   # gap too small
    assert evaluate(st, _slate((2, 0, 0), (0, 2, 0)), {}, TEAMS, Settings()) == []   # too early


def _team_index(totals_by_event):
    return {"1": {eid: TeamGame(eid, date(2026, 11, 1), "2", {"REB": v}, {})
                  for eid, v in totals_by_event.items()}}


def test_workload_share_computes_team_share():
    player = mk_player("REB", [8] * 10)  # event ids "0".."9", team "1"
    idx = _team_index({str(i): 20 for i in range(10)})
    assert workload_share(player, REB, idx, window=6) == 0.4


def test_workload_share_ignores_games_missing_from_team_log():
    player = mk_player("REB", [8] * 10)
    idx = _team_index({str(i): 20 for i in range(2)})  # only 2 of 10 games indexed
    assert workload_share(player, REB, idx, window=6) is None  # fewer than 3 usable games


def test_volume_flag_fires_on_big_team_share():
    st = Streak(mk_player("REB", [8] * 10), REB, 6, 10, 10)
    idx = _team_index({str(i): 20 for i in range(10)})  # 40% share -> above REB's 0.30 threshold
    flags = evaluate(st, _slate(), {}, TEAMS, Settings(), team_index=idx)
    assert [f.kind for f in flags] == ["volume"] and "40% of team Rebounds" in flags[0].text


def test_volume_flag_skipped_below_threshold():
    st = Streak(mk_player("REB", [8] * 10), REB, 6, 10, 10)
    idx = _team_index({str(i): 40 for i in range(10)})  # only 20% share
    assert evaluate(st, _slate(), {}, TEAMS, Settings(), team_index=idx) == []


def test_weather_flag_fires_on_rain_or_wind():
    st = Streak(mk_player("rushing.YDS", [60] * 10), RUSH_YDS, 40, 10, 10)
    rain = evaluate(st, _slate(), {}, TEAMS, Settings(), event_weather={"e": WeatherReading(70, 5)})
    assert [f.kind for f in rain] == ["weather"] and "70% rain" in rain[0].text

    wind = evaluate(st, _slate(), {}, TEAMS, Settings(), event_weather={"e": WeatherReading(10, 28)})
    assert [f.kind for f in wind] == ["weather"] and "28mph wind" in wind[0].text


def test_weather_flag_skipped_below_threshold_missing_or_non_rushing_stat():
    st = Streak(mk_player("rushing.YDS", [60] * 10), RUSH_YDS, 40, 10, 10)
    assert evaluate(st, _slate(), {}, TEAMS, Settings(), event_weather={"e": WeatherReading(20, 8)}) == []
    assert evaluate(st, _slate(), {}, TEAMS, Settings(), event_weather={"e": None}) == []
    assert evaluate(st, _slate(), {}, TEAMS, Settings()) == []   # no event_weather at all

    pass_st = Streak(mk_player("passing.YDS", [250] * 10), PASS_YDS, 200, 10, 10)
    assert evaluate(pass_st, _slate(), {}, TEAMS, Settings(),
                    event_weather={"e": WeatherReading(90, 30)}) == []


def test_preferable_rows_sort_first(tmp_path):
    from streaks.pipeline import run
    target = date(2026, 11, 14)
    write_fixtures(tmp_path / "cache", target, n_games=12)
    client = EspnClient(LEAGUES["nba"], tmp_path / "cache", offline=True)
    res = run(LEAGUES["nba"], client, target, Settings(hot_ratio=99, weak_opp_gap=9, soft_d_pct=0),
              log=lambda *a: None)
    # no flags can fire -> nothing preferable
    assert not any(r["preferable"] for c in res["stats"] for r in c["rows"])
    res = run(LEAGUES["nba"], client, target, Settings(), log=lambda *a: None)
    for c in res["stats"]:
        prefs = [r["preferable"] for r in c["rows"]]
        assert prefs == sorted(prefs, reverse=True)
