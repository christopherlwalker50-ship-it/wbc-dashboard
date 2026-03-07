# Braves Dashboard — Project Context

## What This Is
A Streamlit web dashboard — currently a **2026 WBC-only** tracker — located at:
`C:\Users\CWALK\desktop\claude-playtime\baseball-digest\braves-dashboard\`

Runs at `http://localhost:8501`. Launch via `start-braves-dashboard.bat`.

---

## Files
| File | Purpose |
|---|---|
| `app.py` | Main Streamlit app |
| `requirements.txt` | Python dependencies |
| `WBC_logo.svg.png` | WBC logo image displayed at top |
| `venv/` | Virtual environment (Python) |
| `start-braves-dashboard.bat` | Startup script — launches server + opens browser |

**Startup shortcut** installed at:
`C:\Users\CWALK\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\`

---

## Key Constants (app.py)
- `TEAM_ID = 144` — Atlanta Braves MLB team ID (Braves tab hidden but code preserved)
- `WBC_START = date(2026, 3, 4)` / `WBC_END = date(2026, 3, 18)` — extended +1 day past final to catch evening ET games whose UTC date rolls over
- `PACIFIC = ZoneInfo("America/Los_Angeles")` — kept only for hidden Braves panel; WBC display uses visitor's detected timezone

---

## App Layout / UI

- **Page title**: "2026 World Baseball Classic"
- **Background**: Sky blue (`#87CEEB`); column headers blue (`#5b9bd5`)
- **Logo**: `WBC_logo.svg.png` displayed at width=380
- **Refresh button**: Clears `st.cache_data` and reruns
- **Cache clear on new session**: `st.cache_data.clear()` called once when `initialized` not in `st.session_state`

### Section order (top to bottom)
1. Today's Games
2. Pool Play Standings
3. Stat Leaders (HR / RBI / Hits / Walks — 2×2 grid)
4. Results (past completed games, newest first)
5. Upcoming Games (future + today's not-yet-started games)

---

## Visitor Timezone Detection
- Uses `streamlit-js-eval` to read `Intl.DateTimeFormat().resolvedOptions().timeZone` from the browser
- Falls back to UTC on the first render, then reruns automatically with the correct timezone
- `user_tz` (ZoneInfo) and `tz_abbr` (e.g. "ET", "PT") computed after session state init
- `local_today` used for "today's" date, `today_str`, and the caption
- `build_wbc_df` accepts `user_tz` and `tz_abbr` as parameters — all game times displayed in visitor's local timezone

---

## WBC Tab (sportId=51)
- Filters OUT gameType E, S, R — only shows F/D/L/W
- WBC gameType codes: F = pool play, D = quarterfinals, L = semifinals, W = championship
- Games color-coded by round; winner marked with ⭐; "Game Over" treated same as "Final"
- Round column: pool play shows pool name from `game["description"]` (e.g. "Pool C"); knockout rounds use label
- Date column shows visitor's local time: "Mar 5 - 4:30 PM ET"; midnight UTC → date only ("Mar 5")
- Windows-compatible time format (avoids `%-d`/`%-I`)

### `get_wbc_schedule()` — cached 120s
- `GET https://statsapi.mlb.com/api/v1/schedule` with `sportId=51`, date range WBC_START–WBC_END, `hydrate=linescore`
- Linescore hydration provides live inning data for in-progress games

### `build_wbc_df(schedule_data, user_tz, tz_abbr)`
- Builds full schedule DataFrame sorted by `_round_order` then `_sort_dt`
- Stores `_raw_status` (original API `detailedState`) alongside display `Status`
- For in-progress games: Status shown as "Top 4", "Bot 3", "Mid 6", "End 9" using `linescore.inningState` + `linescore.currentInning`
- Splits into: today_games / results (past final) / game_log (future + today not-yet-started)
- **game_log filter uses `_raw_status`** — excludes "Final", "Game Over", AND "In Progress" (games disappear from Upcoming the moment they start)

### `build_wbc_standings(schedule_data)`
- Two-pass: first initializes all teams from scheduled pool play games; second accumulates W/L/RD from completed games
- Sorted by W desc, RD desc; displayed with `RD` formatted as `%+d`
- Shown as side-by-side columns, one per pool (Pool A/B/C/D)

### `WBC_ROUND_ORDER` (for sort)
```python
{"Pool A": 0, "Pool B": 0, "Pool C": 0, "Pool D": 0,
 "Pool Play": 0, "Quarterfinal": 1, "Semifinal": 2, "Championship": 3}
```

### `style_wbc_row` (row-level styling)
- Pool Play: no style
- Quarterfinal: dark green bg, light green text
- Semifinal: dark orange bg, amber text, bold
- Championship: dark gold bg, gold text, bold, slightly larger font

---

## Stat Leaders (get_wbc_stat_leaders, cached 300s)
- Single pass through all completed game boxscores (`/api/v1/game/{pk}/boxscore`)
- Batting: HR, RBI, Hits, BB — from `seasonStats.batting.*`; max value per player across games
- Field names: `homeRuns`, `rbi`, `hits`, `baseOnBalls`
- Displayed as 2×2 grid: HR/RBI top row, Hits/Walks bottom row; capped at 10 players each
- Section headers: "Home Run Leaders", "RBI Leaders", "Hits Leaders", "Walk Leaders"
- **Rate stats deferred** — max-across-games is wrong for ERA/K9/BB9; needs cumulative outs approach

---

## Atlanta Braves Panel (HIDDEN — code preserved under `if False:`)
- Roster: `GET https://statsapi.mlb.com/api/v1/teams/144/roster?rosterType=active`, sorted by position, cached 3600s
- Results: two-step fetch — v1 schedule (gamePk list) → v1.1 game feed per pk; last 10 completed games; spring training included until any regular season game exists; cached 1800s

---

## WBC Name Shortening (`WBC_NAME_SHORT`)
```python
"Kingdom of the Netherlands" → "Netherlands"
"Dominican Republic" → "Dom. Republic"
"United States" → "USA"
```

---

## Google Analytics 4
- Measurement ID: `G-DBXSTQT7Q7`
- Injected into `venv/Lib/site-packages/streamlit/static/index.html` inside `<head>` (standard gtag.js snippet)
- **Note:** This file is inside the venv — re-add the snippet if Streamlit is upgraded via pip

---

## Known Issues / History
- `statsapi.get("roster", ...)` — errored, replaced with direct `requests` call
- `v1.1/schedule` — 404'd, replaced with v1 schedule + v1.1 game feed two-step approach
- Spring training 2026: `gameType=R` returned no games; added `gameType=R,S`
- WBC stat leaders: `stats/leaders` and `/api/v1/stats` returned wrong/empty data; boxscore `seasonStats.batting.*` works reliably (confirmed 2026-03-04)
- API totals may differ slightly from MLB website (e.g. Ha-Seong Kim 2023 WBC: API says 2 HR, website says 3). Code reflects the API faithfully.

---

## Dependencies
```
streamlit==1.55.0
pandas==2.3.3
requests==2.32.5
MLB-StatsAPI==1.9.0
streamlit-js-eval==0.1.7
```
