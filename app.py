
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
import pandas as pd
import requests
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
    max-width: 1880px;
    padding-top: .35rem;
    padding-bottom: 1rem;
}
h1 {color: #e87623; font-weight: 900!important;}
[data-testid="stMetric"] {
    background: linear-gradient(135deg,#132e46,#1c4b5a);
    border: 1px solid #3a6870;
    padding: 9px;
    border-radius: 12px;
}
[data-testid="stMetric"] * {color: white!important;}
.stButton>button[kind="primary"] {
    background:#e76e21;
    color:white;
    font-weight:800;
    border:0;
}
.stButton>button {border-radius:10px;}
.matchup-tile {
    background:linear-gradient(135deg,#102a40,#1a4852);
    color:white;
    border:1px solid #47616d;
    border-radius:12px;
    padding:12px;
    margin:5px 0;
}
.face-card {
    background:#193b4b;
    border-radius:12px;
    padding:8px;
    display:flex;
    align-items:center;
    gap:10px;
    color:white;
}
.face-card img {
    width:65px;
    height:65px;
    object-fit:contain;
}
.face-kicker {
    font-size:10px;
    color:#ffbd75;
}
.face-name {
    font-size:15px;
    font-weight:800;
}
.face-sub {font-size:11px;}
</style>
""", unsafe_allow_html=True)

API = "https://statsapi.mlb.com/api/v1"


# ==========================================
# HELPERS AND HISTORICAL MLB DATA
# ==========================================

def number(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def bounded(x, a, b):
    return max(a, min(b, x))


def throwing(x):
    return {"R": "RHP", "L": "LHP"}.get(x, "?HP")


@st.cache_data(ttl=86400, show_spinner=False)
def fetch(path, params=None):
    response = requests.get(
        f"{API}/{path}",
        params=params or {},
        timeout=18
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=86400, show_spinner=False)
def team_list(year):
    return sorted(
        fetch("teams", {
            "sportId": 1,
            "season": year
        }).get("teams", []),
        key=lambda t: t["name"]
    )


@st.cache_data(ttl=86400, show_spinner=False)
def roster(team_id, year):
    return fetch(
        f"teams/{team_id}/roster",
        {
            "rosterType": "fullSeason",
            "season": year
        }
    ).get("roster", [])


@st.cache_data(ttl=86400, show_spinner=False)
def player(pid, year, group):
    data = fetch(f"people/{pid}")
    info = (data.get("people") or [{}])[0]

    stats = fetch(
        f"people/{pid}/stats",
        {
            "stats": "season",
            "group": group,
            "season": year,
            "gameType": "R"
        }
    )

    s = next(
        (
            split.get("stat", {})
            for section in stats.get("stats", [])
            for split in section.get("splits", [])
            if split.get("stat")
        ),
        {}
    )

    return {
        "id": pid,
        "name": info.get("fullName", "Unknown"),
        "throws": info.get(
            "pitchHand", {}
        ).get("code", "?"),
        "bats": info.get(
            "batSide", {}
        ).get("code", "?"),
        "stats": s
    }


@st.cache_data(ttl=86400, show_spinner=False)
def load_squad(team_id, year, group):
    choices = []

    for item in roster(team_id, year):
        pos = item.get(
            "position", {}
        ).get("abbreviation")

        if (group == "pitching") == (
            pos in ("P", "TWP")
        ):
            pid = item.get(
                "person", {}
            ).get("id")

            if pid:
                choices.append(pid)

    result = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = [
            pool.submit(
                player, pid, year, group
            )
            for pid in choices
        ]

        for job in as_completed(jobs):
            try:
                p = job.result()
                s = p["stats"]

                if (
                    group == "pitching"
                    and number(s.get("gamesPlayed"))
                ):
                    starts = number(s.get("gamesStarted"))
                    games = number(s.get("gamesPlayed"))

                    p["role"] = (
                        "SP"
                        if starts >= max(3, games * .35)
                        else "RP"
                    )

                    p["relief_apps"] = max(
                        0, games - starts
                    )

                    result.append(p)

                elif (
                    group == "hitting"
                    and number(s.get("atBats"))
                ):
                    result.append(p)

            except (
                requests.RequestException,
                ValueError,
                KeyError
            ):
                continue

    if group == "pitching":
        starters = sorted(
            [
                p for p in result
                if p["role"] == "SP"
            ],
            key=lambda p: -number(
                p["stats"].get("gamesStarted")
            )
        )[:5]

        relief = sorted(
            [
                p for p in result
                if p["role"] == "RP"
            ],
            key=lambda p: -p["relief_apps"]
        )[:8]

        return starters + relief

    return sorted(
        result,
        key=lambda p: -number(
            p["stats"].get(
                "plateAppearances",
                number(p["stats"].get("atBats"))
            )
        )
    )[:13]


# ==========================================
# LEFTY/RIGHTY SPLITS
# ==========================================

@st.cache_data(ttl=86400, show_spinner=False)
def handed_splits(player_id, year, group):
    result = {}

    for hand_code, situation in (
        ("L", "vl"),
        ("R", "vr")
    ):
        try:
            payload = fetch(
                f"people/{player_id}/stats",
                {
                    "stats": "statSplits",
                    "group": group,
                    "season": year,
                    "gameType": "R",
                    "sitCodes": situation
                }
            )

            for section in payload.get("stats", []):
                for entry in section.get("splits", []):
                    values = entry.get("stat") or {}
                    meta = entry.get("split") or {}

                    code = str(
                        entry.get("sitCode")
                        or meta.get("code")
                        or ""
                    ).lower()

                    description = str(
                        meta.get("description")
                        or ""
                    ).lower()

                    if code and code != situation:
                        continue

                    if description:
                        if (
                            hand_code == "L"
                            and "right" in description
                        ):
                            continue

                        if (
                            hand_code == "R"
                            and "left" in description
                        ):
                            continue

                    if number(values.get("atBats")) > 0:
                        result[hand_code] = values
                        break

                if hand_code in result:
                    break

        except (
            requests.RequestException,
            ValueError,
            KeyError
        ):
            continue

    return result


def observed_batting_split(h, pitcher_hand, year):
    if pitcher_hand not in ("L", "R"):
        return None

    return handed_splits(
        h["id"], year, "hitting"
    ).get(pitcher_hand)


def observed_pitching_split(p, batter_hand, year):
    if batter_hand not in ("L", "R"):
        return None

    return handed_splits(
        p["id"], year, "pitching"
    ).get(batter_hand)


@st.cache_data(ttl=86400, show_spinner=False)
def h2h(pid, bid, year):
    try:
        data = fetch(
            f"people/{pid}/stats",
            {
                "stats": "vsPlayer",
                "group": "pitching",
                "season": year,
                "opposingPlayerId": bid,
                "gameType": "R"
            }
        )

        for sec in data.get("stats", []):
            for split in sec.get("splits", []):
                opp = (
                    split.get("opponent")
                    or split.get("player")
                    or {}
                )

                if (
                    str(opp.get("id")) == str(bid)
                    and split.get("stat")
                ):
                    return split["stat"]

    except (
        requests.RequestException,
        KeyError,
        ValueError
    ):
        pass

    return None


# ==========================================
# GAME STATE
# ==========================================

def starting_game(data, starter, mode):
    staff = data["staff"]

    return {
        "year": data["year"],
        "team": data["team"],
        "opponent": data["opponent"],
        "staff": staff,
        "lineup": data["hitters"][:9].copy(),
        "bench": data["hitters"][9:].copy(),
        "pitcher": starter,
        "used": [starter],
        "pitches": {p["id"]: 0 for p in staff},
        "faced": {p["id"]: 0 for p in staff},
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
        "log": ["PLAY BALL!"],
        "decisions": [],
        "strategy_log": [],
        "strikeouts": 0,
        "double_plays": 0,
        "intentional_walks": 0,
        "last_play": "Game begins!",
        "play_result": "",
        "play_detail": "",
        "play_runs": 0,
        "prior_bases": [False] * 3,
        "play_serial": 0,
        "highlights": [],
        "achievements": [],
        "inning_start_runs": 0,
        "inning_reliever": False,
        "jam_escaped": False,
        "relief_scoreless": 0
    }


def pitcher(g):
    return next(
        p for p in g["staff"]
        if p["id"] == g["pitcher"]
    )


def batter(g):
    return g["lineup"][g["spot"] % 9]


# ==========================================
# FATIGUE AND BULLPEN
# ==========================================

def pitcher_limit(p, mode):
    s = p["stats"]
    starts = number(s.get("gamesStarted"))
    ip = number(s.get("inningsPitched"))

    if p["role"] == "SP":
        limit = bounded(
            ip / max(1, starts) * 18
            if starts else 105,
            85,
            140
        )
    else:
        relief_apps = max(
            1,
            number(s.get("gamesPlayed")) - starts
        )

        limit = bounded(
            ip / relief_apps * 23,
            20,
            48
        )

    multiplier = (
        1.35 if mode == "Rookie"
        else .90 if mode == "Hall of Fame"
        else 1
    )

    return limit * multiplier


def fatigue(g, p):
    return (
        g["pitches"][p["id"]]
        / pitcher_limit(p, g["mode"])
    )


def legal_change(g):
    return (
        not g["finished"]
        and (
            g["appearance"] >= 3
            or g["inning"] > g["entry_inning"]
        )
    )


def warm(g, pid):
    if (
        pid in g["used"]
        or pid in g["ready"]
        or pid in g["warming"]
    ):
        return

    g["warming"][pid] = 0

    p = next(
        x for x in g["staff"]
        if x["id"] == pid
    )

    g["log"].append(
        f"🔥 {p['name']} begins warming."
    )


def warm_tick(g):
    for pid in list(g["warming"]):
        g["warming"][pid] += 1

        if g["warming"][pid] >= 2:
            del g["warming"][pid]
            g["ready"].append(pid)

            name = next(
                p["name"]
                for p in g["staff"]
                if p["id"] == pid
            )

            g["log"].append(
                f"✅ {name} is ready."
            )


# ==========================================
# PROBABILITY MODEL
# ==========================================

def batter_hit_rates(h):
    s = h["stats"]

    hits = number(s.get("hits"))
    ab = number(s.get("atBats"))

    avg = (
        hits / ab
        if ab
        else number(s.get("avg"), .250)
    )

    doubles = number(s.get("doubles"))
    triples = number(s.get("triples"))
    hr = number(s.get("homeRuns"))

    singles = max(
        0,
        hits - doubles - triples - hr
    )

    weights = (
        [singles, doubles, triples, hr]
        if hits
        else [70, 19, 2, 9]
    )

    return bounded(avg, .08, .45), weights


def estimate(
    g, p, h,
    approach="Balanced",
    defense="Normal"
):
    ps = p["stats"]
    bf = max(
        1,
        number(ps.get("battersFaced"), 1)
    )

    avg, _ = batter_hit_rates(h)

    split = observed_batting_split(
        h, p["throws"], g["year"]
    )

    split_ab = (
        number(split.get("atBats"))
        if split else 0
    )

    if split_ab > 0:
        observed_avg = (
            number(split.get("hits")) / split_ab
        )

        weight = split_ab / (split_ab + 90)

        avg = (
            weight * observed_avg
            + (1 - weight) * avg
        )

        hand_adjust = 0
    else:
        hand_adjust = (
            -.017
            if (
                h["bats"] == p["throws"]
                and h["bats"] in ("L", "R")
            )
            else .010
        )

    tired = max(
        0,
        fatigue(g, p) - .65
    )

    pitch_split = observed_pitching_split(
        p, h["bats"], g["year"]
    )

    season_k = (
        number(ps.get("strikeOuts")) / bf
    )

    season_bb = (
        number(ps.get("baseOnBalls")) / bf
    )

    if (
        pitch_split
        and number(
            pitch_split.get("battersFaced")
        ) > 0
    ):
        split_bf = number(
            pitch_split.get("battersFaced")
        )

        weight = split_bf / (split_bf + 90)

        k_rate = (
            weight * (
                number(
                    pitch_split.get("strikeOuts")
                ) / split_bf
            )
            + (1 - weight) * season_k
        )

        bb_rate = (
            weight * (
                number(
                    pitch_split.get("baseOnBalls")
                ) / split_bf
            )
            + (1 - weight) * season_bb
        )
    else:
        k_rate = season_k
        bb_rate = season_bb

    hp = bounded(
        avg
        + hand_adjust
        + (
            number(ps.get("whip"), 1.3) - 1.3
        ) * .045
        + (
            number(ps.get("era"), 4.2) - 4.2
        ) * .006
        + tired * .065,
        .08, .48
    )

    wp = bounded(
        bb_rate + tired * .03,
        .025, .18
    )

    kp = bounded(
        k_rate - tired * .03,
        .06, .40
    )

    if approach == "Attack the zone":
        hp *= 1.12
        wp *= .70
        kp *= 1.20

    elif approach == "Pitch carefully":
        hp *= .88
        wp *= 1.70
        kp *= .82

    if (
        defense == "Infield in"
        and g["bases"][2]
        and g["outs"] < 2
    ):
        hp *= 1.13

    if (
        defense == "Double-play depth"
        and g["bases"][0]
        and g["outs"] < 2
    ):
        hp *= 1.04

    hp = bounded(hp, .04, .58)
    wp = bounded(wp, .01, .32)
    kp = bounded(
        kp, .02,
        max(.02, 1 - hp - wp)
    )

    return hp, wp, kp


def next_hitters(g):
    return [
        g["lineup"][
            (g["spot"] + i) % 9
        ]
        for i in range(3)
    ]


def compare_pitchers(g):
    hitters = next_hitters(g)

    options = (
        [pitcher(g)]
        + [
            p for p in g["staff"]
            if p["id"] not in g["used"]
        ]
    )

    result = []

    for p in options:
        probabilities = [
            estimate(g, p, h)
            for h in hitters
        ]

        hp = sum(
            x[0] for x in probabilities
        ) / 3

        wp = sum(
            x[1] for x in probabilities
        ) / 3

        kp = sum(
            x[2] for x in probabilities
        ) / 3

        risk = hp + .7 * wp - .22 * kp
        pid = p["id"]

        state = (
            "ON MOUND"
            if pid == g["pitcher"]
            else "READY"
            if pid in g["ready"]
            else "WARMING"
            if pid in g["warming"]
            else "COLD"
        )

        result.append({
            "ID": pid,
            "Pitcher": p["name"],
            "Role": p["role"],
            "Hand": throwing(p["throws"]),
            "Status": state,
            "ERA": p["stats"].get("era", "—"),
            "WHIP": p["stats"].get("whip", "—"),
            "Hit %": round(hp * 100, 1),
            "Walk %": round(wp * 100, 1),
            "K %": round(kp * 100, 1),
            "Risk proxy": round(risk * 100, 1)
        })

    return sorted(
        result,
        key=lambda r: r["Risk proxy"]
    )


def bring_in(g, pid, reason):
    if (
        not legal_change(g)
        or pid not in g["ready"]
    ):
        return

    old = pitcher(g)

    new = next(
        x for x in g["staff"]
        if x["id"] == pid
    )

    comparisons = compare_pitchers(g)

    old_risk = next(
        r["Risk proxy"]
        for r in comparisons
        if r["ID"] == old["id"]
    )

    new_risk = next(
        r["Risk proxy"]
        for r in comparisons
        if r["ID"] == pid
    )

    g["decisions"].append({
        "Inning": g["inning"],
        "Outs": g["outs"],
        "Out": old["name"],
        "In": new["name"],
        "Hand": throwing(new["throws"]),
        "Reason": reason,
        "Old risk": old_risk,
        "New risk": new_risk,
        "Improved proxy": new_risk < old_risk,
        "Fatigue at change": round(
            fatigue(g, old) * 100
        )
    })

    g["pitcher"] = pid
    g["used"].append(pid)
    g["ready"].remove(pid)
    g["appearance"] = 0
    g["entry_inning"] = g["inning"]

    g["inning_reliever"] = True

    g["log"].append(
        f"🔁 {new['name']} replaces {old['name']}."
    )

    if (
        new_risk < old_risk
        and "Matchup Master"
        not in g["achievements"]
    ):
        g["achievements"].append(
            "Matchup Master"
        )


# ==========================================
# COMPUTER PINCH HITTER
# ==========================================

def pinch_hit(g):
    if (
        g["inning"] < 7
        or not g["bench"]
        or not (
            any(g["bases"])
            or g["inning"] >= 8
        )
    ):
        return

    ix = g["spot"] % 9
    old = g["lineup"][ix]
    current = pitcher(g)

    replacement = max(
        g["bench"],
        key=lambda h: estimate(
            g, current, h
        )[0]
    )

    if (
        estimate(
            g, current, replacement
        )[0]
        >
        estimate(
            g, current, old
        )[0] + .035
    ):
        g["lineup"][ix] = replacement
        g["bench"].remove(replacement)

        g["log"].append(
            f"📣 PINCH HITTER: "
            f"{replacement['name']} replaces "
            f"{old['name']}!"
        )


# ==========================================
# BASERUNNING
# ==========================================

def advance_hit(g, distance):
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
                and random.random() <
                (.72 if g["outs"] == 2 else .50)
            ):
                advance = 2

            if (
                distance == 1
                and i == 0
                and random.random() < .23
            ):
                advance = 2

            dest = i + advance

            if dest >= 3:
                runs += 1
            else:
                while dest < 3 and new[dest]:
                    dest += 1

                if dest >= 3:
                    runs += 1
                else:
                    new[dest] = True

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

    g["runs"] += score
    g["walks"] += 1

    return score


# ==========================================
# ACHIEVEMENTS AND INNINGS
# ==========================================

def after_batter(g):
    g["spot"] += 1
    warm_tick(g)
    g["play_serial"] += 1

    if g["outs"] >= 3:
        g["log"].append(
            f"End of inning {g['inning']}. "
            f"{g['runs']} runs allowed."
        )

        if (
            g["jam_escaped"]
            and g["runs"] == g["inning_start_runs"]
            and "Escape Artist"
            not in g["achievements"]
        ):
            g["achievements"].append(
                "Escape Artist"
            )

        if (
            g["inning_reliever"]
            and g["runs"] == g["inning_start_runs"]
        ):
            g["relief_scoreless"] += 1

            if (
                g["relief_scoreless"] >= 3
                and "Bullpen Lockdown"
                not in g["achievements"]
            ):
                g["achievements"].append(
                    "Bullpen Lockdown"
                )

        elif g["runs"] > g["inning_start_runs"]:
            g["relief_scoreless"] = 0

        g["outs"] = 0
        g["bases"] = [False] * 3
        g["inning"] += 1

        g["inning_start_runs"] = g["runs"]

        g["inning_reliever"] = (
            pitcher(g)["role"] == "RP"
        )

        g["jam_escaped"] = False

    if g["inning"] > 9:
        g["finished"] = True

        if (
            g["runs"] == 0
            and "Shutout Legend"
            not in g["achievements"]
        ):
            g["achievements"].append(
                "Shutout Legend"
            )


def intentional_walk(g):
    if (
        g["finished"]
        or all(g["bases"])
    ):
        return

    g["prior_bases"] = g["bases"].copy()

    p = pitcher(g)
    h = batter(g)
    scored = walk_bases(g)

    g["faced"][p["id"]] += 1
    g["appearance"] += 1
    g["intentional_walks"] += 1

    g["strategy_log"].append({
        "Inning": g["inning"],
        "Batter": h["name"],
        "Pitcher": p["name"],
        "Approach": "Intentional walk",
        "Defense": "—",
        "Hit %": "—",
        "Walk %": 100
    })

    g["play_result"] = "INTENTIONAL WALK"
    g["play_detail"] = (
        f"{h['name']} takes first base"
    )
    g["play_runs"] = scored

    g["last_play"] = (
        f"{p['name']} intentionally walks "
        f"{h['name']}."
    )

    g["log"].append(g["last_play"])
    after_batter(g)


# ==========================================
# AT-BAT SIMULATION
# ==========================================

def simulate(g, approach, defense):
    if g["finished"]:
        return

    g["prior_bases"] = g["bases"].copy()

    pinch_hit(g)

    p = pitcher(g)
    h = batter(g)

    if (
        g["bases"][1]
        or g["bases"][2]
    ):
        g["jam_escaped"] = True

    hp, wp, kp = estimate(
        g, p, h, approach, defense
    )

    g["strategy_log"].append({
        "Inning": g["inning"],
        "Batter": h["name"],
        "Pitcher": p["name"],
        "Approach": approach,
        "Defense": defense,
        "Hit %": round(hp * 100, 1),
        "Walk %": round(wp * 100, 1)
    })

    roll = random.random()
    runs = 0

    if roll < wp:
        runs = walk_bases(g)
        result = "walks"

    elif roll < wp + hp:
        _, weights = batter_hit_rates(h)

        distance = random.choices(
            [1, 2, 3, 4],
            weights=weights,
            k=1
        )[0]

        runs = advance_hit(g, distance)
        g["hits"] += 1

        result = {
            1: "singles",
            2: "DOUBLES",
            3: "TRIPLES",
            4: "HOMERS"
        }[distance]

    elif roll < wp + hp + kp:
        g["outs"] += 1
        g["strikeouts"] += 1
        result = "strikes out"

    else:
        if (
            g["bases"][0]
            and g["outs"] < 2
            and random.random() < (
                .27
                if defense == "Double-play depth"
                else .11
                if defense == "Infield in"
                else .16
            )
        ):
            g["outs"] += 2
            g["double_plays"] += 1
            g["bases"][0] = False

            result = (
                "grounds into a DOUBLE PLAY"
            )

        elif (
            g["bases"][2]
            and g["outs"] < 2
            and random.random() < (
                .04
                if defense == "Infield in"
                else .17
            )
        ):
            g["outs"] += 1
            g["bases"][2] = False
            g["runs"] += 1
            runs = 1

            result = "hits a sacrifice fly"

        else:
            g["outs"] += 1
            result = "is retired on a ball in play"

    pitches = (
        random.randint(4, 9)
        if approach == "Pitch carefully"
        else random.randint(2, 6)
        if approach == "Attack the zone"
        else random.randint(3, 8)
    )

    g["pitches"][p["id"]] += pitches
    g["faced"][p["id"]] += 1
    g["appearance"] += 1

    g["play_result"] = {
        "walks": "WALK",
        "singles": "SINGLE",
        "DOUBLES": "DOUBLE",
        "TRIPLES": "TRIPLE",
        "HOMERS": "HOME RUN!",
        "strikes out": "STRIKEOUT",
        "grounds into a DOUBLE PLAY": "DOUBLE PLAY",
        "hits a sacrifice fly": "SAC FLY",
        "is retired on a ball in play": "OUT"
    }.get(result, "OUT")

    g["play_detail"] = (
        f"{h['name']} vs. {p['name']}"
    )

    g["play_runs"] = runs

    g["last_play"] = (
        f"Inning {g['inning']}: "
        f"{h['name']} {result} "
        f"vs. {p['name']} "
        f"({pitches} pitches)."
    )

    if runs:
        g["last_play"] += (
            f" {runs} run(s) score!"
        )

    g["log"].append(g["last_play"])

    if g["play_result"] in (
        "HOME RUN!",
        "TRIPLE",
        "DOUBLE",
        "STRIKEOUT",
        "DOUBLE PLAY"
    ):
        g["highlights"].append(
            g["last_play"]
        )

    after_batter(g)


# ==========================================
# SITUATIONAL DANGER
# ==========================================

def danger(g):
    if g["finished"]:
        return None

    if all(g["bases"]):
        return "🚨 BASES LOADED"

    if g["bases"][1] or g["bases"][2]:
        return "⚠️ RUNNERS IN SCORING POSITION"

    if (
        g["inning"] >= 7
        and any(g["bases"])
    ):
        return "🔥 LATE-INNING PRESSURE"

    if fatigue(g, pitcher(g)) > .90:
        return "🔋 FATIGUE WARNING"

    return None


# ==========================================
# COMPACT ANIMATED DIAMOND
# ==========================================

def diamond(g):
    def marker(x, y, occupied):
        fill = (
            "#ffb33d"
            if occupied
            else "#f5e8c9"
        )

        return (
            f'<rect x="{x-10}" y="{y-10}" '
            f'width="20" height="20" '
            f'transform="rotate(45 {x} {y})" '
            f'fill="{fill}" '
            f'stroke="#153c35" '
            f'stroke-width="2"/>'
        )

    a, b, c = g["bases"]

    headline = escape(
        g.get("play_result", "")
    )

    detail = escape(
        g.get("play_detail", "")
    )

    runs = g.get("play_runs", 0)

    result = ""

    if headline:
        result = f"""
        <div class="result">
          <div class="tag">⚾ LAST PLAY</div>
          <div class="big">{headline}</div>
          <div class="detail">{detail}</div>
          <div class="score">
            {f"+{runs} RUNS" if runs else ""}
          </div>
        </div>
        """

    effect = ""

    if headline in (
        "SINGLE",
        "DOUBLE",
        "TRIPLE",
        "HOME RUN!"
    ):
        effect = (
            '<div class="ball-flight">⚾</div>'
        )

    html = f"""
    <html>
    <head>
    <style>
    body {{
        margin:0;
        background:transparent;
        font-family:Arial,sans-serif;
        overflow:hidden;
    }}

    .stadium {{
        position:relative;
        width:100%;
        height:310px;
        overflow:hidden;
        border-radius:14px;
        background:#0d352a;
    }}

    svg {{
        width:100%;
        height:100%;
    }}

    .result {{
        position:absolute;
        top:52%;
        left:50%;
        transform:translate(-50%,-50%);
        width:84%;
        text-align:center;
        padding:17px;
        background:rgba(6,22,35,.95);
        border:3px solid #ffb139;
        border-radius:14px;
        animation:
            appear .3s ease-out,
            fade 0.35s 3.2s forwards;
    }}

    .tag {{
        color:#ffb33d;
        font-size:11px;
        font-weight:800;
        letter-spacing:2px;
    }}

    .big {{
        color:white;
        font-weight:900;
        font-size:clamp(26px,5vw,50px);
    }}

    .detail {{
        color:#f3dbaf;
        font-size:13px;
    }}

    .score {{
        color:#ffb139;
        font-weight:900;
        font-size:19px;
    }}

    .ball-flight {{
        position:absolute;
        left:48%;
        top:73%;
        font-size:25px;
        animation:flight 1.8s forwards;
    }}

    @keyframes flight {{
        0% {{
            opacity:1;
            transform:translate(0,0);
        }}
        100% {{
            opacity:0;
            transform:translate(95px,-170px)
                scale(.3);
        }}
    }}

    @keyframes appear {{
        from {{opacity:0;}}
        to {{opacity:1;}}
    }}

    @keyframes fade {{
        to {{
            opacity:0;
            visibility:hidden;
        }}
    }}
    </style>
    </head>

    <body>
    <div class="stadium">

    <svg xmlns="http://www.w3.org/2000/svg"
         viewBox="0 0 550 360">

      <defs>
        <pattern id="grass"
                 width="48"
                 height="48"
                 patternUnits="userSpaceOnUse"
                 patternTransform="rotate(23)">
          <rect width="48" height="48"
                fill="#236b48"/>
          <rect width="24" height="48"
                fill="#2e7c52"/>
        </pattern>
      </defs>

      <rect width="550" height="360"
            fill="#123c34"/>

      <path d="M0 75 Q275 -18 550 75
               L550 360 L0 360Z"
            fill="url(#grass)"/>

      <path d="M275 330 L86 177
               L275 35 L464 177 Z"
            fill="#c58b55"
            stroke="#f3d6a6"
            stroke-width="3"/>

      <path d="M275 296 L122 177
               L275 66 L428 177 Z"
            fill="#287247"/>

      <path d="M275 330 L8 110
               M275 330 L542 110"
            stroke="#f6eed9"
            stroke-width="2"/>

      {marker(428,177,a)}
      {marker(275,66,b)}
      {marker(122,177,c)}
      {marker(275,324,False)}

      <ellipse cx="275" cy="199"
               rx="22" ry="13"
               fill="#c99a69"/>

      <rect x="266" y="197"
            width="18" height="5"
            rx="2" fill="#fff"/>

      <text x="15" y="24"
            fill="#ffcb80"
            font-size="15"
            font-weight="800">
        SPORTS BY THE NUMBERS
      </text>

      <text x="15" y="46"
            fill="#fff"
            font-size="12">
        SHUTOUT CHALLENGE • LIVE
      </text>

    </svg>

    {result}
    {effect}

    </div>
    </body>
    </html>
    """

    components.html(
        html,
        height=316,
        scrolling=False
    )


# ==========================================
# PLAYER PORTRAITS
# ==========================================

def player_headshot(pid):
    return (
        "https://img.mlbstatic.com/"
        "mlb-photos/image/upload/"
        f"w_180,q_auto:good/v1/people/{int(pid)}"
        "/headshot/67/current"
    )


def portrait_card(p, label):
    return f"""
    <div class="face-card">
      <img
        src="{player_headshot(p['id'])}"
        alt=""
        onerror="this.style.display='none'"
      />
      <div>
        <div class="face-kicker">
          {escape(label)}
        </div>
        <div class="face-name">
          {escape(p['name'])}
        </div>
        <div class="face-sub">
          ERA {escape(str(p['stats'].get('era','—')))}
          • WHIP
          {escape(str(p['stats'].get('whip','—')))}
        </div>
      </div>
    </div>
    """


# ==========================================
# POSTGAME GRADE
# ==========================================

def report_grade(g):
    changes = g["decisions"]

    evidence = sum(
        1 for x in changes
        if x["Improved proxy"]
    )

    timing = sum(
        1 for x in changes
        if x["Fatigue at change"] <= 110
    )

    participation = min(
        1,
        len(g["strategy_log"]) / 27
    )

    if changes:
        grade = round(
            100 * (
                .5 * evidence / len(changes)
                + .3 * timing / len(changes)
                + .2 * participation
            )
        )
    else:
        grade = round(
            20 * participation
        )

    return bounded(grade, 0, 100)


# ==========================================
# APP SETUP
# ==========================================

st.title(
    "⚾ YOU'RE THE MANAGER"
)

st.caption(
    "STADIUM EDITION III • COMPACT GAME VIEW • "
    "SPORTS BY THE NUMBERS"
)

if "game" not in st.session_state:
    st.session_state.game = None

if "loaded" not in st.session_state:
    st.session_state.loaded = None

if "selected_relief_id" not in st.session_state:
    st.session_state.selected_relief_id = None


with st.expander(
    "⚙️ NEW GAME • Season & matchup",
    expanded=st.session_state.game is None
):
    year = st.selectbox(
        "Season",
        list(range(2025, 1999, -1))
    )

    try:
        teams = {
            t["name"]: t["id"]
            for t in team_list(year)
        }

        setup_a, setup_b = st.columns(2)

        own = setup_a.selectbox(
            "Your pitching team",
            list(teams)
        )

        opp = setup_b.selectbox(
            "Opposing batting team",
            [
                x for x in teams
                if x != own
            ]
        )

        if st.button(
            "LOAD HISTORICAL ROSTERS",
            type="primary"
        ):
            st.session_state.loaded = None

            with st.status(
                "Preparing historical teams...",
                expanded=True
            ) as status:

                progress = st.progress(0)

                st.write(
                    "⚾ Loading starters and relievers"
                )

                staff = load_squad(
                    teams[own],
                    year,
                    "pitching"
                )

                progress.progress(50)

                st.write(
                    "🏏 Building batting lineup"
                )

                hitters = load_squad(
                    teams[opp],
                    year,
                    "hitting"
                )

                progress.progress(90)

                if staff and len(hitters) >= 9:
                    st.session_state.loaded = {
                        "year": year,
                        "team": own,
                        "opponent": opp,
                        "staff": staff,
                        "hitters": hitters
                    }

                    st.session_state.game = None

                    status.update(
                        label="✅ Teams loaded",
                        state="complete",
                        expanded=False
                    )

                else:
                    st.error(
                        "Not enough historical data "
                        "was returned."
                    )

                    status.update(
                        label="Roster incomplete",
                        state="error"
                    )

                progress.progress(100)

    except requests.RequestException as err:
        st.error(
            "MLB data unavailable right now."
        )
        st.caption(str(err))


# ==========================================
# START GAME
# ==========================================

loaded = st.session_state.loaded

if loaded and st.session_state.game is None:

    starters = [
        x for x in loaded["staff"]
        if x["role"] == "SP"
    ] or loaded["staff"]

    by_id = {
        p["id"]: p
        for p in starters
    }

    s1, s2 = st.columns(2)

    starter = s1.selectbox(
        "Choose your starting pitcher",
        list(by_id),
        format_func=lambda pid: (
            f"{by_id[pid]['name']} • "
            f"{throwing(by_id[pid]['throws'])} • "
            f"ERA "
            f"{by_id[pid]['stats'].get('era','—')}"
        )
    )

    difficulty = s2.selectbox(
        "Difficulty",
        [
            "Rookie",
            "Pro",
            "Hall of Fame"
        ]
    )

    if st.button(
        "⚾ PLAY BALL!",
        type="primary"
    ):
        st.session_state.game = starting_game(
            loaded,
            starter,
            difficulty
        )

        st.session_state.selected_relief_id = None

        st.rerun()


# ==========================================
# COMPACT LIVE GAME
# ==========================================

g = st.session_state.game

if g:
    p = pitcher(g)
    h = batter(g)

    st.caption(
        f"LIVE GAME • {g['year']} • "
        f"{g['team']} vs. {g['opponent']} • "
        f"{g['mode']}"
    )

    cols = st.columns(6)

    metrics = [
        ("INNING", min(9, g["inning"])),
        ("OUTS", g["outs"]),
        ("RUNS", g["runs"]),
        ("HITS", g["hits"]),
        ("PITCHES", g["pitches"][p["id"]]),
        ("STRIKEOUTS", g["strikeouts"])
    ]

    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)

    left, center, right = st.columns(
        [.9, 2.15, 1.05],
        gap="small"
    )

    # ======================================
    # LEFT: CURRENT PITCHER
    # ======================================

    with left:
        st.markdown("#### ⚾ ON THE MOUND")

        st.markdown(
            f"**{p['name']}** • "
            f"{throwing(p['throws'])}"
        )

        st.caption(
            f"ERA {p['stats'].get('era','—')} • "
            f"WHIP {p['stats'].get('whip','—')}"
        )

        st.progress(
            bounded(
                fatigue(g, p),
                0, 1
            ),
            text=(
                f"Fatigue {fatigue(g,p):.0%}"
            )
        )

        st.caption(
            "Use the Dugout tab below to "
            "warm up or change pitchers."
        )

    # ======================================
    # CENTER: ALWAYS-VISIBLE PITCH ACTION
    # ======================================

    with center:
        st.markdown(
            f"**AT BAT: {h['name']}** "
            f"({h['bats']}) • "
            f"AVG {h['stats'].get('avg','—')}"
        )

        if not g["finished"]:
            approach = st.radio(
                "Pitch approach",
                [
                    "Balanced",
                    "Attack the zone",
                    "Pitch carefully"
                ],
                horizontal=True,
                key="pitch_strategy"
            )

            defenses = ["Normal"]

            if (
                g["bases"][0]
                and g["outs"] < 2
            ):
                defenses.append(
                    "Double-play depth"
                )

            if (
                g["bases"][2]
                and g["outs"] < 2
            ):
                defenses.append(
                    "Infield in"
                )

            defense = st.selectbox(
                "Defense",
                defenses
            )

            action1, action2 = st.columns(
                [2, 1]
            )

            with action1:
                if st.button(
                    "⚾ PITCH TO BATTER",
                    type="primary",
                    use_container_width=True
                ):
                    simulate(
                        g,
                        approach,
                        defense
                    )
                    st.rerun()

            with action2:
                if st.button(
                    "🚶 INT. WALK",
                    disabled=all(g["bases"]),
                    use_container_width=True
                ):
                    intentional_walk(g)
                    st.rerun()

        else:
            if g["runs"] == 0:
                st.success(
                    "🏆 SHUTOUT! "
                    "Nine scoreless innings."
                )
            else:
                st.info(
                    f"GAME OVER • "
                    f"{g['runs']} runs allowed."
                )

        st.markdown("#### 🏟️ LIVE DIAMOND")
        diamond(g)

        with st.expander(
            "📻 Radio booth • Play-by-play"
        ):
            for line in reversed(
                g["log"][-12:]
            ):
                st.write(line)

    # ======================================
    # RIGHT: LINEUP
    # ======================================

    with right:
        st.markdown("#### 🧢 OPPOSING LINEUP")

        current = g["spot"] % 9
        lineup = []

        for i, x in enumerate(g["lineup"]):
            label = (
                "🔴"
                if i == current
                else "🟡"
                if i == (current + 1) % 9
                else "⚪"
                if i == (current + 2) % 9
                else ""
            )

            lineup.append({
                "#": i + 1,
                "": label,
                "Batter": x["name"],
                "Hand": x["bats"],
                "AVG": x["stats"].get(
                    "avg", "—"
                )
            })

        st.dataframe(
            pd.DataFrame(lineup),
            hide_index=True,
            use_container_width=True,
            height=280
        )

        alert = danger(g)

        if alert:
            st.error(alert)
        elif not g["finished"]:
            st.success(
                "No immediate danger"
            )

    # ======================================
    # SECONDARY GAME TABS
    # ======================================

    dugout_tab, scouting_tab, report_tab = st.tabs(
        [
            "🔥 Dugout & Bullpen",
            "🔍 Scouting",
            "📊 Analysis & Report"
        ]
    )

    # ======================================
    # DUGOUT
    # ======================================

    with dugout_tab:
        st.markdown(
            "### Bullpen Cards & Pitching Changes"
        )

        with st.expander(
            "Full pitching staff"
        ):
            rows = []

            for x in g["staff"]:
                pid = x["id"]

                status = (
                    "ON MOUND"
                    if pid == g["pitcher"]
                    else "USED"
                    if pid in g["used"]
                    else "READY"
                    if pid in g["ready"]
                    else "WARMING"
                    if pid in g["warming"]
                    else "AVAILABLE"
                )

                rows.append({
                    "Pitcher": x["name"],
                    "Role": x["role"],
                    "Throws": throwing(x["throws"]),
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
                use_container_width=True
            )

        if not g["finished"]:
            available = [
                x for x in g["staff"]
                if x["id"] not in g["used"]
            ]

            if not available:
                st.info(
                    "No unused pitchers remain."
                )

            else:
                for j in range(
                    0,
                    min(4, len(available)),
                    2
                ):
                    pair = st.columns(2)

                    for col, x in zip(
                        pair,
                        available[j:j + 2]
                    ):
                        pid = x["id"]

                        status = (
                            "🟢 READY"
                            if pid in g["ready"]
                            else (
                                f"🟡 WARMING "
                                f"{g['warming'][pid]}/2"
                            )
                            if pid in g["warming"]
                            else "⚪ COLD"
                        )

                        with col:
                            st.markdown(
                                portrait_card(
                                    x,
                                    f"{x['role']} • "
                                    f"{throwing(x['throws'])}"
                                ),
                                unsafe_allow_html=True
                            )

                            st.caption(status)

                            if st.button(
                                "SELECT PITCHER",
                                key=f"pick_{pid}",
                                use_container_width=True
                            ):
                                st.session_state.selected_relief_id = pid
                                st.rerun()

                if len(available) > 4:
                    with st.expander(
                        "Additional bullpen pitchers"
                    ):
                        for x in available[4:]:
                            pid = x["id"]

                            if st.button(
                                f"{x['name']} • "
                                f"{throwing(x['throws'])}",
                                key=f"extra_{pid}"
                            ):
                                st.session_state.selected_relief_id = pid
                                st.rerun()

                choices = {
                    x["id"]: x
                    for x in available
                }

                selected = (
                    st.session_state.selected_relief_id
                )

                if selected not in choices:
                    selected = available[0]["id"]
                    st.session_state.selected_relief_id = selected

                chosen = choices[selected]

                st.markdown(
                    f"**Selected pitcher:** "
                    f"{chosen['name']} "
                    f"({throwing(chosen['throws'])})"
                )

                if selected in g["warming"]:
                    st.warning(
                        "This pitcher is warming up. "
                        f"{g['warming'][selected]}/2 "
                        "batters completed."
                    )

                elif selected in g["ready"]:
                    reason = st.selectbox(
                        "Reason for pitching change",
                        [
                            "Better matchup",
                            "Fatigue",
                            "Handedness advantage",
                            "Strikeout ability",
                            "Prevent runs"
                        ]
                    )

                    if st.button(
                        "🔁 BRING PITCHER IN",
                        disabled=not legal_change(g),
                        type="primary"
                    ):
                        bring_in(
                            g,
                            selected,
                            reason
                        )

                        st.session_state.selected_relief_id = None
                        st.rerun()

                    if not legal_change(g):
                        st.caption(
                            "Current pitcher must face "
                            "three batters or finish "
                            "the half-inning."
                        )

                else:
                    if st.button(
                        "🔥 WARM UP SELECTED PITCHER",
                        type="primary"
                    ):
                        warm(g, selected)
                        st.rerun()

    # ======================================
    # SCOUTING
    # ======================================

    with scouting_tab:
        st.markdown(
            "### Historical Matchup Scouting"
        )

        st.write(
            f"**Pitcher:** {p['name']} • "
            f"{throwing(p['throws'])}"
        )

        st.write(
            f"**Batter:** {h['name']} • "
            f"{h['bats']} batting side"
        )

        if not g["finished"]:
            st.markdown(
                "#### Historical batting splits"
            )

            for side in ("L", "R"):
                split = observed_batting_split(
                    h,
                    side,
                    g["year"]
                )

                if split:
                    ab = number(
                        split.get("atBats")
                    )

                    hits = number(
                        split.get("hits")
                    )

                    avg = hits / ab if ab else 0

                    st.write(
                        f"vs. {throwing(side)}: "
                        f"**{int(hits)} hits / "
                        f"{int(ab)} AB "
                        f"({avg:.3f})**"
                    )

                else:
                    st.caption(
                        f"vs. {throwing(side)}: "
                        "Historical split unavailable"
                    )

            st.caption(
                "Real MLB splits are shown when "
                "available. Game probabilities "
                "remain educational estimates."
            )

            if danger(g):
                record = h2h(
                    p["id"],
                    h["id"],
                    g["year"]
                )

                if record:
                    st.write(
                        "**Historical head-to-head:** "
                        f"{record.get('hits','—')} hits "
                        f"in {record.get('atBats','—')} "
                        "at-bats"
                    )

                else:
                    st.caption(
                        "Verified head-to-head "
                        "statistics unavailable."
                    )

    # ======================================
    # MATCHUP LAB AND REPORT
    # ======================================

    with report_tab:
        with st.expander(
            "📊 BULLPEN MATCHUP LAB",
            expanded=bool(danger(g))
        ):
            comparisons = compare_pitchers(g)

            if comparisons:
                st.dataframe(
                    pd.DataFrame(
                        comparisons
                    ).drop(columns=["ID"]),
                    hide_index=True,
                    use_container_width=True
                )

                current_row = next(
                    x for x in comparisons
                    if x["ID"] == g["pitcher"]
                )

                candidates = [
                    x for x in comparisons
                    if x["ID"] not in g["used"]
                ]

                if candidates:
                    lookup = {
                        x["ID"]: x
                        for x in candidates
                    }

                    selected = st.selectbox(
                        "Compare a reliever",
                        list(lookup),
                        format_func=lambda pid: (
                            lookup[pid]["Pitcher"]
                        )
                    )

                    candidate = lookup[selected]

                    st.write(
                        f"**Current pitcher risk index:** "
                        f"{current_row['Risk proxy']}"
                    )

                    st.write(
                        f"**Selected reliever risk index:** "
                        f"{candidate['Risk proxy']}"
                    )

                    delta = (
                        candidate["Risk proxy"]
                        - current_row["Risk proxy"]
                    )

                    if delta < 0:
                        st.success(
                            "Selected reliever has a "
                            "lower modeled risk index."
                        )
                    else:
                        st.info(
                            "Current pitcher has an "
                            "equal or lower modeled "
                            "risk index."
                        )

                    st.markdown(
                        "#### Next three hitters"
                    )

                    rows = []

                    choice = next(
                        x for x in g["staff"]
                        if x["id"] == selected
                    )

                    for hit in next_hitters(g):
                        a = estimate(
                            g, p, hit
                        )

                        b = estimate(
                            g, choice, hit
                        )

                        rows.append({
                            "Batter": hit["name"],
                            "Current hit %": round(
                                a[0] * 100, 1
                            ),
                            "Reliever hit %": round(
                                b[0] * 100, 1
                            ),
                            "Current K %": round(
                                a[2] * 100, 1
                            ),
                            "Reliever K %": round(
                                b[2] * 100, 1
                            )
                        })

                    st.dataframe(
                        pd.DataFrame(rows),
                        hide_index=True,
                        use_container_width=True
                    )

                st.caption(
                    "Risk index is a teaching tool "
                    "combining hit, walk, and "
                    "strikeout estimates. It is "
                    "not a probability of runs."
                )

        with st.expander(
            "🏆 MANAGER REPORT CARD",
            expanded=g["finished"]
        ):
            st.metric(
                "Manager decision score",
                f"{report_grade(g)}/100"
            )

            st.markdown(
                "#### 🏅 Achievements"
            )

            if g["achievements"]:
                for achievement in g["achievements"]:
                    st.success(
                        f"🏅 {achievement}"
                    )
            else:
                st.caption(
                    "Achievements unlock "
                    "during gameplay."
                )

            st.markdown(
                "#### 🎙️ Highlights"
            )

            if g["highlights"]:
                for highlight in reversed(
                    g["highlights"][-8:]
                ):
                    st.write(highlight)
            else:
                st.caption(
                    "Major plays will appear here."
                )

            if g["decisions"]:
                st.markdown(
                    "#### Pitching changes"
                )

                st.dataframe(
                    pd.DataFrame(g["decisions"]),
                    hide_index=True,
                    use_container_width=True
                )

            if g["strategy_log"]:
                st.markdown(
                    "#### Strategy decisions"
                )

                st.dataframe(
                    pd.DataFrame(
                        g["strategy_log"]
                    ).tail(30),
                    hide_index=True,
                    use_container_width=True
                )

            st.write(
                f"Runs: {g['runs']} • "
                f"Hits: {g['hits']} • "
                f"Strikeouts: {g['strikeouts']} • "
                f"Double plays: {g['double_plays']}"
            )

            if g["finished"]:
                st.download_button(
                    "Download manager decisions",
                    pd.DataFrame(
                        g["decisions"]
                    ).to_csv(index=False),
                    "manager_decisions.csv",
                    "text/csv"
                )

    if st.button(
        "🔄 START NEW GAME"
    ):
        st.session_state.game = None
        st.session_state.loaded = None
        st.session_state.selected_relief_id = None
        st.rerun()


st.caption(
    "Sports by the Numbers • Historical MLB "
    "statistics support an educational "
    "baseball simulation. Outcomes, stamina, "
    "matchup risk, and managerial grading "
    "are modeled estimates."
)
