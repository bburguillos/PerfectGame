
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="You're the Manager",
    page_icon="⚾",
    layout="wide"
)

st.markdown("""
<style>
.block-container {
    max-width: 1750px;
    padding-top: .5rem;
}
h1 {color: #ee7624;}
div[data-testid='stMetric'] {
    background: #183849;
    padding: 9px;
    border-radius: 9px;
}
div[data-testid='stMetric'] * {
    color: white !important;
}
</style>
""", unsafe_allow_html=True)

API = "https://statsapi.mlb.com/api/v1"


# =====================================
# HELPERS
# =====================================

def val(x, default=0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def bound(x, low, high):
    return max(low, min(high, x))


def hand(x):
    return {
        "L": "LHP",
        "R": "RHP"
    }.get(x, "?HP")


def fmt(x):
    return "—" if x is None else str(x)


# =====================================
# MLB DATA
# =====================================

@st.cache_data(ttl=86400, show_spinner=False)
def api(path, params=None):
    r = requests.get(
        f"{API}/{path}",
        params=params or {},
        timeout=16
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=86400, show_spinner=False)
def teams(year):
    return sorted(
        api(
            "teams",
            {"sportId": 1, "season": year}
        ).get("teams", []),
        key=lambda t: t["name"]
    )


@st.cache_data(ttl=86400, show_spinner=False)
def roster(team_id, year):
    return api(
        f"teams/{team_id}/roster",
        {
            "rosterType": "fullSeason",
            "season": year
        }
    ).get("roster", [])


@st.cache_data(ttl=86400, show_spinner=False)
def details(pid, year, group):
    data = api(
        f"people/{pid}",
        {
            "hydrate": (
                f"stats(group=[{group}],"
                f"type=[season],season={year})"
            )
        }
    )

    p = (data.get("people") or [{}])[0]
    stat = {}

    for section in p.get("stats", []):
        for split in section.get("splits", []):
            if split.get("stat"):
                stat = split["stat"]
                break

    if not stat:
        d = api(
            f"people/{pid}/stats",
            {
                "stats": "season",
                "group": group,
                "season": year,
                "gameType": "R"
            }
        )

        for section in d.get("stats", []):
            for split in section.get("splits", []):
                if split.get("stat"):
                    stat = split["stat"]
                    break

    return {
        "id": pid,
        "name": p.get("fullName", "Unknown"),
        "throws": p.get(
            "pitchHand", {}
        ).get("code", "?"),
        "bats": p.get(
            "batSide", {}
        ).get("code", "?"),
        "stats": stat,
        "position": p.get(
            "primaryPosition", {}
        ).get("abbreviation", "")
    }


@st.cache_data(ttl=86400, show_spinner=False)
def squad(team_id, year, group):
    entries = roster(team_id, year)
    ids = []

    for e in entries:
        pos = e.get(
            "position", {}
        ).get("abbreviation")

        if (group == "pitching") == (
            pos in ("P", "TWP")
        ):
            pid = e.get("person", {}).get("id")
            if pid:
                ids.append(pid)

    players = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(
                details, pid, year, group
            ): pid
            for pid in ids
        }

        for future in as_completed(futures):
            try:
                p = future.result()
                s = p["stats"]

                if (
                    group == "pitching"
                    and val(s.get("gamesPlayed")) > 0
                ):
                    gs = val(s.get("gamesStarted"))
                    gp = val(s.get("gamesPlayed"))

                    p["role"] = (
                        "SP"
                        if gs >= max(3, gp * .35)
                        else "RP"
                    )

                    p["relief_apps"] = max(
                        0, gp - gs
                    )

                    players.append(p)

                elif (
                    group == "hitting"
                    and val(s.get("atBats")) > 0
                ):
                    players.append(p)

            except (
                requests.RequestException,
                ValueError
            ):
                pass

    if group == "pitching":
        starters = sorted(
            (
                p for p in players
                if p["role"] == "SP"
            ),
            key=lambda p: -val(
                p["stats"].get("gamesStarted")
            )
        )[:5]

        relief = sorted(
            (
                p for p in players
                if p["role"] == "RP"
            ),
            key=lambda p: -p["relief_apps"]
        )[:8]

        return starters + relief

    return sorted(
        players,
        key=lambda p: -val(
            p["stats"].get(
                "plateAppearances",
                val(p["stats"].get("atBats"))
            )
        )
    )[:13]


# =====================================
# HEAD TO HEAD
# =====================================

@st.cache_data(ttl=86400, show_spinner=False)
def matchup(pid, bid, year):
    try:
        data = api(
            f"people/{pid}/stats",
            {
                "stats": "vsPlayer",
                "group": "pitching",
                "season": year,
                "opposingPlayerId": bid,
                "gameType": "R"
            }
        )

        for section in data.get("stats", []):
            for split in section.get("splits", []):
                opponent = (
                    split.get("opponent", {})
                    or split.get("player", {})
                )

                if (
                    str(opponent.get("id")) == str(bid)
                    and split.get("stat")
                ):
                    return split["stat"]

    except (
        requests.RequestException,
        ValueError
    ):
        pass

    return None


# =====================================
# GAME STATE
# =====================================

def new_game(package, starter, mode):
    staff = package["staff"]
    lineup = package["hitters"][:9]

    return {
        "year": package["year"],
        "team": package["team"],
        "opponent": package["opponent"],
        "staff": staff,
        "lineup": lineup.copy(),
        "bench": package["hitters"][9:].copy(),
        "pitcher": starter,
        "used": [starter],
        "pitches": {
            p["id"]: 0 for p in staff
        },
        "faced": {
            p["id"]: 0 for p in staff
        },
        "appearance": 0,
        "entry_inning": 1,
        "warming": {},
        "ready": [],
        "inning": 1,
        "outs": 0,
        "runs": 0,
        "hits": 0,
        "walks": 0,
        "bases": [False] * 3,
        "spot": 0,
        "finished": False,
        "mode": mode,
        "log": [
            "PLAY BALL! Defend the shutout."
        ],
        "decisions": [],
        "strikeouts": 0,
        "double_plays": 0,
        "score": 100,
        "last_play": "Game begins!"
    }


def pitcher(g):
    return next(
        p for p in g["staff"]
        if p["id"] == g["pitcher"]
    )


def batter(g):
    return g["lineup"][g["spot"] % 9]


# =====================================
# STAMINA
# =====================================

def stamina(g):
    p = pitcher(g)
    s = p["stats"]

    gs = val(s.get("gamesStarted"))
    ip = val(s.get("inningsPitched"))

    average_ip = (
        ip / max(1, gs)
        if gs else 0
    )

    if p["role"] == "SP":
        limit = bound(
            average_ip * 18
            if average_ip else 105,
            85,
            140
        )
    else:
        relief_games = max(
            1,
            val(s.get("gamesPlayed")) - gs
        )

        limit = bound(
            ip / relief_games * 23,
            20,
            48
        )

    if g["mode"] == "Rookie":
        limit *= 1.35

    elif g["mode"] == "Hall of Fame":
        limit *= .90

    return g["pitches"][p["id"]] / limit


def change_legal(g):
    return (
        not g["finished"]
        and (
            g["appearance"] >= 3
            or g["inning"] > g["entry_inning"]
        )
    )


# =====================================
# BULLPEN
# =====================================

def warm(g, pid):
    if (
        pid in g["used"]
        or pid in g["ready"]
        or pid in g["warming"]
    ):
        return

    g["warming"][pid] = 0

    p = next(
        p for p in g["staff"]
        if p["id"] == pid
    )

    g["log"].append(
        f"🔥 {p['name']} "
        f"({hand(p['throws'])}) begins warming."
    )


def warm_tick(g):
    for pid in list(g["warming"]):
        g["warming"][pid] += 1

        if g["warming"][pid] >= 2:
            del g["warming"][pid]
            g["ready"].append(pid)

            p = next(
                p for p in g["staff"]
                if p["id"] == pid
            )

            g["log"].append(
                f"✅ {p['name']} is ready."
            )


def bring_in(g, pid, reason):
    if (
        not change_legal(g)
        or pid not in g["ready"]
    ):
        return

    old = pitcher(g)
    new = next(
        p for p in g["staff"]
        if p["id"] == pid
    )

    prior_fatigue = stamina(g)

    if prior_fatigue > 1.1:
        g["score"] -= 8

    if prior_fatigue > .75:
        g["score"] += 3

    if old["throws"] != new["throws"]:
        g["score"] += 2

    g["pitcher"] = pid
    g["used"].append(pid)
    g["ready"].remove(pid)

    g["appearance"] = 0
    g["entry_inning"] = g["inning"]

    g["decisions"].append({
        "Inning": g["inning"],
        "Outs": g["outs"],
        "Pitcher Out": old["name"],
        "Pitcher In": new["name"],
        "Throws": hand(new["throws"]),
        "Reason": reason
    })

    g["log"].append(
        f"🔁 {new['name']} replaces "
        f"{old['name']} ({reason})."
    )


# =====================================
# COMPUTER MANAGER
# =====================================

def pinch_hit(g):
    if g["inning"] < 7 or not g["bench"]:
        return

    ix = g["spot"] % 9
    old = g["lineup"][ix]
    p = pitcher(g)

    if not (
        sum(g["bases"])
        or g["inning"] >= 8
    ):
        return

    def matchup_score(h):
        avg = val(
            h["stats"].get("avg"), .240
        )

        hhand = h["bats"]

        penalty = (
            .02
            if hhand == p["throws"]
            and hhand in ("L", "R")
            else 0
        )

        return avg - penalty

    replacement = max(
        g["bench"],
        key=matchup_score
    )

    if matchup_score(replacement) > (
        matchup_score(old) + .035
    ):
        g["lineup"][ix] = replacement
        g["bench"].remove(replacement)

        g["log"].append(
            f"📣 PINCH HITTER: "
            f"{replacement['name']} replaces "
            f"{old['name']}!"
        )


# =====================================
# BASERUNNING
# =====================================

def hit_bases(g, distance):
    old = g["bases"]
    new = [False] * 3
    runs = 0

    if distance == 4:
        runs = 1 + sum(old)

    else:
        for i in (2, 1, 0):
            if not old[i]:
                continue

            advance = distance

            if (
                distance == 1
                and i == 1
                and random.random() < (
                    .72 if g["outs"] == 2
                    else .50
                )
            ):
                advance = 2

            if (
                distance == 1
                and i == 0
                and random.random() < .23
            ):
                advance = 2

            destination = i + advance

            if destination >= 3:
                runs += 1

            elif new[destination]:
                free = next(
                    (
                        j for j in range(
                            destination + 1, 3
                        )
                        if not new[j]
                    ),
                    None
                )

                if free is None:
                    runs += 1
                else:
                    new[free] = True

            else:
                new[destination] = True

        new[distance - 1] = True

    g["bases"] = new
    g["runs"] += runs

    return runs


def walk_bases(g):
    a, b, c = g["bases"]

    score = int(a and b and c)

    g["bases"] = [
        True,
        a or b,
        c or (a and b)
    ]

    g["walks"] += 1
    g["runs"] += score

    return score


# =====================================
# SIMULATION ENGINE
# =====================================

def simulate(g):
    if g["finished"]:
        return

    pinch_hit(g)

    p = pitcher(g)
    h = batter(g)

    ps = p["stats"]
    hs = h["stats"]

    ab = max(
        1,
        val(hs.get("atBats"), 1)
    )

    hits = val(hs.get("hits"))

    avg = bound(
        hits / ab
        if hits
        else val(hs.get("avg"), .250),
        .08,
        .45
    )

    doubles = val(hs.get("doubles"))
    triples = val(hs.get("triples"))
    homers = val(hs.get("homeRuns"))

    singles = max(
        0,
        hits - doubles - triples - homers
    )

    if (
        singles + doubles + triples + homers
    ) <= 0:
        singles, doubles, triples, homers = (
            70, 19, 2, 9
        )

    bf = max(
        1,
        val(ps.get("battersFaced"), 1)
    )

    k_rate = bound(
        val(ps.get("strikeOuts")) / bf,
        .06,
        .38
    )

    bb_rate = bound(
        val(ps.get("baseOnBalls")) / bf,
        .025,
        .17
    )

    fatigued = max(
        0,
        stamina(g) - .65
    )

    same = (
        h["bats"] == p["throws"]
        and h["bats"] in ("L", "R")
    )

    hand_adjust = (
        -.017 if same else .010
    )

    whip_adjust = (
        val(ps.get("whip"), 1.3) - 1.3
    ) * .045

    era_adjust = (
        val(ps.get("era"), 4.2) - 4.2
    ) * .006

    hit_prob = bound(
        avg
        + hand_adjust
        + whip_adjust
        + era_adjust
        + fatigued * .065,
        .08,
        .48
    )

    walk_prob = bound(
        bb_rate + fatigued * .03,
        .025,
        .18
    )

    strike_prob = bound(
        k_rate - fatigued * .03,
        .06,
        .40
    )

    out_prob = max(
        .05,
        1 - hit_prob - walk_prob
    )

    strike_prob = min(
        strike_prob,
        out_prob
    )

    roll = random.random()

    scores = 0
    result = ""

    if roll < walk_prob:
        scores = walk_bases(g)
        result = "walks"

    elif roll < walk_prob + hit_prob:
        distance = random.choices(
            [1, 2, 3, 4],
            weights=[
                singles,
                doubles,
                triples,
                homers
            ],
            k=1
        )[0]

        scores = hit_bases(g, distance)
        g["hits"] += 1

        result = {
            1: "singles",
            2: "doubles",
            3: "TRIPLES",
            4: "HOMERS"
        }[distance]

    elif roll < (
        walk_prob + hit_prob + strike_prob
    ):
        g["outs"] += 1
        g["strikeouts"] += 1
        result = "strikes out"

    else:
        first = g["bases"][0]
        third = g["bases"][2]

        if (
            first
            and g["outs"] < 2
            and random.random() < .13
        ):
            g["outs"] += 2
            g["double_plays"] += 1
            g["bases"][0] = False

            result = (
                "grounds into a DOUBLE PLAY"
            )

        elif (
            third
            and g["outs"] < 2
            and random.random() < .17
        ):
            g["outs"] += 1
            g["bases"][2] = False
            g["runs"] += 1

            scores = 1
            result = "hits a sacrifice fly"

        else:
            g["outs"] += 1
            result = (
                "is retired on a ball in play"
            )

    count = random.randint(3, 8)

    g["pitches"][p["id"]] += count
    g["faced"][p["id"]] += 1
    g["appearance"] += 1

    note = (
        f"Inning {g['inning']}: "
        f"{h['name']} {result} "
        f"vs. {p['name']} "
        f"({count} pitches)."
    )

    if scores:
        note += (
            f" {scores} run(s) score!"
        )
        g["score"] -= scores * 12

    g["last_play"] = note
    g["log"].append(note)

    g["spot"] += 1
    warm_tick(g)

    if g["outs"] >= 3:
        g["log"].append(
            f"End of inning {g['inning']} — "
            f"{g['runs']} runs allowed."
        )

        g["outs"] = 0
        g["bases"] = [False] * 3
        g["inning"] += 1

    if g["inning"] > 9:
        g["finished"] = True

        g["log"].append(
            "🏆 SHUTOUT!"
            if g["runs"] == 0
            else "Game complete."
        )


# =====================================
# DANGER DETECTION
# =====================================

def danger(g):
    if g["finished"]:
        return None

    if all(g["bases"]):
        return "🚨 BASES LOADED"

    if (
        g["bases"][1]
        or g["bases"][2]
    ):
        return (
            "⚠️ RUNNER IN SCORING POSITION"
        )

    if (
        g["inning"] >= 7
        and any(g["bases"])
    ):
        return "🔥 LATE-INNING PRESSURE"

    if stamina(g) > .9:
        return "🔋 FATIGUE WARNING"

    return None


# =====================================
# LIVE DIAMOND
# =====================================

def diamond(g):
    def base(x, y, on):
        color = (
            "#FFB93A" if on else "white"
        )

        return (
            f'<rect x="{x-9}" y="{y-9}" '
            f'width="18" height="18" '
            f'transform="rotate(45 {x} {y})" '
            f'fill="{color}" '
            f'stroke="#174832" '
            f'stroke-width="2"/>'
        )

    a, b, c = g["bases"]

    svg = f"""
    <html>
    <body style="margin:0;background:transparent">
    <svg xmlns="http://www.w3.org/2000/svg"
         viewBox="0 0 400 245"
         style="width:100%;height:240px">

      <rect width="400" height="245"
            rx="13" fill="#16563D"/>

      <path d="M200 222 L63 112
               L200 12 L337 112 Z"
            fill="#BA8C58"
            stroke="#F0DDB4"
            stroke-width="3"/>

      <path d="M200 200 L91 112
               L200 34 L309 112 Z"
            fill="#267950"/>

      <path d="M200 222 L20 76
               M200 222 L380 76"
            stroke="white"
            stroke-width="2"/>

      {base(302,112,a)}
      {base(200,29,b)}
      {base(98,112,c)}
      {base(200,216,False)}

      <circle cx="200" cy="124"
              r="12" fill="#D7B27A"/>

      <rect x="193" y="122"
            width="14" height="4"
            fill="white"/>

      <text x="12" y="19"
            font-size="11"
            fill="white">
        Occupied bases shown in yellow
      </text>

    </svg>
    </body>
    </html>
    """

    components.html(
        svg,
        height=247,
        scrolling=False
    )


# =====================================
# APP HEADER
# =====================================

st.title(
    "⚾ YOU'RE THE MANAGER: SHUTOUT CHALLENGE"
)

st.caption(
    "SPORTS BY THE NUMBERS • 2000–2025 MLB • "
    "Real historical statistics / simulated outcomes"
)

if "game" not in st.session_state:
    st.session_state.game = None

if "loaded" not in st.session_state:
    st.session_state.loaded = None


# =====================================
# TEAM SELECTION
# =====================================

with st.expander(
    "⚙️ Select season and teams",
    expanded=st.session_state.game is None
):

    year = st.selectbox(
        "Season",
        list(range(2025, 1999, -1))
    )

    try:
        teamlist = teams(year)

        names = {
            t["name"]: t["id"]
            for t in teamlist
        }

        c1, c2 = st.columns(2)

        own = c1.selectbox(
            "Your pitching team",
            list(names)
        )

        opp = c2.selectbox(
            "Opposing batting team",
            [
                x for x in names
                if x != own
            ]
        )

        if st.button(
            "LOAD MLB ROSTERS",
            type="primary"
        ):

            with st.status(
                "Preparing historical matchup...",
                expanded=True
            ) as box:

                bar = st.progress(0)

                st.write(
                    "⚾ Loading starting pitchers "
                    "and bullpen..."
                )

                staff = squad(
                    names[own],
                    year,
                    "pitching"
                )

                bar.progress(50)

                st.write(
                    "🏏 Loading opposing hitters "
                    "and bench..."
                )

                hitters = squad(
                    names[opp],
                    year,
                    "hitting"
                )

                bar.progress(90)

                if staff and len(hitters) >= 9:
                    st.session_state.loaded = {
                        "year": year,
                        "team": own,
                        "opponent": opp,
                        "staff": staff,
                        "hitters": hitters
                    }

                    st.session_state.game = None
                    st.write("✅ Rosters ready")

                else:
                    st.session_state.loaded = None

                    st.error(
                        "Not enough roster data returned. "
                        "Try another season or matchup."
                    )

                bar.progress(100)

                box.update(
                    label="Roster request finished",
                    state="complete",
                    expanded=False
                )

    except requests.RequestException as exc:
        st.error(
            "MLB data could not be loaded. "
            "Refresh and try again."
        )
        st.caption(str(exc))


# =====================================
# START GAME
# =====================================

loaded = st.session_state.loaded

if loaded and st.session_state.game is None:

    staff = loaded["staff"]

    starters = [
        p for p in staff
        if p["role"] == "SP"
    ] or staff

    lookup = {
        p["id"]: p
        for p in starters
    }

    c1, c2 = st.columns(2)

    starter = c1.selectbox(
        "Starting pitcher",
        [p["id"] for p in starters],
        format_func=lambda pid: (
            f"{lookup[pid]['name']} "
            f"({hand(lookup[pid]['throws'])}) • "
            f"ERA "
            f"{lookup[pid]['stats'].get('era','—')}"
        )
    )

    mode = c2.selectbox(
        "Difficulty",
        [
            "Rookie",
            "Pro",
            "Hall of Fame"
        ],
        index=0
    )

    if st.button(
        "⚾ PLAY BALL!",
        type="primary"
    ):
        st.session_state.game = new_game(
            loaded,
            starter,
            mode
        )
        st.rerun()


# =====================================
# GAME DASHBOARD
# =====================================

g = st.session_state.game

if g:
    p = pitcher(g)
    h = batter(g)

    st.subheader(
        f"{g['year']} {g['team']} "
        f"vs. {g['opponent']} • {g['mode']}"
    )

    cells = st.columns(6)

    labels = [
        "Inning",
        "Outs",
        "Runs Allowed",
        "Hits",
        "Pitch Count",
        "Strikeouts"
    ]

    numbers = [
        min(9, g["inning"]),
        g["outs"],
        g["runs"],
        g["hits"],
        g["pitches"][p["id"]],
        g["strikeouts"]
    ]

    for col, label, number in zip(
        cells, labels, numbers
    ):
        col.metric(label, number)

    left, center, right = st.columns(
        [1.15, 1.32, 1.15],
        gap="medium"
    )

    # =================================
    # PITCHING STAFF
    # =================================

    with left:
        st.markdown("### ⚾ Pitching staff")

        rows = []

        for x in g["staff"]:
            pid = x["id"]

            if pid == g["pitcher"]:
                status = "🔵 MOUND"

            elif pid in g["used"]:
                status = "USED"

            elif pid in g["ready"]:
                status = "🟢 READY"

            elif pid in g["warming"]:
                status = "🟡 WARM"

            else:
                status = "AVAILABLE"

            rows.append({
                "Pitcher": x["name"],
                "Role": x["role"],
                "Hand": hand(x["throws"]),
                "ERA": x["stats"].get(
                    "era", "—"
                ),
                "WHIP": x["stats"].get(
                    "whip", "—"
                ),
                "Status": status
            })

        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            use_container_width=True,
            height=260
        )

        st.write(
            f"**On mound:** {p['name']} "
            f"({hand(p['throws'])})"
        )

        st.progress(
            bound(stamina(g), 0, 1),
            text=f"Fatigue: {stamina(g):.0%}"
        )

        st.caption(
            "Stamina uses historical innings "
            "per appearance. Rookie mode gives "
            "extra endurance."
        )

        if not g["finished"]:
            st.markdown("#### 🔥 Bullpen")

            available = [
                x for x in g["staff"]
                if x["id"] not in g["used"]
            ]

            cold = [
                x for x in available
                if x["id"] not in g["warming"]
                and x["id"] not in g["ready"]
            ]

            if cold:
                byid = {
                    x["id"]: x
                    for x in cold
                }

                pick = st.selectbox(
                    "Warm up a pitcher",
                    list(byid),
                    format_func=lambda pid: (
                        f"{byid[pid]['name']} • "
                        f"{hand(byid[pid]['throws'])}"
                    )
                )

                if st.button("🔥 WARM UP"):
                    warm(g, pick)
                    st.rerun()

            for pid, progress in g["warming"].items():
                x = next(
                    x for x in g["staff"]
                    if x["id"] == pid
                )

                st.caption(
                    f"🟡 {x['name']} "
                    f"({hand(x['throws'])}) — "
                    f"{progress}/2 batters"
                )

            ready = [
                x for x in available
                if x["id"] in g["ready"]
            ]

            if ready:
                byid = {
                    x["id"]: x
                    for x in ready
                }

                incoming = st.selectbox(
                    "Ready reliever",
                    list(byid),
                    format_func=lambda pid: (
                        f"{byid[pid]['name']} • "
                        f"{hand(byid[pid]['throws'])}"
                    )
                )

                reason = st.selectbox(
                    "Your reason",
                    [
                        "Fatigue",
                        "Handedness matchup",
                        "Strikeout ability",
                        "Runners on base",
                        "Save the shutout"
                    ]
                )

                if st.button(
                    "🔁 MAKE CHANGE",
                    disabled=not change_legal(g),
                    type="primary"
                ):
                    bring_in(
                        g,
                        incoming,
                        reason
                    )
                    st.rerun()

            if not change_legal(g):
                st.caption(
                    "Three-batter minimum, "
                    "or finish the half-inning."
                )

    # =================================
    # LIVE GAME
    # =================================

    with center:
        st.markdown("### 🏟️ Live diamond")

        diamond(g)

        st.write(
            f"**At bat:** {h['name']} ({h['bats']}) "
            f" | **Pitcher:** {p['name']} "
            f"({hand(p['throws'])})"
        )

        if g["finished"]:
            if g["runs"] == 0:
                st.success(
                    "🏆 SHUTOUT! "
                    "27 outs, zero runs!"
                )
            else:
                st.info(
                    f"Final: {g['runs']} runs allowed. "
                    "You still completed the challenge!"
                )

        elif st.button(
            "⚾ PITCH TO BATTER",
            type="primary",
            use_container_width=True
        ):
            simulate(g)
            st.rerun()

        st.markdown("#### 📣 Play-by-play")
        st.info(g["last_play"])

        for line in reversed(g["log"][-5:]):
            st.caption(line)

    # =================================
    # OPPOSING LINEUP AND SCOUTING
    # =================================

    with right:
        st.markdown("### 🧢 Opposing lineup")

        spot = g["spot"] % 9
        table = []

        for i, x in enumerate(g["lineup"]):

            if i == spot:
                label = "🔴 AT BAT"

            elif i == (spot + 1) % 9:
                label = "🟡 ON DECK"

            elif i == (spot + 2) % 9:
                label = "⚪ NEXT"

            else:
                label = ""

            table.append({
                "#": i + 1,
                "Status": label,
                "Batter": x["name"],
                "Bats": x["bats"],
                "AVG": x["stats"].get(
                    "avg", "—"
                )
            })

        st.dataframe(
            pd.DataFrame(table),
            hide_index=True,
            use_container_width=True,
            height=260
        )

        st.markdown("### 🧠 Situation room")

        alert = danger(g)

        if alert:
            st.error(alert)

            st.write(
                f"**{p['name']} vs. {h['name']}**"
            )

            if g["mode"] != "Hall of Fame":
                h2h = matchup(
                    p["id"],
                    h["id"],
                    g["year"]
                )

                if (
                    h2h
                    and h2h.get("atBats") is not None
                ):
                    st.write(
                        f"Verified matchup: "
                        f"**{fmt(h2h.get('hits'))} H / "
                        f"{fmt(h2h.get('atBats'))} AB** "
                        f"• AVG "
                        f"**{fmt(h2h.get('avg'))}** "
                        f"• K "
                        f"**{fmt(h2h.get('strikeOuts'))}**"
                    )

                    if val(
                        h2h.get("atBats")
                    ) < 20:
                        st.warning(
                            "Small sample: be careful "
                            "about conclusions."
                        )

                else:
                    st.caption(
                        "Verified pitcher-batter "
                        "record unavailable; season "
                        "totals shown below."
                    )

            st.write(
                f"Batter: AVG "
                f"**{h['stats'].get('avg','—')}** "
                f"• OBP "
                f"**{h['stats'].get('obp','—')}** "
                f"• HR "
                f"**{h['stats'].get('homeRuns','—')}**"
            )

            st.write(
                f"Pitcher: ERA "
                f"**{p['stats'].get('era','—')}** "
                f"• WHIP "
                f"**{p['stats'].get('whip','—')}** "
                f"• K "
                f"**{p['stats'].get('strikeOuts','—')}**"
            )

            if g["mode"] == "Rookie":
                st.info(
                    "Consider pitcher fatigue, "
                    "hitter handedness, and how "
                    "many runners could score."
                )

        else:
            st.success("No immediate danger")

            st.caption(
                f"Current batter season AVG: "
                f"{h['stats'].get('avg','—')}"
            )

    # =================================
    # POSTGAME ANALYSIS
    # =================================

    with st.expander(
        "📋 Decisions & learning report"
    ):
        st.metric(
            "Manager score (approx.)",
            bound(g["score"], 0, 100)
        )

        st.caption(
            "Teaching score: rewards some timely "
            "changes and deducts for runs allowed. "
            "Not an objective MLB management rating."
        )

        if g["decisions"]:
            st.dataframe(
                pd.DataFrame(g["decisions"]),
                hide_index=True
            )

        st.write(
            f"Strikeouts: {g['strikeouts']} "
            f"• Double plays: {g['double_plays']} "
            f"• Runs allowed: {g['runs']}"
        )

        if g["finished"]:
            st.download_button(
                "Download decisions (CSV)",
                pd.DataFrame(
                    g["decisions"]
                ).to_csv(index=False),
                "manager_report.csv",
                "text/csv"
            )

    if st.button("START NEW GAME"):
        st.session_state.game = None
        st.session_state.loaded = None
        st.rerun()


st.caption(
    "Historical player data: MLB Stats API. "
    "At-bats, handedness adjustments, stamina, "
    "baserunning and decisions are modeled for "
    "educational play. Lineups approximate season "
    "regulars, not verified game-day orders."
)
