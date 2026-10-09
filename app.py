import random
import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="You're the Manager | Shutout Challenge", page_icon="⚾", layout="wide")
API = "https://statsapi.mlb.com/api/v1"

st.markdown("""<style>
.block-container{max-width:1650px;padding-top:.7rem;padding-bottom:1rem}
h1{font-size:2rem!important;color:#f47a20}
h3{font-size:1.12rem!important}
[data-testid="stMetric"]{background:#1d3547;border-radius:10px;padding:8px 12px}
[data-testid="stMetric"] label,[data-testid="stMetric"] [data-testid="stMetricValue"]{color:white!important}
</style>""", unsafe_allow_html=True)


def n(x, default=0.0):
    try:
        return float(x)
    except (ValueError, TypeError):
        return default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


@st.cache_data(ttl=86400, show_spinner=False)
def get_json(path, params=None):
    response = requests.get(f"{API}/{path}", params=params or {}, timeout=25)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=86400, show_spinner=False)
def teams_for(year):
    return sorted(get_json("teams", {"sportId": 1, "season": year}).get("teams", []), key=lambda t: t["name"])


@st.cache_data(ttl=86400, show_spinner=False)
def get_team_players(team_id, year):
    entries = get_json(f"teams/{team_id}/roster", {"rosterType": "fullSeason", "season": year}).get("roster", [])
    players = []
    for entry in entries:
        p = entry.get("person", {})
        pid = p.get("id")
        if pid:
            players.append({"id": pid, "name": p.get("fullName", "Unknown"), "position": entry.get("position", {}).get("abbreviation", "")})
    return players


@st.cache_data(ttl=86400, show_spinner=False)
def player_info(pid):
    data = get_json(f"people/{pid}").get("people", [])
    return data[0] if data else {}


@st.cache_data(ttl=86400, show_spinner=False)
def player_stats(pid, year, group):
    data = get_json(f"people/{pid}/stats", {"stats": "season", "group": group, "season": year, "gameType": "R"})
    for section in data.get("stats", []):
        if section.get("splits"):
            return section["splits"][0].get("stat", {})
    return {}


@st.cache_data(ttl=86400, show_spinner=False)
def load_team(team_id, year, kind):
    entries = get_team_players(team_id, year)
    if kind == "pitchers":
        entries = [p for p in entries if p["position"] in ("P", "TWP")]
        group = "pitching"
    else:
        entries = [p for p in entries if p["position"] not in ("P", "TWP")]
        group = "hitting"
    result = []
    for entry in entries:
        stats = player_stats(entry["id"], year, group)
        if not stats:
            continue
        if kind == "pitchers" and n(stats.get("inningsPitched")) < 1:
            continue
        if kind == "hitters" and n(stats.get("atBats")) < 1:
            continue
        info = player_info(entry["id"])
        player = {**entry, "stats": stats,
                  "throws": info.get("pitchHand", {}).get("code", "?"),
                  "bats": info.get("batSide", {}).get("code", "?")}
        if kind == "pitchers":
            starts = n(stats.get("gamesStarted"))
            appearances = max(1, n(stats.get("gamesPlayed")))
            player["role"] = "SP" if starts >= max(3, .35 * appearances) else "RP"
            player["relief_apps"] = max(0, appearances - starts)
        result.append(player)
    if kind == "pitchers":
        starters = sorted((p for p in result if p["role"] == "SP"), key=lambda p: -n(p["stats"].get("gamesStarted")))[:5]
        relievers = sorted((p for p in result if p["role"] == "RP"), key=lambda p: -p["relief_apps"])[:8]
        return starters + relievers if starters or relievers else sorted(result, key=lambda p: -n(p["stats"].get("inningsPitched")))[:13]
    return sorted(result, key=lambda p: -n(p["stats"].get("plateAppearances"), n(p["stats"].get("atBats"))))[:13]


def game_new(year, team, opponent, staff, hitters, starter_id):
    lineup = hitters[:9]
    return {"year": year, "team": team, "opponent": opponent, "staff": staff,
            "lineup": [p.copy() for p in lineup], "bench": [p.copy() for p in hitters[9:]],
            "pitcher": starter_id, "used": [starter_id], "pitches": {p["id"]: 0 for p in staff},
            "batters_faced": {p["id"]: 0 for p in staff}, "entry_inning": 1,
            "warming": {}, "ready": [], "inning": 1, "outs": 0,
            "runs": 0, "hits": 0, "walks": 0, "bases": [False, False, False],
            "spot": 0, "log": ["PLAY BALL! Your mission: record 27 outs without allowing a run."],
            "decisions": [], "finished": False, "game_over": False}


def active_pitcher(g):
    return next(p for p in g["staff"] if p["id"] == g["pitcher"])


def fatigue(g):
    pitcher = active_pitcher(g)
    # Doubled previous thresholds to halve buildup; educational stamina model.
    limit = 190 if pitcher["role"] == "SP" else 46
    return g["pitches"][pitcher["id"]] / limit


def can_change(g):
    if g["finished"]:
        return False
    # Simplified three-batter minimum: inning completion also allows a change.
    return g["batters_faced"][g["pitcher"]] >= 3 or g["inning"] > g["entry_inning"]


def warm_up(g, pid):
    if pid in g["used"] or pid in g["ready"] or pid in g["warming"]:
        return
    g["warming"][pid] = 0
    p = next(p for p in g["staff"] if p["id"] == pid)
    g["log"].append(f"🔥 {p['name']} starts warming in the bullpen.")


def advance_warmups(g):
    for pid in list(g["warming"]):
        g["warming"][pid] += 1
        if g["warming"][pid] >= 2:
            g["ready"].append(pid)
            del g["warming"][pid]
            p = next(p for p in g["staff"] if p["id"] == pid)
            g["log"].append(f"✅ {p['name']} is warm and READY.")


def change_pitcher(g, pid, reason):
    if not can_change(g) or pid not in g["ready"] or pid in g["used"]:
        return False
    previous = active_pitcher(g)["name"]
    pitcher = next(p for p in g["staff"] if p["id"] == pid)
    g["pitcher"] = pid
    g["used"].append(pid)
    g["ready"].remove(pid)
    g["entry_inning"] = g["inning"]
    g["decisions"].append({"Inning": g["inning"], "Removed": previous, "Entered": pitcher["name"], "Reason": reason})
    g["log"].append(f"🔁 {pitcher['name']} replaces {previous}. Reason: {reason}.")
    return True


def maybe_pinch_hit(g):
    if g["inning"] < 7 or not g["bench"]:
        return
    ix = g["spot"] % 9
    old = g["lineup"][ix]
    candidates = sorted(g["bench"], key=lambda p: n(p["stats"].get("avg")), reverse=True)
    new = candidates[0]
    if n(old["stats"].get("avg")) < .235 and n(new["stats"].get("avg")) > n(old["stats"].get("avg")) + .035:
        g["lineup"][ix] = new
        g["bench"].remove(new)
        g["log"].append(f"📣 PINCH HITTER: {new['name']} replaces {old['name']}.")


def hit_advance(g, distance):
    new = [False] * 3
    scored = 0
    for i in (2, 1, 0):
        if g["bases"][i]:
            destination = i + distance
            if destination >= 3:
                scored += 1
            else:
                new[destination] = True
    if distance == 4:
        scored += 1
    else:
        new[distance - 1] = True
    g["bases"] = new
    g["runs"] += scored
    return scored


def walk_advance(g):
    first, second, third = g["bases"]
    scored = int(first and second and third)
    g["bases"] = [True, first or second, third or (first and second)]
    g["walks"] += 1
    g["runs"] += scored
    return scored


def simulate(g):
    if g["finished"]:
        return
    maybe_pinch_hit(g)
    batter = g["lineup"][g["spot"] % 9]
    pitcher = active_pitcher(g)
    ps, hs = pitcher["stats"], batter["stats"]
    avg = n(hs.get("avg"), .250)
    obp = n(hs.get("obp"), .320)
    slg = n(hs.get("slg"), .400)
    bf = max(1, n(ps.get("battersFaced"), 300))
    k_rate = n(ps.get("strikeOuts")) / bf
    bb_rate = n(ps.get("baseOnBalls")) / bf
    tired = max(0, fatigue(g) - .75)
    same_side = batter["bats"] in ("L", "R") and pitcher["throws"] == batter["bats"]
    hand_bonus = -.015 if same_side else .008
    hit_prob = clamp(avg + (n(ps.get("whip"), 1.3) - 1.3) * .055 +
                     (n(ps.get("era"), 4.2) - 4.2) * .008 + hand_bonus + tired * .065, .075, .48)
    walk_prob = clamp(.82 * bb_rate + .12 * max(0, obp - avg) + tired * .025, .025, .16)
    strike_prob = clamp(k_rate - tired * .035, .08, .4)
    g["pitches"][pitcher["id"]] += random.randint(3, 8)
    g["batters_faced"][pitcher["id"]] += 1
    roll = random.random()
    scored = 0
    if roll < walk_prob:
        scored = walk_advance(g)
        result = "draws a walk"
    elif roll < walk_prob + hit_prob:
        power = clamp(slg - avg, .06, .38)
        r = random.random()
        bases = 4 if r < power * .24 else 3 if r < power * .27 else 2 if r < power * .69 else 1
        scored = hit_advance(g, bases)
        g["hits"] += 1
        result = {1: "singles", 2: "doubles", 3: "triples", 4: "HOMERS"}[bases]
    else:
        g["outs"] += 1
        result = "strikes out" if roll < walk_prob + hit_prob + strike_prob else "is retired on a ball in play"
    entry = f"{g['inning']}th inning — {batter['name']} {result} vs. {pitcher['name']}."
    if scored:
        entry += f" {scored} run(s) score!"
    g["log"].append(entry)
    g["spot"] += 1
    advance_warmups(g)
    if g["outs"] >= 3:
        g["log"].append(f"End of inning {g['inning']}.")
        g["inning"] += 1
        g["outs"] = 0
        g["bases"] = [False, False, False]
    if g["inning"] > 9:
        g["finished"] = True
        g["log"].append("FINAL: SHUTOUT!" if g["runs"] == 0 else f"FINAL: {g['runs']} runs allowed.")


def field_html(g):
    a, b, c = g["bases"]
    def base(x, y, full):
        color = "#FFB638" if full else "#FFFFFF"
        return f'<rect x="{x}" y="{y}" width="18" height="18" transform="rotate(45 {x+9} {y+9})" fill="{color}" stroke="#143a2b" stroke-width="2"/>'
    return f'''<svg viewBox="0 0 360 235" width="100%" style="max-height:250px">
      <rect width="360" height="235" rx="14" fill="#1a533b"/>
      <path d="M180 214 L54 105 L180 8 L306 105 Z" fill="#bb8d5d" stroke="#f4d9af" stroke-width="3"/>
      <path d="M180 197 L78 105 L180 27 L282 105 Z" fill="#26764d"/>
      {base(272,96,a)}{base(171,18,b)}{base(70,96,c)}{base(171,200,False)}
      <circle cx="180" cy="111" r="11" fill="#dbbd87"/>
      <text x="180" y="116" text-anchor="middle" font-size="12">P</text>
      <text x="180" y="232" text-anchor="middle" fill="white" font-size="11">HOME</text>
      </svg>'''


st.title("⚾ YOU'RE THE MANAGER")
st.caption("SPORTS BY THE NUMBERS • 2000–2025 HISTORICAL SHUTOUT CHALLENGE")
if "game" not in st.session_state:
    st.session_state.game = None
if "loaded" not in st.session_state:
    st.session_state.loaded = None

with st.expander("⚙️ Set up a historical matchup", expanded=st.session_state.game is None):
    year = st.selectbox("Season", list(range(2025, 1999, -1)))
    try:
        teams = teams_for(year)
        lookup = {t["name"]: t["id"] for t in teams}
        col1, col2 = st.columns(2)
        with col1:
            own_name = st.selectbox("Your team", list(lookup))
        with col2:
            opponent_name = st.selectbox("Opponent", [x for x in lookup if x != own_name])
        if st.button("Load historical game-day rosters", type="primary"):
            with st.status("Preparing historical matchup...", expanded=True) as status:
                bar = st.progress(0)
                st.write("📋 Finding your starting pitchers and bullpen...")
                staff = load_team(lookup[own_name], year, "pitchers")
                bar.progress(50)
                st.write("🏏 Building the opposing lineup and bench...")
                hitters = load_team(lookup[opponent_name], year, "hitters")
                bar.progress(90)
                st.write("🧢 Preparing the dugout...")
                if not staff or len(hitters) < 9:
                    st.error("Not enough statistics returned for this matchup. Choose another season/team.")
                else:
                    st.session_state.loaded = {"year": year, "team": own_name, "opponent": opponent_name,
                                               "staff": staff, "hitters": hitters}
                    st.session_state.game = None
                bar.progress(100)
                status.update(label="Roster request complete", state="complete", expanded=False)
    except requests.RequestException as ex:
        st.error("MLB data service failed to respond. Please retry.")
        st.caption(str(ex))

loaded = st.session_state.loaded
if loaded and st.session_state.game is None:
    staff = loaded["staff"]
    starters = [p for p in staff if p["role"] == "SP"] or staff
    starter_ids = [p["id"] for p in starters]
    by_id = {p["id"]: p for p in staff}
    starter_id = st.selectbox("Choose your starter", starter_ids,
                              format_func=lambda pid: f"{by_id[pid]['name']} • ERA {by_id[pid]['stats'].get('era', '—')}")
    if st.button("⚾ PLAY BALL!", type="primary"):
        st.session_state.game = game_new(loaded["year"], loaded["team"], loaded["opponent"],
                                         staff, loaded["hitters"], starter_id)
        st.rerun()

if st.session_state.game is not None:
    g = st.session_state.game
    p = active_pitcher(g)
    batter = g["lineup"][g["spot"] % 9]
    st.subheader(f"{g['year']} • {g['team']} vs. {g['opponent']}")
    metrics = st.columns(5)
    for box, label, value in zip(metrics, ["Inning", "Outs", "Runs Allowed", "Hits", "Pitch Count"],
                                 [min(9, g["inning"]), g["outs"], g["runs"], g["hits"], g["pitches"][p["id"]]]):
        box.metric(label, value)
    left, middle, right = st.columns([1.15, 1.35, 1.15], gap="medium")
    with left:
        st.markdown("### ⚾ Pitching Staff")
        staff_rows = []
        for item in g["staff"]:
            pid = item["id"]
            if pid == g["pitcher"]:
                status = "🔵 IN GAME"
            elif pid in g["used"]:
                status = "USED"
            elif pid in g["ready"]:
                status = "🟢 READY"
            elif pid in g["warming"]:
                status = f"🟡 WARMING {g['warming'][pid]}/2"
            else:
                status = "AVAILABLE"
            staff_rows.append({"Pitcher": item["name"], "Role": item["role"], "ERA": item["stats"].get("era", "—"),
                               "WHIP": item["stats"].get("whip", "—"), "Pitches": g["pitches"][pid], "Status": status})
        st.dataframe(pd.DataFrame(staff_rows), hide_index=True, use_container_width=True, height=270)
        st.write(f"**On the mound:** {p['name']} ({p['role']})")
        st.progress(min(1.0, fatigue(g)), text=f"Fatigue: {fatigue(g):.0%} (educational model)")
        if not g["finished"]:
            st.markdown("#### 🔥 Bullpen Warmups")
            available = [item for item in g["staff"] if item["id"] not in g["used"]]
            # A form of selectable relievers avoids a tall button for every staff member.
            resting = [item for item in available if item["id"] not in g["ready"] and item["id"] not in g["warming"]]
            if resting:
                option = st.selectbox("Choose a pitcher to warm up", [item["id"] for item in resting],
                                     format_func=lambda pid: next(f"{r['name']} ({r['role']}) • ERA {r['stats'].get('era', '—')}" for r in resting if r["id"] == pid), key="warm_pick")
                if st.button("🔥 WARM UP SELECTED PITCHER"):
                    warm_up(g, option)
                    st.rerun()
            if g["warming"]:
                st.caption("Warming: " + ", ".join(next(p2["name"] for p2 in g["staff"] if p2["id"] == pid) + f" ({progress}/2)" for pid, progress in g["warming"].items()))
            ready = [item for item in available if item["id"] in g["ready"]]
            if ready:
                pick = st.selectbox("Ready to enter", [item["id"] for item in ready],
                                    format_func=lambda pid: next(item["name"] for item in ready if item["id"] == pid))
                reason = st.selectbox("Reason for change", ["Fatigue", "Handedness matchup", "Runners in scoring position", "Strikeout ability", "Protect the shutout"])
                if st.button("🔁 BRING IN RELIEVER", disabled=not can_change(g)):
                    change_pitcher(g, pick, reason)
                    st.rerun()
            if not can_change(g):
                st.caption("Three-batter minimum or finish the half-inning (simplified rule).")
    with middle:
        st.markdown("### 🏟️ Live Diamond")
        st.markdown(field_html(g), unsafe_allow_html=True)
        st.write(f"**At bat:** {batter['name']} ({batter['bats']})")
        st.write(f"**Pitching:** {p['name']} ({p['throws']})")
        if g["finished"]:
            if g["runs"] == 0:
                st.success("🏆 SHUTOUT! 27 outs, no runs allowed!")
            else:
                st.info(f"Final: {g['runs']} run(s) allowed.")
        elif st.button("⚾ PITCH TO BATTER", type="primary", use_container_width=True):
            simulate(g)
            st.rerun()
        st.markdown("#### Play-by-play")
        for entry in reversed(g["log"][-5:]):
            st.caption(entry)
    with right:
        st.markdown("### 🧢 Opposing Lineup")
        ix = g["spot"] % 9
        lineup_rows = []
        for i, player in enumerate(g["lineup"]):
            label = "🔴 AT BAT" if i == ix else "🟡 ON DECK" if i == (ix + 1) % 9 else "⚪ IN HOLE" if i == (ix + 2) % 9 else ""
            lineup_rows.append({"#": i + 1, "Status": label, "Batter": player["name"], "Bats": player["bats"], "AVG": player["stats"].get("avg", "—")})
        st.dataframe(pd.DataFrame(lineup_rows), hide_index=True, use_container_width=True, height=270)
        st.markdown("### 📊 Matchup")
        st.write(f"**{p['name']}** vs. **{batter['name']}**")
        st.caption(f"Batter AVG {batter['stats'].get('avg', '—')} • Pitcher ERA {p['stats'].get('era', '—')} • WHIP {p['stats'].get('whip', '—')}")
        st.caption("Actual head-to-head history is not connected yet; these are real season statistics.")
        st.markdown("### 🧠 Situation Room")
        first, second, third = g["bases"]
        occupied = sum(g["bases"])
        if occupied == 3:
            st.error("Bases loaded! A walk forces in a run. Watch walk rate and fatigue.")
        elif g["outs"] == 2 and occupied:
            st.warning("Two-out jam. One out ends the inning; evaluate this matchup.")
        elif (second or third) and g["outs"] < 2:
            st.warning("Runner in scoring position. Strikeout ability may be valuable.")
        elif fatigue(g) >= .75:
            st.warning("Fatigue rising. Consider preparing a reliever.")
        elif g["inning"] >= 7:
            st.info("Late inning! Compare the next hitters with available bullpen options.")
        else:
            st.success("No immediate danger. Watch pitch count and upcoming hitters.")
        bf = max(1, n(p["stats"].get("battersFaced")))
        st.caption(f"Current pitcher's season K rate: {n(p['stats'].get('strikeOuts')) / bf:.1%}")
    with st.expander("📋 Manager Decision Log"):
        if g["decisions"]:
            st.dataframe(pd.DataFrame(g["decisions"]), hide_index=True)
        else:
            st.write("No pitching changes yet.")
    if g["finished"]:
        report = pd.DataFrame(g["decisions"]).to_csv(index=False)
        st.download_button("Download decision report", data=report, file_name="shutout_manager_report.csv", mime="text/csv")
    if st.button("Start a New Game"):
        st.session_state.game = None
        st.session_state.loaded = None
        st.rerun()

st.caption("MLB statistics are historical. Outcomes, fatigue and situational events are simulated and are not MLB predictions. The lineup is based on season playing time, not actual most-common batting order.")
