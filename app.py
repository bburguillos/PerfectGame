
import random
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ==========================================
# APP CONFIGURATION
# ==========================================

st.set_page_config(
    page_title="You're the Manager | Shutout Challenge",
    page_icon="⚾",
    layout="wide"
)

API = "https://statsapi.mlb.com/api/v1"

st.markdown("""
<style>
.block-container {
    max-width: 1800px;
    padding-top: 0.5rem;
    padding-bottom: 1rem;
}
h1 {
    color: #F47721;
    font-size: 2rem !important;
}
h3 {
    font-size: 1.15rem !important;
}
[data-testid="stMetric"] {
    background: #193449;
    border-radius: 10px;
    padding: 10px;
}
[data-testid="stMetric"] label,
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: white !important;
}
</style>
""", unsafe_allow_html=True)


# ==========================================
# GENERAL HELPERS
# ==========================================

def num(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def hand_label(code):
    return {
        "L": "LHP",
        "R": "RHP",
        "S": "Switch",
    }.get(code, "Unknown")


def batting_hand(code):
    return {
        "L": "L",
        "R": "R",
        "S": "S",
    }.get(code, "?")


def pct(value):
    return f"{value * 100:.1f}%"


# ==========================================
# MLB DATA CONNECTION
# ==========================================

@st.cache_data(ttl=86400, show_spinner=False)
def api_get(path, params=None):
    response = requests.get(
        f"{API}/{path}",
        params=params or {},
        timeout=20
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=86400, show_spinner=False)
def get_teams(year):
    data = api_get(
        "teams",
        {"sportId": 1, "season": year}
    )
    return sorted(
        data.get("teams", []),
        key=lambda x: x["name"]
    )


@st.cache_data(ttl=86400, show_spinner=False)
def get_roster(team_id, year):
    data = api_get(
        f"teams/{team_id}/roster",
        {
            "rosterType": "fullSeason",
            "season": year
        }
    )
    return data.get("roster", [])


@st.cache_data(ttl=86400, show_spinner=False)
def player_info(pid):
    data = api_get(f"people/{pid}")
    people = data.get("people", [])
    return people[0] if people else {}


@st.cache_data(ttl=86400, show_spinner=False)
def player_stats(pid, year, group):
    data = api_get(
        f"people/{pid}/stats",
        {
            "stats": "season",
            "group": group,
            "season": year,
            "gameType": "R"
        }
    )

    for section in data.get("stats", []):
        for split in section.get("splits", []):
            if split.get("stat"):
                return split["stat"]

    return {}


@st.cache_data(ttl=86400, show_spinner=False)
def load_team(team_id, year, kind):
    entries = get_roster(team_id, year)

    if kind == "pitchers":
        group = "pitching"
        entries = [
            item for item in entries
            if item.get("position", {}).get(
                "abbreviation"
            ) in ("P", "TWP")
        ]
    else:
        group = "hitting"
        entries = [
            item for item in entries
            if item.get("position", {}).get(
                "abbreviation"
            ) not in ("P", "TWP")
        ]

    players = []

    for item in entries:
        person = item.get("person", {})
        pid = person.get("id")

        if not pid:
            continue

        try:
            stats = player_stats(pid, year, group)

            if not stats:
                continue

            if kind == "pitchers":
                if num(stats.get("inningsPitched")) < 1:
                    continue
            else:
                if num(stats.get("atBats")) < 1:
                    continue

            info = player_info(pid)

            player = {
                "id": pid,
                "name": person.get(
                    "fullName", "Unknown"
                ),
                "stats": stats,
                "position": item.get(
                    "position", {}
                ).get("abbreviation", ""),
                "throws": info.get(
                    "pitchHand", {}
                ).get("code", "?"),
                "bats": info.get(
                    "batSide", {}
                ).get("code", "?")
            }

            if kind == "pitchers":
                starts = num(
                    stats.get("gamesStarted")
                )
                games = max(
                    1, num(stats.get("gamesPlayed"))
                )

                player["role"] = (
                    "SP"
                    if starts >= max(3, games * 0.35)
                    else "RP"
                )

                player["relief_apps"] = max(
                    0, games - starts
                )

            players.append(player)

        except requests.RequestException:
            continue

    if kind == "pitchers":
        starters = sorted(
            [
                p for p in players
                if p["role"] == "SP"
            ],
            key=lambda p: -num(
                p["stats"].get("gamesStarted")
            )
        )[:5]

        bullpen = sorted(
            [
                p for p in players
                if p["role"] == "RP"
            ],
            key=lambda p: -p["relief_apps"]
        )[:8]

        return starters + bullpen

    return sorted(
        players,
        key=lambda p: -num(
            p["stats"].get(
                "plateAppearances",
                p["stats"].get("atBats")
            )
        )
    )[:13]


# ==========================================
# PITCHER VS BATTER HISTORY
# ==========================================

@st.cache_data(ttl=86400, show_spinner=False)
def head_to_head(pitcher_id, batter_id, year):
    try:
        data = api_get(
            f"people/{pitcher_id}/stats",
            {
                "stats": "vsPlayer",
                "group": "pitching",
                "season": year,
                "opposingPlayerId": batter_id,
                "gameType": "R"
            }
        )

        for section in data.get("stats", []):
            for split in section.get("splits", []):
                opponent = split.get(
                    "opponent", {}
                )
                player = split.get("player", {})

                opponent_id = opponent.get(
                    "id",
                    player.get("id")
                )

                if str(opponent_id) != str(batter_id):
                    continue

                stats = split.get("stat", {})

                if not stats:
                    continue

                return stats

    except (
        requests.RequestException,
        ValueError,
        KeyError
    ):
        pass

    return None


# ==========================================
# GAME INITIALIZATION
# ==========================================

def create_game(
    year, team, opponent,
    staff, hitters, starter_id
):
    return {
        "year": year,
        "team": team,
        "opponent": opponent,
        "staff": staff,
        "lineup": [
            p.copy() for p in hitters[:9]
        ],
        "bench": [
            p.copy() for p in hitters[9:]
        ],
        "pitcher": starter_id,
        "used": [starter_id],
        "pitches": {
            p["id"]: 0 for p in staff
        },
        "batters_faced": {
            p["id"]: 0 for p in staff
        },
        "appearance_batters": 0,
        "entry_inning": 1,
        "warming": {},
        "ready": [],
        "inning": 1,
        "outs": 0,
        "runs": 0,
        "hits": 0,
        "walks": 0,
        "bases": [False, False, False],
        "spot": 0,
        "finished": False,
        "log": [
            "PLAY BALL! The shutout challenge begins."
        ],
        "decisions": []
    }


def current_pitcher(game):
    return next(
        p for p in game["staff"]
        if p["id"] == game["pitcher"]
    )


def current_batter(game):
    return game["lineup"][game["spot"] % 9]


# ==========================================
# PITCHER FATIGUE
# ==========================================

def fatigue(game):
    pitcher = current_pitcher(game)

    # Forgiving educational stamina model.
    # Approximately half the buildup of version 1.
    limit = (
        190 if pitcher["role"] == "SP"
        else 46
    )

    return (
        game["pitches"][pitcher["id"]]
        / limit
    )


def can_change(game):
    if game["finished"]:
        return False

    return (
        game["appearance_batters"] >= 3
        or game["inning"] > game["entry_inning"]
    )


# ==========================================
# INTERACTIVE BULLPEN
# ==========================================

def warm_up(game, pid):
    if (
        pid in game["used"]
        or pid in game["ready"]
        or pid in game["warming"]
    ):
        return False

    game["warming"][pid] = 0

    pitcher = next(
        p for p in game["staff"]
        if p["id"] == pid
    )

    game["log"].append(
        f"🔥 {pitcher['name']} "
        f"({hand_label(pitcher['throws'])}) "
        "begins warming."
    )

    return True


def advance_warmups(game):
    for pid in list(game["warming"]):
        game["warming"][pid] += 1

        if game["warming"][pid] >= 2:
            game["ready"].append(pid)
            del game["warming"][pid]

            pitcher = next(
                p for p in game["staff"]
                if p["id"] == pid
            )

            game["log"].append(
                f"✅ {pitcher['name']} is READY!"
            )


def change_pitcher(game, pid, reason):
    if not can_change(game):
        return False

    if (
        pid not in game["ready"]
        or pid in game["used"]
    ):
        return False

    old = current_pitcher(game)

    new = next(
        p for p in game["staff"]
        if p["id"] == pid
    )

    game["pitcher"] = pid
    game["used"].append(pid)
    game["ready"].remove(pid)

    game["appearance_batters"] = 0
    game["entry_inning"] = game["inning"]

    game["decisions"].append({
        "Inning": game["inning"],
        "Outs": game["outs"],
        "Removed": old["name"],
        "Entered": new["name"],
        "Hand": hand_label(new["throws"]),
        "Reason": reason
    })

    game["log"].append(
        f"🔁 PITCHING CHANGE: "
        f"{new['name']} "
        f"({hand_label(new['throws'])}) "
        f"replaces {old['name']}."
    )

    return True


# ==========================================
# COMPUTER PINCH HITTING
# ==========================================

def computer_pinch_hit(game):
    if game["inning"] < 7:
        return

    if not game["bench"]:
        return

    index = game["spot"] % 9
    batter = game["lineup"][index]
    pitcher = current_pitcher(game)

    candidates = sorted(
        game["bench"],
        key=lambda p: num(
            p["stats"].get("avg")
        ),
        reverse=True
    )

    replacement = candidates[0]

    old_avg = num(
        batter["stats"].get("avg")
    )
    new_avg = num(
        replacement["stats"].get("avg")
    )

    same_hand = (
        batter["bats"] in ("L", "R")
        and batter["bats"] == pitcher["throws"]
    )

    if (
        (old_avg < .235 and new_avg > old_avg + .035)
        or
        (same_hand and new_avg > old_avg + .065)
    ):
        game["lineup"][index] = replacement
        game["bench"].remove(replacement)

        game["log"].append(
            f"📣 PINCH HITTER! "
            f"{replacement['name']} replaces "
            f"{batter['name']}."
        )


# ==========================================
# BASE RUNNER LOGIC
# ==========================================

def advance_hit(game, distance):
    new_bases = [False, False, False]
    runs = 0

    for index in (2, 1, 0):
        if game["bases"][index]:
            destination = index + distance

            if destination >= 3:
                runs += 1
            else:
                new_bases[destination] = True

    if distance == 4:
        runs += 1
    else:
        new_bases[distance - 1] = True

    game["bases"] = new_bases
    game["runs"] += runs

    return runs


def advance_walk(game):
    first, second, third = game["bases"]

    runs = int(first and second and third)

    game["bases"] = [
        True,
        first or second,
        third or (first and second)
    ]

    game["walks"] += 1
    game["runs"] += runs

    return runs


# ==========================================
# SIMULATE AN AT-BAT
# ==========================================

def simulate_at_bat(game):
    if game["finished"]:
        return

    computer_pinch_hit(game)

    pitcher = current_pitcher(game)
    batter = current_batter(game)

    ps = pitcher["stats"]
    hs = batter["stats"]

    avg = num(hs.get("avg"), .250)
    obp = num(hs.get("obp"), .320)
    slg = num(hs.get("slg"), .400)

    bf = max(
        1, num(ps.get("battersFaced"), 300)
    )

    k_rate = (
        num(ps.get("strikeOuts")) / bf
    )

    bb_rate = (
        num(ps.get("baseOnBalls")) / bf
    )

    tired = max(
        0, fatigue(game) - .75
    )

    same_hand = (
        batter["bats"] in ("L", "R")
        and batter["bats"] == pitcher["throws"]
    )

    hand_adjust = (
        -.015 if same_hand else .008
    )

    # Educational simulation, not an official
    # MLB predictive probability model.
    hit_prob = clamp(
        avg
        + (num(ps.get("whip"), 1.3) - 1.3) * .055
        + (num(ps.get("era"), 4.2) - 4.2) * .008
        + hand_adjust
        + tired * .065,
        .075,
        .48
    )

    walk_prob = clamp(
        .82 * bb_rate
        + .12 * max(0, obp - avg)
        + tired * .025,
        .025,
        .16
    )

    strike_prob = clamp(
        k_rate - tired * .035,
        .08,
        .40
    )

    pitches = random.randint(3, 8)

    game["pitches"][pitcher["id"]] += pitches
    game["batters_faced"][pitcher["id"]] += 1
    game["appearance_batters"] += 1

    roll = random.random()
    scored = 0

    if roll < walk_prob:
        scored = advance_walk(game)
        result = "walks"

    elif roll < walk_prob + hit_prob:
        power = clamp(
            slg - avg, .06, .38
        )

        hit_roll = random.random()

        if hit_roll < power * .24:
            distance = 4
        elif hit_roll < power * .27:
            distance = 3
        elif hit_roll < power * .69:
            distance = 2
        else:
            distance = 1

        scored = advance_hit(game, distance)
        game["hits"] += 1

        result = {
            1: "singles",
            2: "doubles",
            3: "triples",
            4: "HOMERS"
        }[distance]

    else:
        game["outs"] += 1

        if roll < (
            walk_prob + hit_prob + strike_prob
        ):
            result = "strikes out"
        else:
            result = "is retired on a ball in play"

    message = (
        f"Inning {game['inning']}: "
        f"{batter['name']} {result} "
        f"against {pitcher['name']}."
    )

    if scored:
        message += f" {scored} run(s) score!"

    game["log"].append(message)

    game["spot"] += 1

    advance_warmups(game)

    if game["outs"] >= 3:
        game["log"].append(
            f"End of inning {game['inning']}."
        )

        game["inning"] += 1
        game["outs"] = 0
        game["bases"] = [False, False, False]

    if game["inning"] > 9:
        game["finished"] = True

        if game["runs"] == 0:
            game["log"].append(
                "🏆 SHUTOUT COMPLETE!"
            )
        else:
            game["log"].append(
                f"FINAL: {game['runs']} runs allowed."
            )


# ==========================================
# DANGER DETECTION
# ==========================================

def danger_status(game):
    first, second, third = game["bases"]
    runners = sum(game["bases"])

    if game["finished"]:
        return False, "Game complete"

    if runners == 3:
        return True, "🚨 BASES LOADED!"

    if second or third:
        return True, "⚠️ RUNNER IN SCORING POSITION!"

    if game["outs"] == 2 and runners:
        return True, "⚠️ TWO-OUT JAM!"

    if game["inning"] >= 7 and runners:
        return True, "🔥 LATE-INNING PRESSURE!"

    if fatigue(game) >= .85:
        return True, "🔋 FATIGUE DANGER!"

    return False, "No immediate danger"


# ==========================================
# LIVE BASEBALL DIAMOND
# ==========================================

def draw_field(game):

    first, second, third = game["bases"]

    def base(x, y, occupied):
        color = (
            "#FFBA32" if occupied
            else "#FFFFFF"
        )

        return f"""
        <rect x="{x}" y="{y}"
              width="18" height="18"
              transform="rotate(45 {x+9} {y+9})"
              fill="{color}"
              stroke="#143A2B"
              stroke-width="2"/>
        """

    svg = f"""
    <html>
    <body style="
        margin:0;
        padding:0;
        overflow:hidden;
        background:transparent;
    ">

    <svg viewBox="0 0 400 250"
         xmlns="http://www.w3.org/2000/svg"
         style="
            width:100%;
            height:250px;
            display:block;
         ">

      <!-- Outfield -->

      <rect width="400" height="250"
            rx="14" fill="#164D38"/>

      <!-- Grass stripes -->

      <path d="M0 35 H400"
            stroke="#236B4D"
            stroke-width="30"/>

      <path d="M0 115 H400"
            stroke="#236B4D"
            stroke-width="30"/>

      <path d="M0 195 H400"
            stroke="#236B4D"
            stroke-width="30"/>

      <!-- Infield dirt -->

      <path d="M200 228 L65 112
               L200 12 L335 112 Z"
            fill="#BE8B58"
            stroke="#F2D6AA"
            stroke-width="3"/>

      <!-- Infield grass -->

      <path d="M200 206 L91 112
               L200 33 L309 112 Z"
            fill="#267A50"/>

      <!-- Foul lines -->

      <line x1="200" y1="228"
            x2="23" y2="78"
            stroke="white"
            stroke-width="2"/>

      <line x1="200" y1="228"
            x2="377" y2="78"
            stroke="white"
            stroke-width="2"/>

      <!-- Bases -->

      {base(300, 103, first)}
      {base(191, 22, second)}
      {base(82, 103, third)}
      {base(191, 213, False)}

      <!-- Pitching mound -->

      <circle cx="200" cy="122"
              r="13" fill="#D5AE78"
              stroke="#B8854F"
              stroke-width="2"/>

      <rect x="193" y="120"
            width="14" height="4"
            rx="1" fill="white"/>

      <!-- Occupied bases legend -->

      <rect x="14" y="12"
            width="11" height="11"
            fill="#FFBA32"/>

      <text x="31" y="22"
            font-size="12"
            fill="white">
        Runner on base
      </text>

      <text x="200" y="245"
            text-anchor="middle"
            font-size="12"
            font-weight="bold"
            fill="white">
        HOME PLATE
      </text>

    </svg>

    </body>
    </html>
    """

    # IMPORTANT:
    # Use Streamlit's HTML component.
    # st.markdown does not reliably render SVG.
    components.html(
        svg,
        height=255,
        scrolling=False
    )


# ==========================================
# HEADER
# ==========================================

st.title("⚾ YOU'RE THE MANAGER")

st.caption(
    "SPORTS BY THE NUMBERS | "
    "MLB SHUTOUT CHALLENGE | 2000–2025"
)

if "game" not in st.session_state:
    st.session_state.game = None

if "loaded" not in st.session_state:
    st.session_state.loaded = None


# ==========================================
# GAME SETUP
# ==========================================

with st.expander(
    "⚙️ Historical Game Setup",
    expanded=st.session_state.game is None
):

    year = st.selectbox(
        "Historical Season",
        list(range(2025, 1999, -1))
    )

    try:
        teams = get_teams(year)

        names = {
            t["name"]: t["id"]
            for t in teams
        }

        col1, col2 = st.columns(2)

        with col1:
            team_name = st.selectbox(
                "Your MLB Team",
                list(names)
            )

        with col2:
            opponent_name = st.selectbox(
                "Opposing Team",
                [
                    name for name in names
                    if name != team_name
                ]
            )

        if st.button(
            "LOAD HISTORICAL ROSTERS",
            type="primary"
        ):

            with st.status(
                "Preparing historical matchup...",
                expanded=True
            ) as loading:

                progress = st.progress(0)

                st.write(
                    "⚾ Loading starters and bullpen..."
                )

                staff = load_team(
                    names[team_name],
                    year,
                    "pitchers"
                )

                progress.progress(50)

                st.write(
                    "🏏 Preparing opposing batting order..."
                )

                hitters = load_team(
                    names[opponent_name],
                    year,
                    "hitters"
                )

                progress.progress(90)

                if not staff or len(hitters) < 9:
                    st.error(
                        "Not enough historical player "
                        "data was returned."
                    )
                    st.session_state.loaded = None
                else:
                    st.session_state.loaded = {
                        "year": year,
                        "team": team_name,
                        "opponent": opponent_name,
                        "staff": staff,
                        "hitters": hitters
                    }

                    st.session_state.game = None

                progress.progress(100)

                loading.update(
                    label="Historical roster loading complete",
                    state="complete",
                    expanded=False
                )

    except requests.RequestException as error:
        st.error(
            "The MLB data service could not "
            "complete the request."
        )
        st.caption(str(error))


# ==========================================
# STARTER SELECTION
# ==========================================

loaded = st.session_state.loaded

if loaded and st.session_state.game is None:

    staff = loaded["staff"]

    starters = [
        p for p in staff
        if p["role"] == "SP"
    ] or staff

    lookup = {
        p["id"]: p
        for p in staff
    }

    starter_id = st.selectbox(
        "Choose Your Starting Pitcher",
        [p["id"] for p in starters],
        format_func=lambda pid: (
            f"{lookup[pid]['name']} "
            f"({hand_label(lookup[pid]['throws'])}) "
            f"| ERA "
            f"{lookup[pid]['stats'].get('era', '—')}"
        )
    )

    if st.button(
        "⚾ PLAY BALL!",
        type="primary"
    ):

        st.session_state.game = create_game(
            loaded["year"],
            loaded["team"],
            loaded["opponent"],
            loaded["staff"],
            loaded["hitters"],
            starter_id
        )

        st.rerun()


# ==========================================
# LIVE GAME
# ==========================================

if st.session_state.game is not None:

    game = st.session_state.game

    pitcher = current_pitcher(game)
    batter = current_batter(game)

    st.subheader(
        f"{game['year']} | "
        f"{game['team']} vs. "
        f"{game['opponent']}"
    )

    # SCOREBOARD

    boxes = st.columns(5)

    values = [
        min(game["inning"], 9),
        game["outs"],
        game["runs"],
        game["hits"],
        game["pitches"][pitcher["id"]]
    ]

    labels = [
        "Inning",
        "Outs",
        "Runs Allowed",
        "Hits Allowed",
        "Pitch Count"
    ]

    for box, label, value in zip(
        boxes, labels, values
    ):
        box.metric(label, value)

    # THREE-COLUMN GAME DASHBOARD

    left, middle, right = st.columns(
        [1.2, 1.35, 1.2],
        gap="medium"
    )

    # =====================================
    # LEFT COLUMN: PITCHING STAFF
    # =====================================

    with left:

        st.markdown("### ⚾ Pitching Staff")

        staff_rows = []

        for p in game["staff"]:

            pid = p["id"]

            if pid == game["pitcher"]:
                status = "🔵 ON MOUND"
            elif pid in game["used"]:
                status = "USED"
            elif pid in game["ready"]:
                status = "🟢 READY"
            elif pid in game["warming"]:
                status = "🟡 WARMING"
            else:
                status = "AVAILABLE"

            staff_rows.append({
                "Pitcher": p["name"],
                "Role": p["role"],
                "Hand": hand_label(p["throws"]),
                "ERA": p["stats"].get("era", "—"),
                "WHIP": p["stats"].get("whip", "—"),
                "Status": status
            })

        st.dataframe(
            pd.DataFrame(staff_rows),
            use_container_width=True,
            hide_index=True,
            height=270
        )

        st.write(
            f"**On the Mound:** {pitcher['name']} "
            f"({hand_label(pitcher['throws'])})"
        )

        st.progress(
            min(1.0, fatigue(game)),
            text=f"Fatigue: {fatigue(game):.0%}"
        )

        st.caption(
            f"Pitches thrown: "
            f"{game['pitches'][pitcher['id']]}"
        )

        # BULLPEN MANAGEMENT

        if not game["finished"]:

            st.markdown("#### 🔥 Bullpen Management")

            available = [
                p for p in game["staff"]
                if p["id"] not in game["used"]
            ]

            resting = [
                p for p in available
                if p["id"] not in game["ready"]
                and p["id"] not in game["warming"]
            ]

            if resting:

                rest_lookup = {
                    p["id"]: p
                    for p in resting
                }

                warm_choice = st.selectbox(
                    "Select pitcher to warm up",
                    list(rest_lookup),
                    format_func=lambda pid: (
                        f"{rest_lookup[pid]['name']} "
                        f"({hand_label(rest_lookup[pid]['throws'])})"
                    )
                )

                if st.button(
                    "🔥 WARM UP PITCHER"
                ):
                    warm_up(game, warm_choice)
                    st.rerun()

            if game["warming"]:

                for pid, progress in game["warming"].items():

                    p = next(
                        x for x in game["staff"]
                        if x["id"] == pid
                    )

                    st.caption(
                        f"🟡 {p['name']} "
                        f"({hand_label(p['throws'])}) "
                        f"warming: {progress}/2"
                    )

            ready = [
                p for p in available
                if p["id"] in game["ready"]
            ]

            if ready:

                ready_lookup = {
                    p["id"]: p
                    for p in ready
                }

                relief_choice = st.selectbox(
                    "Ready Pitchers",
                    list(ready_lookup),
                    format_func=lambda pid: (
                        f"{ready_lookup[pid]['name']} "
                        f"({hand_label(ready_lookup[pid]['throws'])})"
                    )
                )

                reason = st.selectbox(
                    "Reason for Pitching Change",
                    [
                        "Fatigue",
                        "Left/Right Matchup",
                        "Runner in Scoring Position",
                        "Strikeout Ability",
                        "Protect the Shutout"
                    ]
                )

                if st.button(
                    "🔁 MAKE PITCHING CHANGE",
                    disabled=not can_change(game),
                    type="primary"
                ):
                    change_pitcher(
                        game,
                        relief_choice,
                        reason
                    )
                    st.rerun()

            if not can_change(game):
                st.caption(
                    "Pitcher must face three batters "
                    "or finish the half-inning."
                )

    # =====================================
    # MIDDLE COLUMN: LIVE DIAMOND
    # =====================================

    with middle:

        st.markdown("### 🏟️ Live Diamond")

        # Fixed SVG field rendering
        draw_field(game)

        st.write(
            f"**At Bat:** {batter['name']} "
            f"({batting_hand(batter['bats'])})"
        )

        st.write(
            f"**Pitcher:** {pitcher['name']} "
            f"({hand_label(pitcher['throws'])})"
        )

        if game["finished"]:

            if game["runs"] == 0:
                st.success(
                    "🏆 SHUTOUT! "
                    "Nine scoreless innings!"
                )
            else:
                st.info(
                    f"Final: {game['runs']} "
                    "runs allowed."
                )

        elif st.button(
            "⚾ PITCH TO BATTER",
            type="primary",
            use_container_width=True
        ):

            simulate_at_bat(game)
            st.rerun()

        st.markdown("#### 📣 Play-by-Play")

        for entry in reversed(game["log"][-6:]):
            st.caption(entry)

    # =====================================
    # RIGHT COLUMN: OPPOSING LINEUP
    # =====================================

    with right:

        st.markdown("### 🧢 Opposing Lineup")

        current_spot = game["spot"] % 9

        rows = []

        for index, player in enumerate(
            game["lineup"]
        ):

            if index == current_spot:
                status = "🔴 AT BAT"

            elif index == (
                current_spot + 1
            ) % 9:
                status = "🟡 ON DECK"

            elif index == (
                current_spot + 2
            ) % 9:
                status = "⚪ IN HOLE"

            else:
                status = ""

            rows.append({
                "#": index + 1,
                "Status": status,
                "Batter": player["name"],
                "Bats": batting_hand(
                    player["bats"]
                ),
                "AVG": player["stats"].get(
                    "avg", "—"
                )
            })

        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            use_container_width=True,
            height=270
        )

        # =================================
        # DANGER SCOUTING
        # =================================

        danger, danger_message = danger_status(
            game
        )

        st.markdown("### 🧠 Situation Room")

        if danger:

            st.error(danger_message)

            st.markdown(
                "#### 🚨 Pitcher vs. Batter"
            )

            st.write(
                f"**{pitcher['name']}** "
                f"({hand_label(pitcher['throws'])}) "
                f"vs. **{batter['name']}** "
                f"({batting_hand(batter['bats'])})"
            )

            with st.spinner(
                "Checking MLB historical matchup..."
            ):
                matchup = head_to_head(
                    pitcher["id"],
                    batter["id"],
                    game["year"]
                )

            if matchup:

                m1, m2, m3 = st.columns(3)

                m1.metric(
                    "Hits",
                    matchup.get("hits", "—")
                )

                m2.metric(
                    "At-Bats",
                    matchup.get("atBats", "—")
                )

                m3.metric(
                    "Strikeouts",
                    matchup.get(
                        "strikeOuts", "—"
                    )
                )

                st.metric(
                    "Head-to-Head AVG",
                    matchup.get("avg", "—")
                )

                if num(
                    matchup.get("atBats")
                ) < 20:
                    st.warning(
                        "Small sample size! "
                        "Be careful making predictions."
                    )

            else:

                st.info(
                    "No verified head-to-head "
                    "record was returned for "
                    "these players."
                )

            st.markdown(
                "#### 📊 Season Statistics"
            )

            s1, s2 = st.columns(2)

            with s1:
                st.metric(
                    "Batter AVG",
                    batter["stats"].get(
                        "avg", "—"
                    )
                )

                st.metric(
                    "Batter OBP",
                    batter["stats"].get(
                        "obp", "—"
                    )
                )

            with s2:
                st.metric(
                    "Pitcher ERA",
                    pitcher["stats"].get(
                        "era", "—"
                    )
                )

                st.metric(
                    "Pitcher WHIP",
                    pitcher["stats"].get(
                        "whip", "—"
                    )
                )

            bf = max(
                1,
                num(
                    pitcher["stats"].get(
                        "battersFaced"
                    )
                )
            )

            k_rate = (
                num(
                    pitcher["stats"].get(
                        "strikeOuts"
                    )
                ) / bf
            )

            walk_rate = (
                num(
                    pitcher["stats"].get(
                        "baseOnBalls"
                    )
                ) / bf
            )

            st.write(
                f"**Strikeout rate:** {pct(k_rate)}"
            )

            st.write(
                f"**Walk rate:** {pct(walk_rate)}"
            )

            st.info(
                "MANAGER'S DECISION: "
                "Should this pitcher stay in "
                "or should you use the bullpen?"
            )

        else:

            st.success(
                "✅ No immediate danger."
            )

            st.caption(
                "Detailed scouting appears "
                "when the game enters a "
                "dangerous situation."
            )

            st.write(
                f"Current Batter: "
                f"**{batter['name']}**"
            )

            st.write(
                f"Season AVG: "
                f"**{batter['stats'].get('avg', '—')}**"
            )

    # =====================================
    # MANAGER LOG AND NEW GAME
    # =====================================

    with st.expander("📋 Manager Decision Log"):

        if game["decisions"]:
            st.dataframe(
                pd.DataFrame(game["decisions"]),
                hide_index=True
            )
        else:
            st.write(
                "No pitching changes yet."
            )

    if game["finished"]:

        report = pd.DataFrame(
            game["decisions"],
            columns=[
                "Inning", "Outs", "Removed",
                "Entered", "Hand", "Reason"
            ]
        ).to_csv(index=False)

        st.download_button(
            "Download Manager Report",
            data=report,
            file_name="manager_report.csv",
            mime="text/csv"
        )

    if st.button("START A NEW GAME"):
        st.session_state.game = None
        st.session_state.loaded = None
        st.rerun()


st.caption(
    "Sports by the Numbers | "
    "Real MLB historical statistics support "
    "an educational baseball simulation. "
    "At-bat results and fatigue are simulated."
)
