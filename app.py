import streamlit as st
import statsapi
import pandas as pd
import requests
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo

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
            if game.get("status", {}).get("detailedState") in ("Final", "Game Over"):
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


def build_wbc_df(schedule_data):
    rows = []
    for date_entry in schedule_data.get("dates", []):
        for game in date_entry.get("games", []):
            if game.get("gameType") in ("E", "S", "R"):
                continue
            away = shorten(game["teams"]["away"]["team"]["name"])
            home = shorten(game["teams"]["home"]["team"]["name"])
            away_score = game["teams"]["away"].get("score")
            home_score = game["teams"]["home"].get("score")
            status = game["status"]["detailedState"]
            game_time_utc = game["gameDate"]
            game_date_pt = _to_pt_date(game_time_utc)
            game_date = game_date_pt.strftime("%Y-%m-%d")
            venue = game.get("venue", {}).get("name", "")

            # Format date for display in PT, with time if known (avoid %-d/%-I, Windows-incompatible)
            try:
                dt_utc = datetime.fromisoformat(game_time_utc.replace("Z", "+00:00"))
                dt_pt = dt_utc.astimezone(PACIFIC)
                month_day = f"{dt_pt.strftime('%b')} {dt_pt.day}"
                if dt_pt.hour == 0 and dt_pt.minute == 0:
                    date_str = month_day
                else:
                    hour = dt_pt.hour % 12 or 12
                    am_pm = "AM" if dt_pt.hour < 12 else "PM"
                    date_str = f"{month_day} - {hour}:{dt_pt.strftime('%M')} {am_pm} PT"
            except Exception:
                date_str = game_date

            away_display = away
            home_display = home
            if away_score is not None and home_score is not None:
                if status in ("Final", "Game Over"):
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
                "Round": round_label,
                "Date": date_str,
                "Away": away_display,
                "Home": home_display,
                "Status": status,
            })

    df = pd.DataFrame(rows, columns=["_sort_dt", "_date", "Round", "Date", "Away", "Home", "Status"])
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
            if game["status"]["detailedState"] not in ("Final", "Game Over"):
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




st.set_page_config(page_title="2026 World Baseball Classic", layout="wide")

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
st.caption(f"Data as of {_pt_today().strftime('%B %d, %Y')} PT")

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
    wbc_df = build_wbc_df(wbc_data)

    if wbc_df.empty:
        st.info("No WBC games found yet for 2026.")

    today_str = _pt_today().strftime("%Y-%m-%d")
    today_games = wbc_df[wbc_df["_date"] == today_str]
    results = wbc_df[(wbc_df["_date"] < today_str) & (wbc_df["Status"].isin(["Final", "Game Over"]))].sort_values(["_round_order", "_sort_dt"], ascending=False)
    game_log = wbc_df[
        (wbc_df["_date"] > today_str) |
        ((wbc_df["_date"] == today_str) & (~wbc_df["Status"].isin(["Final", "Game Over"])))
    ]

    if not today_games.empty:
        st.subheader("Today's Games")
        today_display = today_games[["Round", "Date", "Away", "Home", "Status"]]
        st.dataframe(today_display.style.apply(style_wbc_row, axis=1), hide_index=True)

    pool_standings = build_wbc_standings(wbc_data)
    if pool_standings:
        st.subheader("Pool Play Standings")
        cols = st.columns(len(pool_standings))
        for i, (pool_name, sdf) in enumerate(pool_standings.items()):
            with cols[i]:
                st.caption(pool_name)
                st.dataframe(
                    sdf,
                    column_config={"RD": st.column_config.NumberColumn("RD", format="%+d")},
                    use_container_width=True,
                )

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

    if not results.empty:
        st.subheader("Results")
        results_display = results[["Round", "Date", "Away", "Home"]]
        st.dataframe(results_display.style.apply(style_wbc_row, axis=1), hide_index=True)

    if not game_log.empty:
        st.subheader("Upcoming Games")
        game_log_display = game_log[["Round", "Date", "Away", "Home"]]
        st.dataframe(game_log_display.style.apply(style_wbc_row, axis=1), hide_index=True)

except Exception as e:
    st.error(f"Could not load WBC schedule: {e}")
