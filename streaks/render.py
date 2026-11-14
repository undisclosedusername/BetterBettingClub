"""Render streak cards as standalone HTML and (optionally) PNG via Playwright."""
from __future__ import annotations

import html
import os
from datetime import date
from pathlib import Path


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d10;font-family:Inter,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#fff}
.card{position:relative;width:1030px;padding:56px 48px 48px 72px;overflow:hidden;
  background:linear-gradient(180deg,var(--accent) 0%,#151318 62%,#111114 100%)}
.hero{position:absolute;right:-40px;top:0;height:560px;opacity:.55;
  -webkit-mask-image:linear-gradient(to left,#000 30%,transparent 95%),linear-gradient(to bottom,#000 55%,transparent);
  -webkit-mask-composite:source-in;mask-composite:intersect}
.league{position:relative;display:inline-block;font-weight:800;font-size:30px;letter-spacing:2px;
  padding:10px 16px;border:3px solid #fff;border-radius:10px}
h1{position:relative;font-size:56px;font-weight:700;margin-top:36px;letter-spacing:.5px}
.date{position:relative;font-size:32px;color:#e7e3ea;margin:6px 0 34px}
.rows{position:relative;display:flex;flex-direction:column;gap:19px}
.row{display:flex;align-items:stretch;min-height:71px;border-radius:10px;overflow:hidden;
  border:1.5px solid rgba(255,255,255,.28);
  background:linear-gradient(90deg,rgba(var(--rgb),.55),rgba(30,28,34,.75) 70%)}
.row.pref{border:2.5px solid #f5c542;box-shadow:0 0 0 1px rgba(245,197,66,.25),0 0 22px rgba(245,197,66,.35)}
.logo{width:134px;flex:none;display:flex;align-items:center;justify-content:center}
.logo img{width:96px;height:96px;object-fit:contain}
.logo span{font-weight:800;font-size:26px}
.body{flex:1;display:flex;flex-direction:column;justify-content:center;padding:10px 0 10px 18px;gap:6px}
.name{font-size:27px;font-weight:600}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:17px;color:#cfc9d3}
.chip{background:rgba(245,197,66,.16);color:#f8d675;border:1px solid rgba(245,197,66,.55);
  border-radius:999px;padding:2px 10px;font-weight:600}
.star{color:#f5c542;margin-right:6px}
.games{display:flex;align-items:center;padding-right:18px;font-size:27px;font-variant-numeric:tabular-nums}
.near .games{color:#d9d4dc}
.legend{position:relative;margin-top:22px;font-size:18px;color:#cfc9d3}
.legend b{color:#f5c542}
"""


def _rgb(hex_color: str) -> str:
    h = (hex_color or "333333").lstrip("#")
    if len(h) != 6:
        h = "333333"
    return ",".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def date_label(result: dict) -> str:
    dates = [date.fromisoformat(d) for d in result.get("slate_dates") or [result["date"]]]
    d0, d1 = dates[0], dates[-1]
    if d0 == d1:
        return f"{d0:%A %B} {d0.day}"
    return f"{d0:%b} {d0.day} – {d1:%b} {d1.day}"


def card_html(result: dict, card: dict) -> str:
    teams = result["teams"]
    rows_data = card["rows"]
    esc = html.escape

    def team(tid):
        return teams.get(tid or "") or {"abbr": "", "logo": "", "color": "333333"}

    accent = team(rows_data[0]["team"])["color"] if rows_data else "5a1a2a"
    hero = rows_data[0]["player"]["headshot"] if rows_data else ""
    rows = []
    for r in rows_data:
        t = team(r["team"])
        logo = (f'<img src="{esc(t["logo"])}" alt="{esc(t["abbr"])}">' if t["logo"]
                else f'<span>{esc(t["abbr"])}</span>')
        meta = []
        if r.get("opponent"):
            meta.append(f'{"vs" if r.get("home") else "@"} {esc(team(r["opponent"])["abbr"])}')
        meta += [f'<span class="chip">{esc(f["text"])}</span>' for f in r["flags"]]
        cls = "row" + ("" if r["perfect"] else " near") + (" pref" if r["preferable"] else "")
        star = '<span class="star">★</span>' if r["preferable"] else ""
        rows.append(
            f'<div class="{cls}" style="--rgb:{_rgb(t["color"])}">'
            f'<div class="logo" style="background:#{esc(t["color"] or "333333")}">{logo}</div>'
            f'<div class="body"><div class="name">{star}{esc(r["player"]["short"])} : '
            f'{esc(r["threshold_label"])} {esc(card["label"])}</div>'
            + (f'<div class="meta">{"".join(meta)}</div>' if meta else "") +
            f'</div><div class="games">{r["hits"]}/{r["games"]} Games</div></div>')
    if not rows:
        rows.append('<div class="row"><div class="body"><div class="name">No qualifying streaks</div></div></div>')
    legend = ('<div class="legend"><b>★</b> preferable matchup — soft opponent defense, '
              'weaker opponent, or player in form</div>') if any(r["preferable"] for r in rows_data) else ""
    hero_img = f'<img class="hero" src="{esc(hero)}" alt="">' if hero else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{esc(result["league_name"])} {esc(card["title"])} Streaks</title>
<style>{CSS}</style></head><body>
<div class="card" style="--accent:#{esc(accent)}">{hero_img}
<div class="league">{esc(result["league_name"])}</div>
<h1>100% {esc(card["title"])} Streaks</h1>
<div class="date">{date_label(result)}</div>
<div class="rows">{''.join(rows)}</div>{legend}</div></body></html>"""


def write_cards(cards: list[tuple[str, str]], out_dir: Path, png: bool = True) -> list[Path]:
    """cards = [(slug, html)]. Writes slug.html and, if possible, slug.png."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for slug, doc in cards:
        p = out_dir / f"{slug}.html"
        p.write_text(doc, encoding="utf-8")
        written.append(p)
    if png:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("  (playwright not installed - skipping PNGs; pip install playwright && playwright install chromium)")
            return written
        with sync_playwright() as pw:
            exe = os.environ.get("STREAKS_CHROMIUM")
            browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1030, "height": 1300}, device_scale_factor=1)
            for slug, _ in cards:
                page.goto((out_dir / f"{slug}.html").resolve().as_uri(), wait_until="load", timeout=30000)
                png_path = out_dir / f"{slug}.png"
                page.locator(".card").screenshot(path=str(png_path))
                written.append(png_path)
            browser.close()
    return written
