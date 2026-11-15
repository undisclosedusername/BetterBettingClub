"""Offline unit tests for the weather lookup. No network calls - the cache is
pre-seeded on disk so WeatherClient never has to hit Open-Meteo."""
import json
from datetime import date, datetime

from streaks.weather import STADIUMS, WeatherClient, WeatherReading, game_weather


def test_stadium_table_marks_domes_and_outdoor():
    assert STADIUMS["NO"].roof == "dome"
    assert STADIUMS["ARI"].roof == "retractable"
    assert STADIUMS["SEA"].roof == "outdoor"


def test_game_weather_skips_dome_and_unknown_teams(tmp_path):
    client = WeatherClient(tmp_path)
    assert game_weather(client, "NO", "2026-11-15T18:00Z") is None    # dome
    assert game_weather(client, "ZZZ", "2026-11-15T18:00Z") is None   # unknown team
    assert game_weather(client, "SEA", "") is None                    # no kickoff time


def _seed_cache(client, lat, lon, day, times, precip, wind):
    cpath = client._cache_path(lat, lon, day)
    cpath.write_text(json.dumps(
        {"hourly": {"time": times, "precipitation_probability": precip, "wind_speed_10m": wind}}))


def test_weather_client_at_exact_hour_match(tmp_path):
    client = WeatherClient(tmp_path)
    stadium = STADIUMS["SEA"]
    _seed_cache(client, stadium.lat, stadium.lon, date(2026, 11, 15),
                ["2026-11-15T17:00", "2026-11-15T18:00", "2026-11-15T19:00"],
                [10, 70, 20], [5, 25, 8])
    assert client.at(stadium.lat, stadium.lon, datetime(2026, 11, 15, 18, 0)) == WeatherReading(70, 25)


def test_weather_client_at_falls_back_to_nearest_hour(tmp_path):
    client = WeatherClient(tmp_path)
    stadium = STADIUMS["SEA"]
    _seed_cache(client, stadium.lat, stadium.lon, date(2026, 11, 15),
                ["2026-11-15T17:00", "2026-11-15T19:00"], [10, 70], [5, 25])
    # 18:20 isn't in the data; 19:00 (40min away) is nearer than 17:00 (1h20 away)
    assert client.at(stadium.lat, stadium.lon, datetime(2026, 11, 15, 18, 20)) == WeatherReading(70, 25)


def test_weather_client_returns_none_for_missing_hours(tmp_path):
    client = WeatherClient(tmp_path)
    stadium = STADIUMS["SEA"]
    _seed_cache(client, stadium.lat, stadium.lon, date(2026, 11, 15), [], [], [])
    assert client.at(stadium.lat, stadium.lon, datetime(2026, 11, 15, 18, 0)) is None


def test_game_weather_uses_outdoor_stadium_and_cached_forecast(tmp_path):
    client = WeatherClient(tmp_path)
    stadium = STADIUMS["SEA"]
    _seed_cache(client, stadium.lat, stadium.lon, date(2026, 11, 15),
                ["2026-11-15T18:00"], [80], [30])
    assert game_weather(client, "SEA", "2026-11-15T18:00Z") == WeatherReading(80, 30)
