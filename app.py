import streamlit as st
import statsapi
import pandas as pd
import requests
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo
from streamlit_js_eval import streamlit_js_eval

TEAM_ID = 144
TEAM_NAME = "Atlanta Braves"

POSITION_ORDER = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH", "SP", "RP"]

WBC_ROUND_LABELS = {
    "F": "Pool Play",
    "D": "Quarterfinal",
    "L": "Semifinal",
    "W": "Championship",
}

WBC_ROUND_ORDER = {
    "Pool A": 0,
    "Pool B": 0,
    "Pool C": 0,
    "Pool D": 0,
    "Pool Play": 0,
    "Quarterfinal": 1,
    "Semifinal": 2,
    "Championship": 3,
}

WBC_NAME_SHORT = {
    "Kingdom of the Netherlands": "Netherlands",
    "Dominican Republic": "Dom. Republic",
    "United States": "USA",
}

def shorten(name):
    return WBC_NAME_SHORT.get(name, name)


def _is_final_state(raw_status):
    """Returns True for any game-complete state: Final, Game Over, or Completed Early (any variant)."""
    return raw_status in ("Final", "Game Over") or "completed early" in raw_status.lower()


PACIFIC = ZoneInfo("America/Los_Angeles")

def _pt_today():
    return datetime.now(PACIFIC).date()

def _to_pt_date(utc_str):
    dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    return dt.astimezone(PACIFIC).date()

# 2026 WBC: March 4–17, extended to March 18 to catch evening ET games whose UTC date rolls over
WBC_START = date(2026, 3, 4)
WBC_END = date(2026, 3, 18)

@st.cache_data(ttl=3600)
def get_roster():
    resp = requests.get(
        f"https://statsapi.mlb.com/api/v1/teams/{TEAM_ID}/roster",
        params={"rosterType": "active"},
        timeout=10,
    )
    resp.raise_for_status()
    response = resp.json()
    rows = []
    for entry in response.get("roster", []):
        rows.append(
            {
                "#": entry.get("jerseyNumber", ""),
                "Position": entry["position"]["abbreviation"],
                "Player": entry["person"]["fullName"],
            }
        )
    df = pd.DataFrame(rows, columns=["#", "Position", "Player"])

    def pos_sort_key(pos):
        try:
            return POSITION_ORDER.index(pos)
        except ValueError:
            return len(POSITION_ORDER)

    df["_sort"] = df["Position"].apply(pos_sort_key)
    df = df.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)
    return df


@st.cache_data(ttl=1800)
def get_last_7_results():
    end_date = _pt_today()
    start_date = end_date - timedelta(days=30)

    # Step 1: v1 schedule to get gamePks for completed games
    schedule_resp = requests.get(
        "https://statsapi.mlb.com/api/v1/schedule",
        params={
            "sportId": 1,
            "teamId": TEAM_ID,
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate": end_date.strftime("%Y-%m-%d"),
            "gameType": "R,S",
        },
        timeout=10,
    )
    schedule_resp.raise_for_status()
    schedule_data = schedule_resp.json()

    game_pks = []
    for date_entry in schedule_data.get("dates", []):
        for game in date_entry.get("games", []):
            if game.get("status", {}).get("detailedState") == "Final":
                game_pks.append((game["gameDate"], game["gamePk"], game.get("gameType", "R")))

    game_pks.sort(key=lambda x: x[0])

    # Once any regular season games exist, drop spring training
    if any(gt == "R" for _, _, gt in game_pks):
        game_pks = [(d, pk, gt) for d, pk, gt in game_pks if gt == "R"]

    game_pks = game_pks[-10:]

    # Step 2: v1.1 game feed for each gamePk
    games = []
    for _, game_pk, game_type in game_pks:
        resp = requests.get(
            f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
            timeout=10,
        )
        resp.raise_for_status()
        feed = resp.json()
        feed["_gameType"] = game_type
        games.append(feed)

    return games


@st.cache_data(ttl=120)
def get_wbc_schedule():
    resp = requests.get(
        "https://statsapi.mlb.com/api/v1/schedule",
        params={
            "sportId": 51,
            "startDate": WBC_START.strftime("%Y-%m-%d"),
            "endDate": WBC_END.strftime("%Y-%m-%d"),
            "hydrate": "linescore",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=300)
def get_wbc_stat_leaders():
    schedule_data = get_wbc_schedule()
    completed_pks = []
    for date_entry in schedule_data.get("dates", []):
        for game in date_entry.get("games", []):
            if game.get("gameType") in ("E", "S", "R"):
                continue
            if _is_final_state(game.get("status", {}).get("detailedState", "")):
                completed_pks.append(game["gamePk"])

    player_map = {}  # player_id -> {name, team, hr, rbi}
    for pk in completed_pks:
        resp = requests.get(
            f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore",
            timeout=10,
        )
        resp.raise_for_status()
        bx = resp.json()
        for side in ("away", "home"):
            team_name = bx.get("teams", {}).get(side, {}).get("team", {}).get("name", "")
            for pdata in bx.get("teams", {}).get(side, {}).get("players", {}).values():
                pid = pdata.get("person", {}).get("id", 0)
                name = pdata.get("person", {}).get("fullName", "")
                batting = pdata.get("seasonStats", {}).get("batting", {})
                try:
                    hrs = int(batting.get("homeRuns", 0))
                except (ValueError, TypeError):
                    hrs = 0
                try:
                    rbi = int(batting.get("rbi", 0))
                except (ValueError, TypeError):
                    rbi = 0
                try:
                    hits = int(batting.get("hits", 0))
                except (ValueError, TypeError):
                    hits = 0
                try:
                    bb = int(batting.get("baseOnBalls", 0))
                except (ValueError, TypeError):
                    bb = 0
                existing = player_map.get(pid, {"name": name, "team": shorten(team_name), "hr": 0, "rbi": 0, "hits": 0, "bb": 0})
                player_map[pid] = {
                    "name": name,
                    "team": shorten(team_name),
                    "hr": max(existing["hr"], hrs),
                    "rbi": max(existing["rbi"], rbi),
                    "hits": max(existing["hits"], hits),
                    "bb": max(existing["bb"], bb),
                }

    hr_leaders = sorted([v for v in player_map.values() if v["hr"] > 0], key=lambda x: -x["hr"])[:10]
    rbi_leaders = sorted([v for v in player_map.values() if v["rbi"] > 0], key=lambda x: -x["rbi"])[:10]
    hits_leaders = sorted([v for v in player_map.values() if v["hits"] > 0], key=lambda x: -x["hits"])[:10]
    bb_leaders = sorted([v for v in player_map.values() if v["bb"] > 0], key=lambda x: -x["bb"])[:10]
    return hr_leaders, rbi_leaders, hits_leaders, bb_leaders


def build_wbc_df(schedule_data, user_tz, tz_abbr):
    rows = []
    for date_entry in schedule_data.get("dates", []):
        for game in date_entry.get("games", []):
            if game.get("gameType") in ("E", "S", "R"):
                continue
            away = shorten(game["teams"]["away"]["team"]["name"])
            home = shorten(game["teams"]["home"]["team"]["name"])
            away_score = game["teams"]["away"].get("score")
            home_score = game["teams"]["home"].get("score")
            raw_status = game["status"]["detailedState"]
            status = raw_status
            if raw_status == "In Progress":
                ls = game.get("linescore", {})
                inning = ls.get("currentInning")
                inning_state = ls.get("inningState", "")
                if inning and inning_state in ("Top", "Middle", "Bottom", "End"):
                    state_label = {"Top": "Top", "Middle": "Mid", "Bottom": "Bot", "End": "End"}.get(inning_state, inning_state)
                    status = f"{state_label} {inning}"
            elif _is_final_state(raw_status):
                ls = game.get("linescore", {})
                innings = ls.get("currentInning")
                if innings and innings != 9:
                    status = f"F/{innings}"
                else:
                    status = "Final"
            game_time_utc = game["gameDate"]
            venue = game.get("venue", {}).get("name", "")

            # Parse once, convert to visitor's local timezone (avoid %-d/%-I, Windows-incompatible)
            try:
                dt_utc = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                dt_local = dt_utc.astimezone(user_tz)
                game_date = dt_local.date().strftime("%Y-%m-%d")
                month_day = f"{dt_local.strftime('%b')} {dt_local.day}"
                if dt_local.hour == 0 and dt_local.minute == 0:
                    date_str = month_day
                else:
                    hour = dt_local.hour % 12 or 12
                    am_pm = "AM" if dt_local.hour < 12 else "PM"
                    date_str = f"{month_day} - {hour}:{dt_local.strftime('%M')} {am_pm} {tz_abbr}"
            except Exception:
                game_date = game_time_utc[:10]
                date_str = game_date

            away_display = away
            home_display = home
            if away_score is not None and home_score is not None:
                if _is_final_state(raw_status):
                    if away_score > home_score:
                        away_display = f"⭐ {away} - {away_score}"
                        home_display = f"{home} - {home_score}"
                    elif home_score > away_score:
                        away_display = f"{away} - {away_score}"
                        home_display = f"⭐ {home} - {home_score}"
                    else:
                        away_display = f"{away} - {away_score}"
                        home_display = f"{home} - {home_score}"
                else:
                    away_display = f"{away} - {away_score}"
                    home_display = f"{home} - {home_score}"

            game_type = game.get("gameType", "")
            if game_type == "F":
                round_label = game.get("description") or "Pool Play"
            else:
                round_label = WBC_ROUND_LABELS.get(game_type, game_type)
            rows.append({
                "_sort_dt": game_time_utc,
                "_date": game_date,
                "_raw_status": raw_status,
                "Round": round_label,
                "Date": date_str,
                "Away": away_display,
                "Home": home_display,
                "Status": status,
            })

    df = pd.DataFrame(rows, columns=["_sort_dt", "_date", "_raw_status", "Round", "Date", "Away", "Home", "Status"])
    df["_round_order"] = df["Round"].map(WBC_ROUND_ORDER).fillna(0)
    df = df.sort_values(["_round_order", "_sort_dt"]).reset_index(drop=True)
    return df


def build_wbc_standings(schedule_data):
    pools = {}

    # First pass: initialize all teams from every scheduled pool play game
    for date_entry in schedule_data.get("dates", []):
        for game in date_entry.get("games", []):
            if game.get("gameType") != "F":
                continue
            away = shorten(game["teams"]["away"]["team"]["name"])
            home = shorten(game["teams"]["home"]["team"]["name"])
            pool = game.get("description") or "Pool Play"
            if pool not in pools:
                pools[pool] = {}
            for team in (away, home):
                if team not in pools[pool]:
                    pools[pool][team] = {"W": 0, "L": 0, "RD": 0}

    # Second pass: accumulate W/L/RD from completed games
    for date_entry in schedule_data.get("dates", []):
        for game in date_entry.get("games", []):
            if game.get("gameType") != "F":
                continue
            if not _is_final_state(game["status"]["detailedState"]):
                continue
            away = shorten(game["teams"]["away"]["team"]["name"])
            home = shorten(game["teams"]["home"]["team"]["name"])
            away_score = game["teams"]["away"].get("score")
            home_score = game["teams"]["home"].get("score")
            if away_score is None or home_score is None:
                continue
            pool = game.get("description") or "Pool Play"
            diff = away_score - home_score
            if diff > 0:
                pools[pool][away]["W"] += 1
                pools[pool][home]["L"] += 1
            else:
                pools[pool][home]["W"] += 1
                pools[pool][away]["L"] += 1
            pools[pool][away]["RD"] += diff
            pools[pool][home]["RD"] -= diff

    pool_dfs = {}
    for pool_name in sorted(pools.keys()):
        rows = [{"Team": t, "W": s["W"], "L": s["L"], "RD": s["RD"]} for t, s in pools[pool_name].items()]
        df = pd.DataFrame(rows).sort_values(["W", "RD"], ascending=[False, False]).reset_index(drop=True)
        df.index = range(1, len(df) + 1)
        pool_dfs[pool_name] = df

    return pool_dfs


def build_results_df(games):
    rows = []
    for game in games:
        game_data = game["gameData"]
        live_data = game["liveData"]

        home_id = game_data["teams"]["home"]["id"]
        home_name = game_data["teams"]["home"]["name"]
        away_name = game_data["teams"]["away"]["name"]
        home_score = live_data["linescore"]["teams"]["home"].get("runs", 0)
        away_score = live_data["linescore"]["teams"]["away"].get("runs", 0)
        game_date = _to_pt_date(game_data["datetime"]["dateTime"]).strftime("%Y-%m-%d")

        if home_id == TEAM_ID:
            opponent = "vs " + away_name
            braves_score = home_score
            opp_score = away_score
        else:
            opponent = "@ " + home_name
            braves_score = away_score
            opp_score = home_score

        result = "W" if braves_score > opp_score else "L"
        score = f"{braves_score}-{opp_score}"
        game_type = game.get("_gameType", "R")
        label = "Spring Training" if game_type == "S" else "Regular Season"
        rows.append({"Date": game_date, "Type": label, "Opponent": opponent, "Score": score, "Result": result})

    return pd.DataFrame(rows, columns=["Date", "Type", "Opponent", "Score", "Result"])


def style_wbc_row(row):
    styles = {
        "Pool Play":     [""] * len(row),
        "Quarterfinal":  ["background-color: #1a3a2a; color: #90d890;"] * len(row),
        "Semifinal":     ["background-color: #3a2710; color: #f0b870; font-weight: bold;"] * len(row),
        "Championship":  ["background-color: #4a3800; color: #ffd700; font-weight: bold; font-size: 1.05em;"] * len(row),
    }
    return styles.get(row["Round"], [""] * len(row))


def style_result(val):
    if val == "W":
        return "background-color: #1a7a1a; color: white; font-weight: bold;"
    elif val == "L":
        return "background-color: #8b1a1a; color: white; font-weight: bold;"
    return ""




def render_bracket_svg(wbc_df):
    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def trunc(s, n=27):
        s = str(s)
        return s if len(s) <= n else s[: n - 1] + "\u2026"

    qf = wbc_df[wbc_df["Round"] == "Quarterfinal"].sort_values("_sort_dt").reset_index(drop=True)
    sf = wbc_df[wbc_df["Round"] == "Semifinal"].sort_values("_sort_dt").reset_index(drop=True)
    ch = wbc_df[wbc_df["Round"] == "Championship"].reset_index(drop=True)

    CW, CH = 230, 82        # card width, height
    HALF = CH // 2          # divider y-offset within card
    QF_X, SF_X, FI_X = 10, 315, 620
    MID1 = (QF_X + CW + SF_X) // 2     # connector midpoint QF→SF
    MID2 = (SF_X + CW + FI_X) // 2     # connector midpoint SF→Final

    TOP = 22                # space above first card for round labels
    IN_GAP = 40             # gap between the two cards within a bracket pair
    OUT_GAP = 78            # gap between the two bracket pairs

    qy = [TOP, TOP + CH + IN_GAP, TOP + 2*CH + IN_GAP + OUT_GAP, TOP + 3*CH + 2*IN_GAP + OUT_GAP]
    qc = [y + HALF for y in qy]                          # QF card centers
    sc = [(qc[0] + qc[1]) // 2, (qc[2] + qc[3]) // 2]  # SF card centers
    sy = [c - HALF for c in sc]                          # SF card tops

    fc = (sc[0] + sc[1]) // 2
    fy = fc - HALF

    W = FI_X + CW + 10
    H = max(qy[3] + CH, sy[1] + CH, fy + CH) + 20

    LC = "#888"
    STYLE = {
        "Quarterfinal": {"bg": "#1a3a2a", "tx": "#90d890", "bd": "#3a6a4a", "dv": "#2a5a3a", "sm": "#507050"},
        "Semifinal":    {"bg": "#3a2710", "tx": "#f0b870", "bd": "#7a5020", "dv": "#6a4010", "sm": "#806040"},
        "Championship": {"bg": "#4a3800", "tx": "#ffd700", "bd": "#8a6800", "dv": "#6a5000", "sm": "#907800"},
    }

    def card_svg(x, y, row, rnd):
        s = STYLE[rnd]
        away = esc(trunc(row["Away"]))
        home = esc(trunc(row["Home"]))
        info = esc(trunc(row["Date"] if row["_raw_status"] == "Scheduled" else row["Status"], 32))
        return (
            f'<rect x="{x}" y="{y}" width="{CW}" height="{CH}" rx="5" ry="5"'
            f' fill="{s["bg"]}" stroke="{s["bd"]}" stroke-width="1.5"/>'
            f'<line x1="{x+1}" y1="{y+HALF}" x2="{x+CW-1}" y2="{y+HALF}"'
            f' stroke="{s["dv"]}" stroke-width="1"/>'
            f'<text x="{x+10}" y="{y+26}" font-size="13" fill="{s["tx"]}"'
            f' font-family="sans-serif">{away}</text>'
            f'<text x="{x+10}" y="{y+59}" font-size="13" fill="{s["tx"]}"'
            f' font-family="sans-serif">{home}</text>'
            f'<text x="{x + CW//2}" y="{y+76}" font-size="9" fill="{s["sm"]}"'
            f' font-family="sans-serif" text-anchor="middle" font-weight="bold">{info}</text>'
        )

    def conn_svg(rx, cy1, cy2, lx, mx):
        my = (cy1 + cy2) // 2
        return (
            f'<line x1="{rx}" y1="{cy1}" x2="{mx}" y2="{cy1}" stroke="{LC}" stroke-width="1.5"/>'
            f'<line x1="{rx}" y1="{cy2}" x2="{mx}" y2="{cy2}" stroke="{LC}" stroke-width="1.5"/>'
            f'<line x1="{mx}" y1="{cy1}" x2="{mx}" y2="{cy2}" stroke="{LC}" stroke-width="1.5"/>'
            f'<line x1="{mx}" y1="{my}" x2="{lx}" y2="{my}" stroke="{LC}" stroke-width="1.5"/>'
        )

    SCALE = 1.5
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(W * SCALE)}" height="{int(H * SCALE)}"'
        f' style="background:transparent;display:block;overflow:visible">',
        f'<g transform="scale({SCALE})">',
    ]

    for lx, lbl in [(QF_X + CW//2, "QUARTERFINALS"), (SF_X + CW//2, "SEMIFINALS"), (FI_X + CW//2, "CHAMPIONSHIP")]:
        out.append(
            f'<text x="{lx}" y="14" text-anchor="middle" font-size="10" fill="#000"'
            f' font-family="sans-serif" font-weight="bold" letter-spacing="1">{lbl}</text>'
        )

    QFR = QF_X + CW
    SFR = SF_X + CW
    if len(qf) >= 2:
        out.append(conn_svg(QFR, qc[0], qc[1], SF_X, MID1))
    if len(qf) >= 4:
        out.append(conn_svg(QFR, qc[2], qc[3], SF_X, MID1))
    if len(sf) >= 2:
        out.append(conn_svg(SFR, sc[0], sc[1], FI_X, MID2))

    for i in range(min(len(qf), 4)):
        out.append(card_svg(QF_X, qy[i], qf.iloc[i], "Quarterfinal"))
    for i in range(min(len(sf), 2)):
        out.append(card_svg(SF_X, sy[i], sf.iloc[i], "Semifinal"))
    if len(ch) > 0:
        out.append(card_svg(FI_X, fy, ch.iloc[0], "Championship"))

    out.append('</g>')
    out.append('</svg>')
    return '\n'.join(out)


st.set_page_config(page_title="2026 World Baseball Classic", layout="wide")

if "initialized" not in st.session_state:
    st.cache_data.clear()
    st.session_state.initialized = True

# Detect visitor's browser timezone; falls back to UTC on first render, then reruns with correct tz
_browser_tz = streamlit_js_eval(
    js_expressions='Intl.DateTimeFormat().resolvedOptions().timeZone',
    key='browser_tz'
)
try:
    user_tz = ZoneInfo(_browser_tz) if _browser_tz else ZoneInfo("UTC")
except Exception:
    user_tz = ZoneInfo("UTC")
tz_abbr = datetime.now(user_tz).strftime('%Z')
local_today = datetime.now(user_tz).date()

st.markdown("""
<style>
[data-testid="stAppViewContainer"] {
    background-color: #87CEEB !important;
}
[data-testid="stDataFrame"] [role="columnheader"],
[data-testid="stDataFrame"] th {
    background-color: #5b9bd5 !important;
    color: white !important;
    font-weight: bold !important;
}
</style>
""", unsafe_allow_html=True)

st.image("WBC_logo.svg.png", width=380)

if st.button("Refresh Data"):
    st.cache_data.clear()
    st.rerun()

if False:  # Atlanta Braves tab hidden — code preserved
    with tab_braves:
        col_roster, col_results = st.columns(2)

        with col_roster:
            st.subheader("Active Roster")
            try:
                roster_df = get_roster()
                st.dataframe(roster_df, hide_index=True, height=620)
            except Exception as e:
                st.error(f"Could not load roster: {e}")

        with col_results:
            st.subheader("Last 10 Games")
            try:
                games = get_last_7_results()
                if not games:
                    st.info("No completed regular season games found in the last 30 days.")
                else:
                    results_df = build_results_df(games)
                    wins = (results_df["Result"] == "W").sum()
                    losses = (results_df["Result"] == "L").sum()

                    styled = results_df.style.applymap(style_result, subset=["Result"])
                    st.dataframe(styled, hide_index=True)

                    m1, m2, m3 = st.columns(3)
                    m1.metric("Wins", wins)
                    m2.metric("Losses", losses)
                    m3.metric("Win %", f"{wins / (wins + losses):.1%}" if (wins + losses) > 0 else "N/A")
            except Exception as e:
                st.error(f"Could not load game results: {e}")

try:
    wbc_data = get_wbc_schedule()
    wbc_df = build_wbc_df(wbc_data, user_tz, tz_abbr)

    if wbc_df.empty:
        st.info("No WBC games found yet for 2026.")

    today_str = local_today.strftime("%Y-%m-%d")
    today_games = wbc_df[wbc_df["_date"] == today_str]
    results = wbc_df[(wbc_df["_date"] < today_str) & (wbc_df["_raw_status"].apply(_is_final_state))].sort_values(["_round_order", "_sort_dt"], ascending=False)
    game_log = wbc_df[
        (wbc_df["_date"] > today_str) |
        ((wbc_df["_date"] == today_str) & (~wbc_df["_raw_status"].apply(_is_final_state)) & (wbc_df["_raw_status"] != "In Progress"))
    ]

    tournament_rounds = {"Quarterfinal", "Semifinal", "Championship"}
    in_tournament_mode = not wbc_df[wbc_df["Round"].isin(tournament_rounds)].empty

    pool_standings = build_wbc_standings(wbc_data)

    def render_standings(standings):
        cols = st.columns(len(standings))
        for i, (pool_name, sdf) in enumerate(standings.items()):
            with cols[i]:
                st.caption(pool_name)
                st.dataframe(
                    sdf,
                    column_config={"RD": st.column_config.NumberColumn("RD", format="%+d")},
                    use_container_width=True,
                )

    def render_stat_leaders():
        try:
            hr_leaders, rbi_leaders, hits_leaders, bb_leaders = get_wbc_stat_leaders()
            col_hr, col_rbi = st.columns(2)
            with col_hr:
                st.subheader("Home Run Leaders")
                if hr_leaders:
                    hr_df = pd.DataFrame([{"Player": p["name"], "Country": p["team"], "HR": p["hr"]} for p in hr_leaders])
                    hr_df.index = range(1, len(hr_df) + 1)
                    st.dataframe(hr_df, use_container_width=True)
                else:
                    st.info("No home runs hit yet.")
            with col_rbi:
                st.subheader("RBI Leaders")
                if rbi_leaders:
                    rbi_df = pd.DataFrame([{"Player": p["name"], "Country": p["team"], "RBI": p["rbi"]} for p in rbi_leaders])
                    rbi_df.index = range(1, len(rbi_df) + 1)
                    st.dataframe(rbi_df, use_container_width=True)
                else:
                    st.info("No RBIs recorded yet.")
            col_hits, col_bb = st.columns(2)
            with col_hits:
                st.subheader("Hits Leaders")
                if hits_leaders:
                    hits_df = pd.DataFrame([{"Player": p["name"], "Country": p["team"], "H": p["hits"]} for p in hits_leaders])
                    hits_df.index = range(1, len(hits_df) + 1)
                    st.dataframe(hits_df, use_container_width=True)
                else:
                    st.info("No hits recorded yet.")
            with col_bb:
                st.subheader("Walk Leaders")
                if bb_leaders:
                    bb_df = pd.DataFrame([{"Player": p["name"], "Country": p["team"], "BB": p["bb"]} for p in bb_leaders])
                    bb_df.index = range(1, len(bb_df) + 1)
                    st.dataframe(bb_df, use_container_width=True)
                else:
                    st.info("No walks recorded yet.")
        except Exception as e:
            st.error(f"Could not load stat leaders: {e}")

    if in_tournament_mode:
        tab_tourn, tab_pool, tab_stats = st.tabs(["🏆 Tournament", "📋 Pool Play Results", "📊 Offensive Leaders"])
        with tab_tourn:
            st.markdown(
                f'<div style="overflow-x:auto;padding:10px 0">{render_bracket_svg(wbc_df)}</div>',
                unsafe_allow_html=True,
            )
        with tab_pool:
            if pool_standings:
                st.subheader("Pool Play Standings")
                render_standings(pool_standings)
            pool_results = wbc_df[
                wbc_df["Round"].str.startswith("Pool") &
                wbc_df["_raw_status"].apply(_is_final_state)
            ].sort_values("_sort_dt", ascending=False)
            if not pool_results.empty:
                st.subheader("Game Results")
                st.dataframe(
                    pool_results[["Round", "Date", "Away", "Home"]].style.apply(style_wbc_row, axis=1),
                    hide_index=True,
                )
        with tab_stats:
            render_stat_leaders()
    else:
        if not today_games.empty:
            st.subheader("Today's Games")
            today_display = today_games[["Round", "Date", "Away", "Home", "Status"]]
            st.dataframe(today_display.style.apply(style_wbc_row, axis=1), hide_index=True)
        if pool_standings:
            st.subheader("Pool Play Standings")
            render_standings(pool_standings)
        render_stat_leaders()

    if not in_tournament_mode and not results.empty:
        st.subheader("Results")
        results_display = results[["Round", "Date", "Away", "Home"]]
        st.dataframe(results_display.style.apply(style_wbc_row, axis=1), hide_index=True)

    if not in_tournament_mode and not game_log.empty:
        st.subheader("Upcoming Games")
        game_log_display = game_log[["Round", "Date", "Away", "Home"]]
        st.dataframe(game_log_display.style.apply(style_wbc_row, axis=1), hide_index=True)

except Exception as e:
    st.error(f"Could not load WBC schedule: {e}")
