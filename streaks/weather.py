"""Kickoff-hour weather for outdoor NFL stadiums, via Open-Meteo (free, no API
key, so unlike odds.py this can run on every site rebuild without a quota).

Bad weather (rain, wind) favors a run-heavy game script - fewer drop-backs,
more clock-killing carries - which is the "rushing props for rainy/windy
games" read real bettors use. Only matters for rushing-flavored stats, and
only for stadiums with no roof to close (see STADIUMS below).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

BASE = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class Stadium:
    lat: float
    lon: float
    roof: str  # "outdoor" | "dome" | "retractable"


# Home stadiums for every NFL team, keyed by ESPN team abbreviation. "dome"
# and "retractable" are treated as weather-proof (the roof is closed in bad
# weather) and never get a weather flag - only "outdoor" is checked.
STADIUMS: dict[str, Stadium] = {
    "ARI": Stadium(33.5276, -112.2626, "retractable"),
    "ATL": Stadium(33.7554, -84.4008, "dome"),
    "BAL": Stadium(39.2780, -76.6227, "outdoor"),
    "BUF": Stadium(42.7738, -78.7870, "outdoor"),
    "CAR": Stadium(35.2258, -80.8528, "outdoor"),
    "CHI": Stadium(41.8623, -87.6167, "outdoor"),
    "CIN": Stadium(39.0954, -84.5160, "outdoor"),
    "CLE": Stadium(41.5061, -81.6995, "outdoor"),
    "DAL": Stadium(32.7473, -97.0945, "retractable"),
    "DEN": Stadium(39.7439, -105.0201, "outdoor"),
    "DET": Stadium(42.3400, -83.0456, "dome"),
    "GB": Stadium(44.5013, -88.0622, "outdoor"),
    "HOU": Stadium(29.6847, -95.4107, "retractable"),
    "IND": Stadium(39.7601, -86.1639, "retractable"),
    "JAX": Stadium(30.3239, -81.6373, "outdoor"),
    "KC": Stadium(39.0489, -94.4839, "outdoor"),
    "LAC": Stadium(33.9535, -118.3392, "dome"),      # SoFi: fixed roof
    "LAR": Stadium(33.9535, -118.3392, "dome"),
    "LV": Stadium(36.0909, -115.1833, "dome"),
    "MIA": Stadium(25.9580, -80.2389, "outdoor"),
    "MIN": Stadium(44.9735, -93.2575, "dome"),
    "NE": Stadium(42.0909, -71.2643, "outdoor"),
    "NO": Stadium(29.9511, -90.0812, "dome"),
    "NYG": Stadium(40.8135, -74.0745, "outdoor"),
    "NYJ": Stadium(40.8135, -74.0745, "outdoor"),
    "PHI": Stadium(39.9008, -75.1675, "outdoor"),
    "PIT": Stadium(40.4468, -80.0158, "outdoor"),
    "SEA": Stadium(47.5952, -122.3316, "outdoor"),
    "SF": Stadium(37.4032, -121.9698, "outdoor"),
    "TB": Stadium(27.9759, -82.5033, "outdoor"),
    "TEN": Stadium(36.1665, -86.7713, "outdoor"),
    "WSH": Stadium(38.9076, -76.8645, "outdoor"),
}


class WeatherError(RuntimeError):
    pass


@dataclass
class WeatherReading:
    precip_prob: float   # % chance of precipitation at kickoff hour
    wind_mph: float


class WeatherClient:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()

    def _cache_path(self, lat: float, lon: float, day: date) -> Path:
        name = hashlib.sha1(f"{lat:.2f},{lon:.2f}".encode()).hexdigest()[:16]
        return self.cache_dir / f"{day:%Y%m%d}-{name}.json"

    def _forecast(self, lat: float, lon: float, day: date) -> dict:
        cpath = self._cache_path(lat, lon, day)
        if cpath.exists():
            return json.loads(cpath.read_text())
        params = {
            "latitude": lat, "longitude": lon,
            "hourly": "precipitation_probability,wind_speed_10m",
            "wind_speed_unit": "mph", "timezone": "UTC", "forecast_days": 10,
        }
        r = self.session.get(BASE, params=params, timeout=20)
        if not r.ok:
            raise WeatherError(f"{r.status_code}: {r.text[:300]}")
        data = r.json()
        cpath.write_text(json.dumps(data))
        return data

    def at(self, lat: float, lon: float, when: datetime) -> WeatherReading | None:
        """Forecast nearest the kickoff hour (`when`, a naive-or-UTC datetime)."""
        when = when.replace(tzinfo=None)
        try:
            data = self._forecast(lat, lon, when.date())
        except WeatherError:
            return None
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        precip = hourly.get("precipitation_probability") or []
        wind = hourly.get("wind_speed_10m") or []
        if not times:
            return None
        target = when.strftime("%Y-%m-%dT%H:00")
        idx = times.index(target) if target in times else min(
            range(len(times)), key=lambda i: abs(datetime.fromisoformat(times[i]) - when))
        if idx >= len(precip) or idx >= len(wind) or precip[idx] is None or wind[idx] is None:
            return None
        return WeatherReading(precip[idx], wind[idx])


def game_weather(client: WeatherClient, home_abbr: str, kickoff_iso: str) -> WeatherReading | None:
    """None for indoor stadiums, missing data, or an unreachable forecast - callers
    treat that as "nothing to flag", never as an error."""
    stadium = STADIUMS.get(home_abbr)
    if not stadium or stadium.roof != "outdoor" or not kickoff_iso:
        return None
    try:
        when = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return client.at(stadium.lat, stadium.lon, when)
