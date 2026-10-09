
import random
import requests
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="You're the Manager | Shutout Challenge",
    page_icon="⚾",
    layout="wide"
)

API = "https://statsapi.mlb.com/api/v1"

st.markdown("""
<style>
.block-container {
    max-width: 1700px;
    padding-top: .6rem;
    padding-bottom: 1rem;
}
h1 {color: #F47721; font-size: 2rem !important;}
h3 {font-size: 1.15rem !important;}
[data-testid="stMetric"] {
    background: #1D3547;
    padding: 9px;
    border-radius: 9px;
}
[data-testid="stMetric"] label,
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: white !important;
}
</style>
""", unsafe_allow_html=True)


# =====================================
# HELPER FUNCTIONS
# =====================================

def n(value, default=0.0):
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


def pct(value):
    return f"{value * 100:.1f}%"


# =====================================
# REAL MLB DATA
# =====================================

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
def player_info(player_id):
    data = api_get(f"people/{player_id}")
    people = data.get("people", [])
    return people[0] if people else {}


@st.cache_data(ttl=86400, show_spinner=False)
def player_stats(player_id, year, group):
    data = api_get(
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
            if split.get("stat"):
                return split["stat"]

    return {}


@st.cache_data(ttl=86400, show_spinner=False)
def load_team(team_id, year, kind):
    entries = get_roster(team_id, year)

    if kind == "pitchers":
        entries = [
            item for item in entries
            if item.get("position", {}).get(
                "abbreviation"
            ) in ("P", "TWP")
        ]
        group = "pitching"

    else:
        entries = [
            item for item in entries
            if item.get("position", {}).get(
                "abbreviation"
            ) not in ("P", "TWP")
        ]
        group = "hitting"

    result = []

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
                if n(stats.get("inningsPitched")) < 1:
                    continue
            else:
                if n(stats.get("atBats")) < 1:
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
                starts = n(stats.get("gamesStarted"))
                appearances = max(
                    1, n(stats.get("gamesPlayed"))
                )

                player["role"] = (
                    "SP"
                    if starts >= max(
                        3, appearances * .35
                    )
                    else "RP"
                )

                player["relief_apps"] = max(
                    0, appearances - starts
                )

            result.append(player)

        except requests.RequestException:
            continue

    if kind == "pitchers":
        starters = sorted(
            [p for p in result if p["role"] == "SP"],
            key=lambda p: -n(
                p["stats"].get("gamesStarted")
            )
        )[:5]

        relievers = sorted(
            [p for p in result if p["role"] == "RP"],
            key=lambda p: -p["relief_apps"]
        )[:8]

        return starters + relievers

    return sorted(
        result,
        key=lambda p: -n(
            p["stats"].get("plateAppearances"),
            n(p["stats"].get("atBats"))
        )
    )[:13]


# =====================================
# HEAD-TO-HEAD MLB DATA
# =====================================

@st.cache_data(ttl=86400, show_spinner=False)
def get_head_to_head(pitcher_id, batter_id, year):
    """
    Attempt to retrieve real pitcher-vs-batter data.

    A result is accepted only when the returned
    split explicitly identifies the requested
    opposing player. Missing data is not estimated
    or represented as an actual historical record.
    """

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
                    "id", player.get("id")
                )

                if str(opponent_id) != str(batter_id):
                    continue

                stat = split.get("stat", {})

                if not stat:
                    continue

                return {
                    "hits": stat.get("hits"),
                    "at_bats": stat.get("atBats"),
                    "strikeouts": stat.get(
                        "strikeOuts"
                    ),
                    "walks": stat.get("baseOnBalls"),
                    "home_runs": stat.get("homeRuns"),
                    "average": stat.get("avg")
                }

    except (
        requests.RequestException,
        ValueError,
        KeyError
    ):
        pass

    return None


# =====================================
# GAME STATE
# =====================================

def new_game(
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
        "entry_inning": 1,
        "entry_outs": 0,
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
            "PLAY BALL! Get 27 outs "
            "without allowing a run."
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


def fatigue(game):
    pitcher = current_pitcher(game)

    # Slower educational fatigue model.
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
        game["batters_faced"][game["pitcher"]] >= 3
        or game["inning"] > game["entry_inning"]
    )


# =====================================
# BULLPEN MANAGEMENT
# =====================================

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
        "starts warming."
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
    game["entry_inning"] = game["inning"]
    game["entry_outs"] = game["outs"]

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


# =====================================
# COMPUTER PINCH HITTING
# =====================================

def maybe_pinch_hit(game):
    if game["inning"] < 7:
        return

    if not game["bench"]:
        return

    ix = game["spot"] % 9
    batter = game["lineup"][ix]
    pitcher = current_pitcher(game)

    # Simple simulated opposing-manager logic.
    candidates = sorted(
        game["bench"],
        key=lambda x: n(x["stats"].get("avg")),
        reverse=True
    )

    replacement = candidates[0]

    old_avg = n(
        batter["stats"].get("avg")
    )
    new_avg = n(
        replacement["stats"].get("avg")
    )

    unfavorable = (
        batter["bats"] == pitcher["throws"]
        and batter["bats"] in ("L", "R")
    )

    if (
        (old_avg < .235 and new_avg > old_avg + .035)
        or
        (unfavorable and new_avg > old_avg + .065)
    ):
        game["lineup"][ix] = replacement
        game["bench"].remove(replacement)

        game["log"].append(
            f"📣 PINCH HITTER! "
            f"{replacement['name']} replaces "
            f"{batter['name']}."
        )


# =====================================
# BASERUNNING
# =====================================

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


# =====================================
# SIMULATED AT-BATS
# =====================================

def simulate_at_bat(game):
    if game["finished"]:
        return

    maybe_pinch_hit(game)

    pitcher = current_pitcher(game)
    batter = current_batter(game)

    ps = pitcher["stats"]
    hs = batter["stats"]

    avg = n(hs.get("avg"), .250)
    obp = n(hs.get("obp"), .320)
    slg = n(hs.get("slg"), .400)

    batters_faced = max(
        1, n(ps.get("battersFaced"), 300)
    )

    k_rate = (
        n(ps.get("strikeOuts"))
        / batters_faced
    )

    bb_rate = (
        n(ps.get("baseOnBalls"))
        / batters_faced
    )

    tired = max(
        0, fatigue(game) - .75
    )

    same_hand = (
        batter["bats"] in ("L", "R")
        and batter["bats"] == pitcher["throws"]
    )

    handedness_adjustment = (
        -.015 if same_hand else .008
    )

    # Simplified educational probability model.
    # Not a calibrated MLB forecasting model.
    hit_prob = clamp(
        avg
        + (n(ps.get("whip"), 1.3) - 1.3) * .055
        + (n(ps.get("era"), 4.2) - 4.2) * .008
        + handedness_adjustment
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

    pitch_count = random.randint(3, 8)

    game["pitches"][pitcher["id"]] += pitch_count
    game["batters_faced"][pitcher["id"]] += 1

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
            bases = 4
        elif hit_roll < power * .27:
            bases = 3
        elif hit_roll < power * .69:
            bases = 2
        else:
            bases = 1

        scored = advance_hit(game, bases)
        game["hits"] += 1

        result = {
            1: "singles",
            2: "doubles",
            3: "triples",
            4: "HOMERS"
        }[bases]

    else:
        game["outs"] += 1

        result = (
            "strikes out"
            if roll < (
                walk_prob
                + hit_prob
                + strike_prob
            )
            else "is retired on a ball in play"
        )

    entry = (
        f"Inning {game['inning']}: "
        f"{batter['name']} {result} "
        f"against {pitcher['name']}."
    )

    if scored:
        entry += f" {scored} run(s) score!"

    game["log"].append(entry)

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
                "🏆 COMPLETE GAME SHUTOUT!"
            )
        else:
            game["log"].append(
                f"FINAL: {game['runs']} runs allowed."
            )


# =====================================
# DANGER DETECTION
# =====================================

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
        return True, "🔋 PITCHER FATIGUE DANGER!"

    return False, "No immediate danger"


# =====================================
# BASEBALL DIAMOND
# =====================================

def draw_field(game):
    first, second, third = game["bases"]

    def base(x, y, occupied):
        color = (
            "#FFB638" if occupied
            else "#FFFFFF"
        )

        return (
            f'<rect x="{x}" y="{y}" '
            f'width="18" height="18" '
            f'transform="rotate(45 {x+9} {y+9})" '
            f'fill="{color}" '
            f'stroke="#143a2b" stroke-width="2"/>'
        )

    svg = f"""
    <svg viewBox="0 0 360 235"
         width="100%"
         style="max-height:250px">

      <rect width="360" height="235"
            rx="14" fill="#1A533B"/>

      <path d="M180 214 L54 105 L180 8
               L306 105 Z"
            fill="#BB8D5D"
            stroke="#F4D9AF"
            stroke-width="3"/>

      <path d="M180 197 L78 105 L180 27
               L282 105 Z"
            fill="#26764D"/>

      {base(272, 96, first)}
      {base(171, 18, second)}
      {base(70, 96, third)}
      {base(171, 200, False)}

      <circle cx="180" cy="111" r="11"
              fill="#DBBD87"/>

      <text x="180" y="116"
            text-anchor="middle"
            font-size="12">P</text>

      <text x="180" y="232"
            text-anchor="middle"
            fill="white"
            font-size="11">HOME</text>

    </svg>
    """

    st.markdown(svg, unsafe_allow_html=True)


# =====================================
# STREAMLIT APP HEADER
# =====================================

st.title("⚾ YOU'RE THE MANAGER")
st.caption(
    "SPORTS BY THE NUMBERS | "
    "HISTORICAL MLB SHUTOUT CHALLENGE"
)

if "game" not in st.session_state:
    st.session_state.game = None

if "loaded" not in st.session_state:
    st.session_state.loaded = None


# =====================================
# HISTORICAL GAME SETUP
# =====================================

with st.expander(
    "⚙️ Set Up Historical Matchup",
    expanded=st.session_state.game is None
):

    year = st.selectbox(
        "Select Season",
        list(range(2025, 1999, -1))
    )

    try:
        teams = get_teams(year)

        lookup = {
            t["name"]: t["id"]
            for t in teams
        }

        c1, c2 = st.columns(2)

        with c1:
            own_name = st.selectbox(
                "Your Team",
                list(lookup)
            )

        with c2:
            opponent_name = st.selectbox(
                "Opponent",
                [
                    name for name in lookup
                    if name != own_name
                ]
            )

        if st.button(
            "Load Historical Rosters",
            type="primary"
        ):

            with st.status(
                "Preparing historical matchup...",
                expanded=True
            ) as loading:

                bar = st.progress(0)

                st.write(
                    "📋 Loading starting pitchers "
                    "and bullpen..."
                )

                staff = load_team(
                    lookup[own_name],
                    year,
                    "pitchers"
                )

                bar.progress(50)

                st.write(
                    "🏏 Building opposing lineup "
                    "and bench..."
                )

                hitters = load_team(
                    lookup[opponent_name],
                    year,
                    "hitters"
                )

                bar.progress(90)

                st.write(
                    "🧢 Preparing dugout..."
                )

                if not staff or len(hitters) < 9:
                    st.error(
                        "Not enough historical data "
                        "was returned. Try a different "
                        "team or season."
                    )
                else:
                    st.session_state.loaded = {
                        "year": year,
                        "team": own_name,
                        "opponent": opponent_name,
                        "staff": staff,
                        "hitters": hitters
                    }

                    st.session_state.game = None

                bar.progress(100)

                loading.update(
                    label="Roster loading complete!",
                    state="complete",
                    expanded=False
                )

    except requests.RequestException as error:
        st.error(
            "MLB's data service could not "
            "complete the request."
        )
        st.caption(str(error))


# =====================================
# STARTING PITCHER SELECTION
# =====================================

loaded = st.session_state.loaded

if loaded and st.session_state.game is None:

    staff = loaded["staff"]

    starters = [
        p for p in staff
        if p["role"] == "SP"
    ] or staff

    by_id = {
        p["id"]: p
        for p in staff
    }

    starter_id = st.selectbox(
        "Choose Your Starting Pitcher",
        [p["id"] for p in starters],
        format_func=lambda pid: (
            f"{by_id[pid]['name']} "
            f"({hand_label(by_id[pid]['throws'])}) "
            f"| ERA "
            f"{by_id[pid]['stats'].get('era', '—')}"
        )
    )

    if st.button(
        "⚾ PLAY BALL!",
        type="primary"
    ):

        st.session_state.game = new_game(
            loaded["year"],
            loaded["team"],
            loaded["opponent"],
            loaded["staff"],
            loaded["hitters"],
            starter_id
        )

        st.rerun()


# =====================================
# LIVE GAME DASHBOARD
# =====================================

if st.session_state.game is not None:

    game = st.session_state.game

    pitcher = current_pitcher(game)
    batter = current_batter(game)

    st.subheader(
        f"{game['year']} | "
        f"{game['team']} vs. "
        f"{game['opponent']}"
    )

    metrics = st.columns(5)

    labels = [
        "Inning",
        "Outs",
        "Runs Allowed",
        "Hits Allowed",
        "Pitch Count"
    ]

    values = [
        min(9, game["inning"]),
        game["outs"],
        game["runs"],
        game["hits"],
        game["pitches"][pitcher["id"]]
    ]

    for box, label, value in zip(
        metrics, labels, values
    ):
        box.metric(label, value)

    left, middle, right = st.columns(
        [1.2, 1.3, 1.2],
        gap="medium"
    )

    # =================================
    # LEFT: PITCHING STAFF
    # =================================

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
                "Throws": hand_label(p["throws"]),
                "ERA": p["stats"].get("era", "—"),
                "WHIP": p["stats"].get("whip", "—"),
                "Pitches": game["pitches"][pid],
                "Status": status
            })

        st.dataframe(
            pd.DataFrame(staff_rows),
            hide_index=True,
            use_container_width=True,
            height=270
        )

        st.write(
            f"**On the mound:** "
            f"{pitcher['name']} "
            f"({hand_label(pitcher['throws'])})"
        )

        st.progress(
            min(1.0, fatigue(game)),
            text=(
                f"Fatigue: {fatigue(game):.0%}"
            )
        )

        if not game["finished"]:

            st.markdown("#### 🔥 Bullpen")

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

                resting_lookup = {
                    p["id"]: p
                    for p in resting
                }

                option = st.selectbox(
                    "Select pitcher to warm up",
                    list(resting_lookup),
                    format_func=lambda pid: (
                        f"{resting_lookup[pid]['name']} "
                        f"({hand_label(resting_lookup[pid]['throws'])}) "
                        f"| ERA "
                        f"{resting_lookup[pid]['stats'].get('era', '—')}"
                    ),
                    key="warm_pick"
                )

                if st.button(
                    "🔥 WARM UP PITCHER"
                ):
                    warm_up(game, option)
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

                pick = st.selectbox(
                    "Ready to enter",
                    list(ready_lookup),
                    format_func=lambda pid: (
                        f"{ready_lookup[pid]['name']} "
                        f"({hand_label(ready_lookup[pid]['throws'])})"
                    )
                )

                reason = st.selectbox(
                    "Reason for change",
                    [
                        "Fatigue",
                        "Left/right matchup",
                        "Runners in scoring position",
                        "Strikeout ability",
                        "Protect the shutout"
                    ]
                )

                if st.button(
                    "🔁 BRING IN RELIEVER",
                    disabled=not can_change(game),
                    type="primary"
                ):
                    change_pitcher(
                        game, pick, reason
                    )
                    st.rerun()

            if not can_change(game):
                st.caption(
                    "Three-batter minimum or "
                    "finish the half-inning."
                )

    # =================================
    # CENTER: LIVE GAME
    # =================================

    with middle:

        st.markdown("### 🏟️ Live Diamond")

        draw_field(game)

        st.write(
            f"**At Bat:** {batter['name']} "
            f"({batter['bats']})"
        )

        st.write(
            f"**Pitching:** {pitcher['name']} "
            f"({hand_label(pitcher['throws'])})"
        )

        if game["finished"]:

            if game["runs"] == 0:
                st.success(
                    "🏆 SHUTOUT! "
                    "27 outs and zero runs!"
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

        st.markdown("#### Live Play-by-Play")

        for entry in reversed(game["log"][-6:]):
            st.caption(entry)

    # =================================
    # RIGHT: OPPOSING LINEUP
    # =================================

    with right:

        st.markdown("### 🧢 Opposing Lineup")

        current_spot = game["spot"] % 9

        lineup_rows = []

        for index, h in enumerate(game["lineup"]):

            if index == current_spot:
                status = "🔴 AT BAT"
            elif index == (current_spot + 1) % 9:
                status = "🟡 ON DECK"
            elif index == (current_spot + 2) % 9:
                status = "⚪ IN HOLE"
            else:
                status = ""

            lineup_rows.append({
                "#": index + 1,
                "Status": status,
                "Batter": h["name"],
                "Bats": h["bats"],
                "AVG": h["stats"].get("avg", "—")
            })

        st.dataframe(
            pd.DataFrame(lineup_rows),
            hide_index=True,
            use_container_width=True,
            height=270
        )

        # =============================
        # DANGER AND MATCHUP SCOUTING
        # =============================

        danger, message = danger_status(game)

        st.markdown("### 🧠 Situation Room")

        if danger:

            st.error(message)

            st.markdown(
                "#### 🚨 HEAD-TO-HEAD SCOUTING"
            )

            st.write(
                f"**{pitcher['name']}** "
                f"({hand_label(pitcher['throws'])}) "
                f"vs. **{batter['name']}** "
                f"({batter['bats']})"
            )

            with st.spinner(
                "Checking historical matchup..."
            ):

                h2h = get_head_to_head(
                    pitcher["id"],
                    batter["id"],
                    game["year"]
                )

            if h2h and (
                h2h["at_bats"] is not None
            ):

                h1, h2, h3 = st.columns(3)

                h1.metric(
                    "Hits",
                    h2h["hits"]
                    if h2h["hits"] is not None
                    else "—"
                )

                h2.metric(
                    "At-Bats",
                    h2h["at_bats"]
                )

                h3.metric(
                    "Strikeouts",
                    h2h["strikeouts"]
                    if h2h["strikeouts"] is not None
                    else "—"
                )

                if h2h["average"] is not None:
                    avg_text = h2h["average"]

                elif n(h2h["at_bats"]) > 0:
                    avg_text = (
                        f"{n(h2h['hits']) / n(h2h['at_bats']):.3f}"
                    )

                else:
                    avg_text = "N/A"

                st.metric(
                    "Historical Matchup AVG",
                    avg_text
                )

                if n(h2h["at_bats"]) < 20:
                    st.warning(
                        "Small sample! This matchup "
                        "may not be reliable enough "
                        "to predict the next at-bat."
                    )

                st.caption(
                    "MLB head-to-head data returned "
                    "for this player pairing. "
                    "Check sample size carefully."
                )

            else:

                st.info(
                    "No verified head-to-head "
                    "record was returned for "
                    "these two players."
                )

            st.markdown(
                "#### 📊 Season Comparison"
            )

            c1, c2 = st.columns(2)

            with c1:
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

            with c2:
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
                n(
                    pitcher["stats"].get(
                        "battersFaced"
                    )
                )
            )

            k_rate = (
                n(
                    pitcher["stats"].get(
                        "strikeOuts"
                    )
                ) / bf
            )

            bb_rate = (
                n(
                    pitcher["stats"].get(
                        "baseOnBalls"
                    )
                ) / bf
            )

            st.write(
                f"**Strikeout rate:** {pct(k_rate)}"
            )

            st.write(
                f"**Walk rate:** {pct(bb_rate)}"
            )

            st.markdown("#### ⚾ Manager's Decision")

            st.write(
                "Would you leave this pitcher "
                "in the game or bring in a "
                "reliever? Use the statistics "
                "above to decide."
            )

        else:

            st.success(
                "✅ No immediate danger."
            )

            st.caption(
                "Detailed matchup scouting "
                "will appear when the game "
                "enters a dangerous situation."
            )

            st.write(
                f"Current batter: **{batter['name']}**"
            )

            st.write(
                f"Season AVG: "
                f"**{batter['stats'].get('avg', '—')}**"
            )

    # =================================
    # MANAGER LOG / END OF GAME
    # =================================

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

    if st.button("Start a New Game"):
        st.session_state.game = None
        st.session_state.loaded = None
        st.rerun()

st.caption(
    "Historical MLB statistics support an "
    "educational simulation. Game outcomes, "
    "fatigue, and situational events are simulated. "
    "Head-to-head data is shown only when "
    "the API returns an identifiable matchup."
)
