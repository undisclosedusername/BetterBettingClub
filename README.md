# streaks — 100% player-prop streak cards for NBA & NFL

Finds players who hit a stat threshold in every one of their most recent games
(e.g. "D. Mitchell : 10+ Points — 15/15 Games"), plus one-miss near streaks
("J. Harden : 2+ 3PTM — 10/11"). It limits results to teams on the slate
(tonight for NBA, the coming week for NFL), moves preferable matchups to the
top, and renders the results as PNG cards and a static website.

```bash
pip install -r requirements.txt && playwright install chromium
python -m streaks nba                         # today's slate, every NBA stat
python -m streaks nfl --date 2026-11-15       # Sunday NFL slate
python -m streaks nba --stats pts,reb,ast,3pm --json out.json
python -m streaks nba --since 2026-02-01      # let streaks reach back into last season
python -m streaks nfl --list-stats
```

Output goes to `output/<league>-<date>/` (`*.html` and `*.png`). Box scores are
cached in `.cache/`, so later runs only download new games.

## How a streak is picked
For each player × stat, every threshold on the stat's ladder (`streaks/config.py`)
is a candidate if it is ≥ the stat's `min_line` **and** ≥ `--floor-ratio` (default
0.5) × the player's average over their last 15 games. This rules out trivial lines.
Of those candidates, the one with the **longest perfect streak** wins, and ties go
to the higher line. A streak must be at least `--min-games` (default 4) long.
Players without a perfect streak can still show up with a near streak of at least
`--min-near-games` (default 8) games and at most `--max-misses` (default 1) misses.
Cards list perfect streaks first, longest at the top, then near streaks.

Games the player sat out (DNP / 0 min) are skipped and don't break a streak.

## Preferable matchups (★)
Each streak is checked against tonight's matchup (`streaks/matchup.py`). It counts
as **preferable** if any of these is true:

| flag | rule | default |
|---|---|---|
| Soft D | opponent is in the softest share of the league at allowing *this stat*, based on its last 15 games | top 25% (`--soft-d-pct`) |
| Weak opp | opponent is under .500 and its win% is well below the player's team's | gap ≥ .200, ≥ 4 GP (`--weak-opp-gap`) |
| Hot | player's last-5 average is well above their earlier average | +20% (`--hot-ratio`) |

Preferable rows come first (more flags rank higher), then the usual streak order.
On cards they get a gold border, a ★, and chips explaining why. Use
`--no-preferable-first` to keep the pure streak order but still highlight rows.
"Soft D" uses team totals, so a team that allows lots of rebounds is flagged for
every player, whatever their position.

## Website (GitHub Pages)
```
public/index.html            static page (no framework, no build step)
public/data/<league>.json    written by: python -m streaks.build --out public/data
.github/workflows/site.yml   test -> build data -> publish to Pages
```
Setup, done once:
1. Push to GitHub (to the `main` branch). The repo should be public, because
   Pages and the every-30-minutes schedule are only free on public repos.
2. In the repo, go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.
3. Under **Actions → site → Run workflow**, run it once to publish right away. The
   site appears at `https://<user>.github.io/<repo>/`.

After that, the workflow runs every 30 minutes on game days (and on every push to `main`).
It rebuilds the data and publishes the site. Data is never committed to the repo.
Instead, the box-score cache and the last published JSON carry over between runs
through `actions/cache`. GitHub can start scheduled runs 5–15 minutes late when it's busy.

The JSON (`streaks/pipeline.py`, `schema: 1`) contains everything the page needs:
teams, logos, colors, records, the games, and for each stat the ranked rows with
flags and the last 10 values.

## Stats
NBA: pts, reb, ast, 3pm, pra, pr, pa, ra, stl, blk
NFL: pass_yds, pass_td, pass_cmp, rush_yds, rush_att, rec_yds, rec, rush_rec_yds,
anytime_td, int, sacks, tackles

Add or change stats and ladders in `streaks/config.py`.

## Data
ESPN's public site API (`site.api.espn.com`): `scoreboard` for the schedule and
`summary` for box scores. It is unofficial and undocumented, so the field names
can change. All parsing lives in `streaks/espn.py`.

Set `STREAKS_CHROMIUM=/path/to/chrome` to render PNGs with a Chromium other
than Playwright's own.
