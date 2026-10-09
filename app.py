
import streamlit as st
import requests
from datetime import date

st.set_page_config(
    page_title="You're the Manager",
    page_icon="⚾",
    layout="wide"
)

API = "https://statsapi.mlb.com/api/v1"

@st.cache_data(ttl=3600, show_spinner=False)
def mlb_get(path, params=None):
    response = requests.get(
        f"{API}/{path}",
        params=params or {},
        timeout=20
    )
    response.raise_for_status()
    return response.json()

@st.cache_data(ttl=3600)
def get_teams(year):
    data = mlb_get("teams", {
        "sportId": 1,
        "season": year
    })
    teams = data.get("teams", [])
    return sorted(
        teams,
        key=lambda team: team["name"]
    )

@st.cache_data(ttl=3600)
def get_roster(team_id, year):
    data = mlb_get(
        f"teams/{team_id}/roster",
        {
            "rosterType": "fullSeason",
            "season": year
        }
    )
    return data.get("roster", [])

@st.cache_data(ttl=3600)
def get_stats(player_id, year, group):
    data = mlb_get(
        f"people/{player_id}/stats",
        {
            "stats": "season",
            "group": group,
            "season": year,
            "gameType": "R"
        }
    )
    for section in data.get("stats", []):
        for split in section.get("splits", []):
            return split.get("stat", {})
    return {}

@st.cache_data(ttl=3600)
def get_player(player_id):
    data = mlb_get(f"people/{player_id}")
    people = data.get("people", [])
    return people[0] if people else {}

def number(value, default="N/A"):
    if value is None or value == "":
        return default
    return str(value)

st.title("⚾ SPORTS BY THE NUMBERS")
st.subheader("YOU'RE THE MANAGER: SHUTOUT CHALLENGE")

st.write(
    "Travel through baseball history. Build your "
    "pitching staff using real MLB statistics."
)

st.info(
    "Mission: Manage nine defensive innings "
    "and allow ZERO runs."
)

year = st.selectbox(
    "📅 Choose a historical MLB season",
    list(range(2025, 1969, -1)),
    index=0
)

try:
    teams = get_teams(year)

    if not teams:
        st.warning(
            "No MLB teams were returned for this season."
        )
        st.stop()

    team_lookup = {
        team["name"]: team["id"]
        for team in teams
    }

    col1, col2 = st.columns(2)

    with col1:
        team_name = st.selectbox(
            "🏟️ Your MLB Team",
            list(team_lookup.keys())
        )

    with col2:
        opponents = [
            name for name in team_lookup
            if name != team_name
        ]
        opponent_name = st.selectbox(
            "⚔️ Opposing Team",
            opponents
        )

    your_roster = get_roster(
        team_lookup[team_name], year
    )

    opponent_roster = get_roster(
        team_lookup[opponent_name], year
    )

    def pitchers_only(roster):
        return [
            item for item in roster
            if item.get("position", {}).get(
                "type"
            ) == "Pitcher"
            or item.get("position", {}).get(
                "abbreviation"
            ) == "P"
        ]

    def batters_only(roster):
        return [
            item for item in roster
            if item.get("position", {}).get(
                "type"
            ) != "Pitcher"
        ]

    pitchers = pitchers_only(your_roster)
    hitters = batters_only(opponent_roster)

    st.divider()

    st.header("⚾ Build Your Pitching Staff")

    if not pitchers:
        st.warning(
            "No historical pitching roster was returned "
            "for this team and season. Try another "
            "team or season."
        )
    else:
        pitcher_lookup = {
            item["person"]["fullName"]:
            item["person"]["id"]
            for item in pitchers
        }

        pitcher_name = st.selectbox(
            "Choose Your Starting Pitcher",
            list(pitcher_lookup)
        )

        pitcher_id = pitcher_lookup[pitcher_name]

        stats = get_stats(
            pitcher_id, year, "pitching"
        )

        player = get_player(pitcher_id)

        st.subheader(
            f"📊 {pitcher_name} — {year}"
        )

        st.write(
            "Throws:",
            player.get(
                "pitchHand", {}
            ).get("description", "Unknown")
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "ERA",
            number(stats.get("era"))
        )
        c2.metric(
            "WHIP",
            number(stats.get("whip"))
        )
        c3.metric(
            "Strikeouts",
            number(stats.get("strikeOuts"))
        )
        c4.metric(
            "Innings Pitched",
            number(stats.get("inningsPitched"))
        )

        c5, c6, c7, c8 = st.columns(4)

        c5.metric(
            "Walks",
            number(stats.get("baseOnBalls"))
        )
        c6.metric(
            "Home Runs Allowed",
            number(stats.get("homeRuns"))
        )
        c7.metric(
            "Games Started",
            number(stats.get("gamesStarted"))
        )
        c8.metric(
            "Saves",
            number(stats.get("saves"))
        )

        with st.expander(
            "View Additional Pitching Statistics"
        ):
            st.json(stats)

        st.session_state["selected_pitcher"] = {
            "name": pitcher_name,
            "id": pitcher_id,
            "year": year,
            "stats": stats
        }

    st.divider()

    st.header("🏏 Scout the Opposing Hitters")

    if not hitters:
        st.warning(
            "No opposing hitters were returned "
            "for this historical roster."
        )
    else:
        hitter_lookup = {
            item["person"]["fullName"]:
            item["person"]["id"]
            for item in hitters
        }

        hitter_name = st.selectbox(
            "Choose an Opposing Hitter",
            list(hitter_lookup)
        )

        hitter_id = hitter_lookup[hitter_name]

        batting = get_stats(
            hitter_id, year, "hitting"
        )

        hitter = get_player(hitter_id)

        st.write(
            "Bats:",
            hitter.get(
                "batSide", {}
            ).get("description", "Unknown")
        )

        b1, b2, b3, b4 = st.columns(4)

        b1.metric(
            "Batting Average",
            number(batting.get("avg"))
        )
        b2.metric(
            "Hits",
            number(batting.get("hits"))
        )
        b3.metric(
            "Home Runs",
            number(batting.get("homeRuns"))
        )
        b4.metric(
            "At-Bats",
            number(batting.get("atBats"))
        )

        with st.expander(
            "View All Available Batting Statistics"
        ):
            st.json(batting)

    st.divider()

    st.header("🎮 Shutout Simulator")

    st.success(
        "Historical team selection, scouting, "
        "and pitcher selection are ready."
    )

    st.caption(
        "Coming in the next build: bullpen roster, "
        "fatigue, pitch counts, simulated at-bats, "
        "baserunning, and nine-inning gameplay."
    )

    st.warning(
        "This version is a historical scouting screen, "
        "not yet a playable baseball simulation."
    )

except requests.RequestException as error:
    st.error(
        "MLB's data service did not respond successfully. "
        "Try refreshing or choosing another season."
    )
    st.caption(str(error))
