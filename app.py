
import streamlit as st
import requests
import random
import pandas as pd

st.set_page_config(
    page_title="Shutout Challenge",
    page_icon="⚾",
    layout="wide"
)

API = "https://statsapi.mlb.com/api/v1"

st.markdown("""
<style>
.block-container {
    padding-top: 0.6rem;
    max-width: 1600px;
}
h1 {color: #F47721;}
[data-testid="stMetric"] {
    background: #182D3E;
    padding: 9px;
    border-radius: 9px;
}
[data-testid="stMetric"] label {
    color: #FFFFFF;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #FFFFFF;
}
</style>
""", unsafe_allow_html=True)


# ---------- MLB DATA ----------

@st.cache_data(ttl=86400)
def api_get(path, params=None):
    r = requests.get(
        f"{API}/{path}",
        params=params or {},
        timeout=25
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=86400)
def teams_for_year(year):
    data = api_get("teams", {
        "sportId": 1,
        "season": year
    })
    return sorted(
        data.get("teams", []),
        key=lambda x: x["name"]
    )


@st.cache_data(ttl=86400)
def roster(team_id, year):
    data = api_get(
        f"teams/{team_id}/roster",
        {
            "rosterType": "fullSeason",
            "season": year
        }
    )
    return data.get("roster", [])


@st.cache_data(ttl=86400)
def person_info(pid):
    data = api_get(f"people/{pid}")
    return (data.get("people") or [{}])[0]


@st.cache_data(ttl=86400)
def season_stats(pid, year, group):
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
            return split.get("stat", {})
    return {}


def num(value, default=0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


@st.cache_data(ttl=86400)
def load_team(team_id, year):
    entries = roster(team_id, year)
    players = []

    for item in entries:
        person = item.get("person", {})
        pid = person.get("id")
        if not pid:
            continue

        position = item.get("position", {})
        info = person_info(pid)

        pitch = season_stats(pid, year, "pitching")
        hit = season_stats(pid, year, "hitting")

        player = {
            "id": pid,
            "name": person.get("fullName", "Unknown"),
            "position": position.get("abbreviation", ""),
            "throws": info.get(
                "pitchHand", {}
            ).get("code", "?"),
            "bats": info.get(
                "batSide", {}
            ).get("code", "?"),
            "pitch": pitch,
            "hit": hit
        }

        if pitch or hit:
            players.append(player)

    return players


def pitchers_from(players):
    pitchers = [
        p for p in players
        if num(p["pitch"].get("inningsPitched")) >= 1
    ]

    for p in pitchers:
        stats = p["pitch"]
        gs = num(stats.get("gamesStarted"))
        games = num(stats.get("gamesPlayed"), 1)
        p["role"] = (
            "SP" if gs >= max(3, games * 0.35)
            else "RP"
        )

    return sorted(
        pitchers,
        key=lambda p: (
            p["role"] != "SP",
            -num(p["pitch"].get("inningsPitched"))
        )
    )


def hitters_from(players):
    hitters = [
        p for p in players
        if num(p["hit"].get("atBats")) >= 1
    ]
    return sorted(
        hitters,
        key=lambda p: -num(
            p["hit"].get("plateAppearances",
                         p["hit"].get("atBats"))
        )
    )


# ---------- GAME ENGINE ----------

def new_game(year, team, opponent,
             staff, hitters, starter_id):

    return {
        "year": year,
        "team": team,
        "opponent": opponent,
        "staff": staff,
        "lineup": hitters[:9].copy(),
        "bench": hitters[9:],
        "pitcher": starter_id,
        "used": [starter_id],
        "counts": {p["id"]: 0 for p in staff},
        "batters_faced": {
            p["id"]: 0 for p in staff
        },
        "inning_entered": 1,
        "inning": 1,
        "outs": 0,
        "runs": 0,
        "hits": 0,
        "walks": 0,
        "bases": [False, False, False],
        "spot": 0,
        "log": ["Play ball! The shutout begins."],
        "decisions": [],
        "finished": False
    }


def current_pitcher(game):
    return next(
        p for p in game["staff"]
        if p["id"] == game["pitcher"]
    )


def fatigue(game):
    p = current_pitcher(game)
    count = game["counts"][p["id"]]

    limit = 95 if p["role"] == "SP" else 23
    return clamp(count / limit, 0, 2)


def allowed_change(game):
    pitcher_id = game["pitcher"]
    faced = game["batters_faced"][pitcher_id]

    return (
        faced >= 3
        or game["inning"] > game["inning_entered"]
    )


def change_pitcher(game, pid, explanation):
    if not allowed_change(game):
        return False

    if pid in game["used"]:
        return False

    old_name = current_pitcher(game)["name"]
    chosen = next(
        p for p in game["staff"]
        if p["id"] == pid
    )

    game["pitcher"] = pid
    game["used"].append(pid)
    game["inning_entered"] = game["inning"]

    game["decisions"].append({
        "Inning": game["inning"],
        "Removed": old_name,
        "Brought In": chosen["name"],
        "Reason": explanation
    })

    game["log"].append(
        f"Pitching change: {chosen['name']} "
        f"replaces {old_name}."
    )

    return True


def computer_pinch_hit(game):
    if game["inning"] < 7:
        return None

    if not game["bench"]:
        return None

    spot = game["spot"] % 9
    current = game["lineup"][spot]

    current_avg = num(current["hit"].get("avg"), .250)

    candidates = sorted(
        game["bench"],
        key=lambda p: num(p["hit"].get("avg")),
        reverse=True
    )

    best = candidates[0]
    best_avg = num(best["hit"].get("avg"))

    if (
        current_avg < .235
        and best_avg > current_avg + .035
    ):
        game["lineup"][spot] = best
        game["bench"].remove(best)

        message = (
            f"PINCH HITTER! {best['name']} "
            f"replaces {current['name']}."
        )
        game["log"].append(message)
        return message

    return None


def advance_hit(game, bases_gained):
    runners = game["bases"]
    new_bases = [False, False, False]
    runs = 0

    for index in (2, 1, 0):
        if runners[index]:
            destination = index + bases_gained
            if destination >= 3:
                runs += 1
            else:
                new_bases[destination] = True

    if bases_gained >= 4:
        runs += 1
    else:
        new_bases[bases_gained - 1] = True

    game["bases"] = new_bases
    game["runs"] += runs
    return runs


def take_walk(game):
    first, second, third = game["bases"]
    runs = 1 if first and second and third else 0

    if first and second:
        third = True
    if first:
        second = True

    game["bases"] = [True, second, third]
    game["runs"] += runs
    game["walks"] += 1

    return runs


def simulate_at_bat(game):
    if game["finished"]:
        return

    computer_pinch_hit(game)

    hitter = game["lineup"][game["spot"] % 9]
    pitcher = current_pitcher(game)

    hs = hitter["hit"]
    ps = pitcher["pitch"]

    avg = num(hs.get("avg"), .250)
    obp = num(hs.get("obp"), .320)
    slg = num(hs.get("slg"), .400)

    bf = max(1, num(ps.get("battersFaced"), 300))
    k_rate = num(ps.get("strikeOuts")) / bf
    bb_rate = num(ps.get("baseOnBalls")) / bf

    era = num(ps.get("era"), 4.20)
    whip = num(ps.get("whip"), 1.30)

    tired = max(0, fatigue(game) - .75)

    # These are educational probability adjustments,
    # not calibrated MLB predictions.
    hand_adjust = 0
    if pitcher["throws"] == "L":
        if hitter["bats"] == "L":
            hand_adjust = -.020
        elif hitter["bats"] == "R":
            hand_adjust = .010

    hit_prob = clamp(
        avg + (whip - 1.3) * .055
        + (era - 4.2) * .008
        + tired * .065
        + hand_adjust,
        .075, .48
    )

    walk_prob = clamp(
        (bb_rate + max(0, obp - avg) * .12)
        / 1.12 + tired * .025,
        .025, .16
    )

    strikeout_prob = clamp(
        k_rate - tired * .035,
        .08, .40
    )

    pitches = random.randint(3, 8)
    game["counts"][pitcher["id"]] += pitches
    game["batters_faced"][pitcher["id"]] += 1

    roll = random.random()
    score = 0

    if roll < walk_prob:
        score = take_walk(game)
        outcome = "walks"

    elif roll < walk_prob + hit_prob:
        game["hits"] += 1

        power = clamp(slg - avg, .06, .38)
        hit_roll = random.random()

        if hit_roll < power * .24:
            bases = 4
            outcome = "hits a HOME RUN"
        elif hit_roll < power * .42:
            bases = 2
            outcome = "doubles"
        elif hit_roll < power * .45:
            bases = 3
            outcome = "triples"
        else:
            bases = 1
            outcome = "singles"

        score = advance_hit(game, bases)

    elif roll < (
        walk_prob + hit_prob + strikeout_prob
    ):
        game["outs"] += 1
        outcome = "strikes out"

    else:
        game["outs"] += 1
        outcome = "is retired on a ball in play"

    message = (
        f"Inning {game['inning']}: "
        f"{hitter['name']} {outcome} "
        f"against {pitcher['name']}."
    )

    if score:
        message += f" {score} run(s) score!"

    game["log"].append(message)
    game["spot"] += 1

    if game["outs"] >= 3:
        game["log"].append(
            f"End of inning {game['inning']}."
        )
        game["inning"] += 1
        game["outs"] = 0
        game["bases"] = [False, False, False]

    if game["inning"] > 9:
        game["finished"] = True


# ---------- BASEBALL FIELD ----------

def diamond(game):
    first, second, third = game["bases"]

    def base(x, y, occupied):
        color = "#FFB52E" if occupied else "white"
        return (
            f'<rect x="{x}" y="{y}" '
            f'width="19" height="19" '
            f'transform="rotate(45 {x+9.5} {y+9.5})" '
            f'fill="{color}" stroke="#172F27" '
            f'stroke-width="2"/>'
        )

    svg = f"""
    <svg viewBox="0 0 400 230"
      style="width:100%;max-height:240px">
      <rect width="400" height="230"
        rx="15" fill="#195239"/>
      <path d="M200 207 L70 102 L200 8
        L330 102 Z"
        fill="#B78A59" stroke="#F1D5AC"
        stroke-width="3"/>
      <path d="M200 188 L96 102 L200 27
        L304 102 Z"
        fill="#26754B"/>
      {base(295, 92, first)}
      {base(190, 17, second)}
      {base(85, 92, third)}
      {base(191, 196, False)}
      <circle cx="200" cy="106" r="10"
        fill="#D9BF8F"/>
      <text x="200" y="111"
        text-anchor="middle"
        font-size="12" fill="black">P</text>
      <text x="200" y="224"
        fill="white" text-anchor="middle"
        font-size="12">HOME PLATE</text>
    </svg>
    """
    st.markdown(svg, unsafe_allow_html=True)


# ---------- USER INTERFACE ----------

st.title("⚾ YOU'RE THE MANAGER")
st.caption(
    "SPORTS BY THE NUMBERS | "
    "MLB SHUTOUT CHALLENGE | 2000–2025"
)

if "game" not in st.session_state:
    st.session_state.game = None

with st.expander(
    "⚙️ Set Up a Historical Game",
    expanded=st.session_state.game is None
):
    year = st.selectbox(
        "Season", list(range(2025, 1999, -1))
    )

    try:
        teams = teams_for_year(year)
        names = {t["name"]: t["id"] for t in teams}

        c1, c2 = st.columns(2)

        with c1:
            team_name = st.selectbox(
                "Your Team", list(names)
            )

        with c2:
            opponent_name = st.selectbox(
                "Opponent",
                [x for x in names if x != team_name]
            )

        if st.button(
            "Load Historical Rosters",
            type="primary"
        ):
            with st.spinner(
                "Loading real MLB season data..."
            ):
                own = load_team(
                    names[team_name], year
                )
                opposing = load_team(
                    names[opponent_name], year
                )

            staff = pitchers_from(own)
            hitters = hitters_from(opposing)

            if not staff or len(hitters) < 9:
                st.error(
                    "Not enough season statistics were "
                    "returned. Try another matchup."
                )
            else:
                st.session_state.loaded = {
                    "year": year,
                    "team": team_name,
                    "opponent": opponent_name,
                    "staff": staff,
                    "hitters": hitters
                }
                st.success("Rosters loaded!")

    except requests.RequestException as error:
        st.error(
            "The MLB data connection failed. "
            "Please retry."
        )
        st.caption(str(error))


if "loaded" in st.session_state:
    loaded = st.session_state.loaded

    if st.session_state.game is None:
        staff = loaded["staff"]
        starters = [
            p for p in staff if p["role"] == "SP"
        ]

        if not starters:
            starters = staff

        starter_ids = [p["id"] for p in starters]
        lookup = {p["id"]: p for p in staff}

        starter_id = st.selectbox(
            "Choose Your Starting Pitcher",
            starter_ids,
            format_func=lambda pid: (
                f"{lookup[pid]['name']} | "
                f"ERA {lookup[pid]['pitch'].get('era','—')}"
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
                staff,
                loaded["hitters"],
                starter_id
            )
            st.rerun()


if st.session_state.game is not None:
    game = st.session_state.game
    pitcher = current_pitcher(game)
    hitter = game["lineup"][game["spot"] % 9]

    st.subheader(
        f"{game['year']} | {game['team']} "
        f"vs. {game['opponent']}"
    )

    a, b, c, d, e = st.columns(5)
    a.metric("Inning", min(game["inning"], 9))
    b.metric("Outs", game["outs"])
    c.metric("Runs Allowed", game["runs"])
    d.metric("Hits Allowed", game["hits"])
    e.metric(
        "Pitch Count",
        game["counts"][pitcher["id"]]
    )

    left, middle, right = st.columns(
        [1.15, 1.4, 1.15],
        gap="medium"
    )

    with left:
        st.markdown("### ⚾ Pitching Staff")

        staff_rows = []
        for p in game["staff"]:
            ps = p["pitch"]
            count = game["counts"][p["id"]]
            staff_rows.append({
                "Pitcher": p["name"],
                "Role": p["role"],
                "ERA": ps.get("era", "—"),
                "WHIP": ps.get("whip", "—"),
                "Pitches": count,
                "Status": (
                    "ON MOUND"
                    if p["id"] == game["pitcher"]
                    else "Used"
                    if p["id"] in game["used"]
                    else "Ready"
                )
            })

        frame = pd.DataFrame(staff_rows)

        st.dataframe(
            frame,
            hide_index=True,
            use_container_width=True,
            height=270
        )

        st.write(
            f"**Current:** {pitcher['name']} "
            f"({pitcher['role']})"
        )

        condition = fatigue(game)
        st.progress(
            min(1.0, condition),
            text=f"Fatigue: {condition*100:.0f}%"
        )

        if not game["finished"]:
            available = [
                p for p in game["staff"]
                if p["id"] not in game["used"]
            ]

            if available:
                options = {
                    p["name"]: p["id"]
                    for p in available
                }

                selected = st.selectbox(
                    "Warm up a pitcher",
                    list(options)
                )

                reason = st.selectbox(
                    "Why make this change?",
                    [
                        "Current pitcher is tired",
                        "Better left/right matchup",
                        "Better statistical performance",
                        "Protect the shutout"
                    ]
                )

                can_change = allowed_change(game)

                if not can_change:
                    st.caption(
                        "Pitcher must face three batters "
                        "or finish the half-inning."
                    )

                if st.button(
                    "Make Pitching Change",
                    disabled=not can_change
                ):
                    change_pitcher(
                        game, options[selected], reason
                    )
                    st.rerun()

    with middle:
        st.markdown("### 🏟️ Live Diamond")
        diamond(game)

        st.write(
            f"**At Bat:** {hitter['name']} "
            f"({hitter['bats']})"
        )

        st.write(
            f"**Pitching:** {pitcher['name']} "
            f"({pitcher['throws']})"
        )

        if game["finished"]:
            if game["runs"] == 0:
                st.success(
                    "🏆 SHUTOUT! You recorded 27 outs "
                    "without allowing a run!"
                )
            else:
                st.info(
                    f"Final: {game['runs']} runs allowed."
                )

        elif st.button(
            "⚾ PITCH TO BATTER",
            type="primary",
            use_container_width=True
        ):
            simulate_at_bat(game)
            st.rerun()

        st.markdown("#### Live Play-by-Play")
        for entry in reversed(game["log"][-5:]):
            st.caption(entry)

    with right:
        st.markdown("### 🧢 Opposing Lineup")

        lineup_rows = []
        for index, h in enumerate(game["lineup"]):
            lineup_rows.append({
                "#": index + 1,
                "Batter": h["name"],
                "Bats": h["bats"],
                "AVG": h["hit"].get("avg", "—")
            })

        st.dataframe(
            pd.DataFrame(lineup_rows),
            hide_index=True,
            use_container_width=True,
            height=270
        )

        st.markdown("### 📊 Matchup Scouting")

        st.write(
            f"**{pitcher['name']}** vs. "
            f"**{hitter['name']}**"
        )

        st.write(
            "Batter season average:",
            hitter["hit"].get("avg", "N/A")
        )

        st.write(
            "Pitcher ERA:",
            pitcher["pitch"].get("era", "N/A")
        )

        st.write(
            "Pitcher WHIP:",
            pitcher["pitch"].get("whip", "N/A")
        )

        st.caption(
            "Verified individual head-to-head "
            "history is not yet connected. "
            "These are season statistics."
        )

    with st.expander("📋 Manager Decision Log"):
        if game["decisions"]:
            st.dataframe(
                pd.DataFrame(game["decisions"]),
                hide_index=True
            )
        else:
            st.write("No pitching changes yet.")

    if game["finished"]:
        st.download_button(
            "Download Manager Report",
            data=pd.DataFrame(
                game["decisions"]
            ).to_csv(index=False),
            file_name="manager_report.csv",
            mime="text/csv"
        )

    if st.button("Start a New Game"):
        st.session_state.game = None
        st.session_state.pop("loaded", None)
        st.rerun()

st.caption(
    "Real MLB statistics support an educational "
    "simulation. At-bat results, fatigue, and "
    "game situations are simulated."
)
