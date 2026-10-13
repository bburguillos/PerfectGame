import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="You're the Manager", page_icon="⚾", layout="wide")
st.markdown("""<style>
:root {--orange:#f58b31;--navy:#102338;--cream:#f7e7c5}
.block-container{max-width:1880px;padding-top:.35rem;padding-bottom:1.2rem}
h1,h2,h3{font-family:Arial,sans-serif;letter-spacing:.2px}
h1{color:#e87623;font-weight:900!important}
[data-testid="stMetric"]{background:linear-gradient(135deg,#132e46,#1c4b5a);padding:12px;border:1px solid #3a6870;border-radius:12px}
[data-testid="stMetric"] *{color:#fff!important}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:14px}
.stButton>button[kind="primary"]{background:#e76e21;color:white;font-weight:800;border-radius:10px;border:0}
.stButton>button{border-radius:10px;font-weight:650}
[data-testid="stDataFrame"]{border-radius:10px;overflow:hidden}
.broadcast{background:linear-gradient(100deg,#0e253b,#20506a);border:1px solid #376175;color:#fff;padding:13px 20px;border-radius:15px;margin:8px 0 13px}
.broadcast .eyebrow{font:700 12px Arial;letter-spacing:2px;color:#ffb561}
.broadcast .headline{font:900 clamp(18px,2.6vw,30px) Arial;margin:5px 0}
.broadcast .detail{color:#dae6ec;font:14px Arial}
.pitcher-card{border-radius:15px;background:linear-gradient(125deg,#173349,#294e5a);padding:14px;color:#fff;border:1px solid #3e7280;margin:8px 0}
.pitcher-card .label{letter-spacing:1.6px;color:#ffb868;font:700 11px Arial}
.pitcher-card .name{font:800 21px Arial;margin:6px 0}
.pitcher-card .stats{font:13px Arial;color:#d2e2e6}
.lineup-current{font-weight:800}
</style>""", unsafe_allow_html=True)
API = 'https://statsapi.mlb.com/api/v1'


def number(x, default=0.0):
    try: return float(x)
    except (TypeError, ValueError): return default


def bounded(x, a, b): return max(a, min(b, x))
def throwing(x): return {'R':'RHP','L':'LHP'}.get(x,'?HP')


@st.cache_data(ttl=86400, show_spinner=False)
def fetch(path, params=None):
    response = requests.get(f'{API}/{path}', params=params or {}, timeout=18)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=86400, show_spinner=False)
def team_list(year):
    return sorted(fetch('teams', {'sportId':1,'season':year}).get('teams', []), key=lambda t:t['name'])


@st.cache_data(ttl=86400, show_spinner=False)
def roster(team_id, year):
    return fetch(f'teams/{team_id}/roster', {'rosterType':'fullSeason','season':year}).get('roster',[])


@st.cache_data(ttl=86400, show_spinner=False)
def player(pid, year, group):
    data=fetch(f'people/{pid}')
    info=(data.get('people') or [{}])[0]
    stats=fetch(f'people/{pid}/stats',{'stats':'season','group':group,'season':year,'gameType':'R'})
    s=next((split.get('stat',{}) for section in stats.get('stats',[]) for split in section.get('splits',[]) if split.get('stat')), {})
    return dict(id=pid,name=info.get('fullName','Unknown'),throws=info.get('pitchHand',{}).get('code','?'),bats=info.get('batSide',{}).get('code','?'),stats=s)


@st.cache_data(ttl=86400, show_spinner=False)
def load_squad(team_id, year, group):
    choices=[]
    for item in roster(team_id,year):
        pos=item.get('position',{}).get('abbreviation')
        if (group=='pitching') == (pos in ('P','TWP')):
            pid=item.get('person',{}).get('id')
            if pid: choices.append(pid)
    result=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs=[pool.submit(player,pid,year,group) for pid in choices]
        for job in as_completed(jobs):
            try:
                p=job.result(); s=p['stats']
                if group=='pitching' and number(s.get('gamesPlayed')):
                    starts=number(s.get('gamesStarted')); games=number(s.get('gamesPlayed'))
                    p['role']='SP' if starts >= max(3,games*.35) else 'RP'
                    p['relief_apps']=max(0,games-starts); result.append(p)
                elif group=='hitting' and number(s.get('atBats')):
                    result.append(p)
            except (requests.RequestException, ValueError, KeyError):
                continue
    if group=='pitching':
        return (sorted([p for p in result if p['role']=='SP'],key=lambda p:-number(p['stats'].get('gamesStarted')))[:5]
                +sorted([p for p in result if p['role']=='RP'],key=lambda p:-p['relief_apps'])[:8])
    return sorted(result,key=lambda p:-number(p['stats'].get('plateAppearances'),number(p['stats'].get('atBats'))))[:13]


@st.cache_data(ttl=86400, show_spinner=False)
def handed_splits(player_id, year, group):
    """Observed MLB season split stats against L/R opponent hands.

    Hitting: vl/vs LHP, vr/vs RHP.
    Pitching: vl/vs LHB, vr/vs RHB.
    Missing or unidentified records are left missing.
    """
    result = {}
    for hand_code, situation in (("L", "vl"), ("R", "vr")):
        try:
            payload = fetch(
                f"people/{player_id}/stats",
                {"stats": "statSplits", "group": group,
                 "season": year, "gameType": "R", "sitCodes": situation}
            )
            for section in payload.get("stats", []):
                for entry in section.get("splits", []):
                    values = entry.get("stat") or {}
                    # A single-situation request can still contain
                    # several records: accept only a matching situation
                    # or an unambiguous single-record response.
                    meta = entry.get("split") or {}
                    code = str(entry.get("sitCode") or meta.get("code") or "").lower()
                    description = str(meta.get("description") or "").lower()
                    if code and code not in (situation,):
                        continue
                    if description:
                        if hand_code == "L" and "right" in description:
                            continue
                        if hand_code == "R" and "left" in description:
                            continue
                    if number(values.get("atBats")) > 0:
                        result[hand_code] = values
                        break
                if hand_code in result:
                    break
        except (requests.RequestException, ValueError, KeyError):
            continue
    return result


def observed_batting_split(hitter, pitcher_hand, year):
    if pitcher_hand not in ("L", "R"):
        return None
    return handed_splits(hitter["id"], year, "hitting").get(pitcher_hand)


def observed_pitching_split(pitcher_obj, hitter_hand, year):
    if hitter_hand not in ("L", "R"):
        return None
    return handed_splits(pitcher_obj["id"], year, "pitching").get(hitter_hand)


@st.cache_data(ttl=86400, show_spinner=False)
def h2h(pid, bid, year):
    try:
        data=fetch(f'people/{pid}/stats',{'stats':'vsPlayer','group':'pitching','season':year,'opposingPlayerId':bid,'gameType':'R'})
        for sec in data.get('stats',[]):
            for split in sec.get('splits',[]):
                opp=split.get('opponent') or split.get('player') or {}
                if str(opp.get('id'))==str(bid) and split.get('stat'):
                    return split['stat']
    except (requests.RequestException, KeyError, ValueError): pass
    return None


def starting_game(data, starter, mode):
    staff=data['staff']
    return dict(year=data['year'],team=data['team'],opponent=data['opponent'],staff=staff,
      lineup=data['hitters'][:9].copy(),bench=data['hitters'][9:].copy(),pitcher=starter,
      used=[starter],pitches={p['id']:0 for p in staff},faced={p['id']:0 for p in staff},
      appearance=0,entry_inning=1,warming={},ready=[],inning=1,outs=0,runs=0,
      hits=0,walks=0,bases=[False]*3,spot=0,finished=False,mode=mode,
      log=['PLAY BALL!'],decisions=[],strategy_log=[],strikeouts=0,double_plays=0,
      intentional_walks=0,last_play='Game begins!',play_result='',play_detail='',play_runs=0)


def pitcher(g): return next(p for p in g['staff'] if p['id']==g['pitcher'])
def batter(g): return g['lineup'][g['spot']%9]


def pitcher_limit(p, mode):
    s=p['stats']; starts=number(s.get('gamesStarted')); ip=number(s.get('inningsPitched'))
    if p['role']=='SP': limit=bounded(ip/max(1,starts)*18 if starts else 105,85,140)
    else: limit=bounded(ip/max(1,number(s.get('gamesPlayed'))-starts)*23,20,48)
    return limit*(1.35 if mode=='Rookie' else .90 if mode=='Hall of Fame' else 1)


def fatigue(g,p): return g['pitches'][p['id']]/pitcher_limit(p,g['mode'])
def legal_change(g): return not g['finished'] and (g['appearance']>=3 or g['inning']>g['entry_inning'])


def warm(g,pid):
    if pid in g['used'] or pid in g['ready'] or pid in g['warming']: return
    g['warming'][pid]=0
    p=next(x for x in g['staff'] if x['id']==pid)
    g['log'].append(f"🔥 {p['name']} begins warming.")


def warm_tick(g):
    for pid in list(g['warming']):
        g['warming'][pid]+=1
        if g['warming'][pid]>=2:
            del g['warming'][pid]; g['ready'].append(pid)
            g['log'].append(f"✅ {next(p['name'] for p in g['staff'] if p['id']==pid)} is ready.")


def batter_hit_rates(h):
    s=h['stats']; hits=number(s.get('hits')); ab=number(s.get('atBats'))
    avg=hits/ab if ab else number(s.get('avg'),.250)
    doubles=number(s.get('doubles')); triples=number(s.get('triples')); hr=number(s.get('homeRuns'))
    single=max(0,hits-doubles-triples-hr)
    return bounded(avg,.08,.45), [single,doubles,triples,hr] if hits else [70,19,2,9]


def estimate(g,p,h,approach='Balanced',defense='Normal'):
    """Educational outcome model informed by actual available L/R splits."""
    ps=p['stats']; bf=max(1,number(ps.get('battersFaced'),1))
    avg,_=batter_hit_rates(h)
    actual_h=observed_batting_split(h,p['throws'],g['year'])
    split_ab=number(actual_h.get('atBats')) if actual_h else 0
    if split_ab >= 1:
        # Blend sample-size-sensitive observed split with season rate.
        # Avoid equating 5 AB and 400 AB evidence.
        observed_avg=number(actual_h.get('hits'))/split_ab
        weight=split_ab/(split_ab+90)
        avg=weight*observed_avg+(1-weight)*avg
        hand_adjust=0
    else:
        hand_adjust=-.017 if h['bats']==p['throws'] and h['bats'] in ('L','R') else .010

    tired=max(0,fatigue(g,p)-.65)
    pitching_split=observed_pitching_split(p,h['bats'],g['year'])
    if pitching_split and number(pitching_split.get('battersFaced')) >= 1:
        split_bf=max(1,number(pitching_split.get('battersFaced')))
        split_k=number(pitching_split.get('strikeOuts'))/split_bf
        split_bb=number(pitching_split.get('baseOnBalls'))/split_bf
        season_k=number(ps.get('strikeOuts'))/bf
        season_bb=number(ps.get('baseOnBalls'))/bf
        w=split_bf/(split_bf+90)
        k_rate=w*split_k+(1-w)*season_k
        bb_rate=w*split_bb+(1-w)*season_bb
        # Pitching splits modify strikeout and walk rates, not a
        # fabricated per-batter head-to-head probability.
    else:
        k_rate=number(ps.get('strikeOuts'))/bf
        bb_rate=number(ps.get('baseOnBalls'))/bf
    hp=bounded(avg+hand_adjust+(number(ps.get('whip'),1.3)-1.3)*.045+(number(ps.get('era'),4.2)-4.2)*.006+tired*.065,.08,.48)
    wp=bounded(bb_rate+tired*.03,.025,.18)
    kp=bounded(k_rate-tired*.03,.06,.40)
    if approach=='Attack the zone': hp*=1.12;wp*=.70;kp*=1.20
    elif approach=='Pitch carefully': hp*=.88;wp*=1.70;kp*=.82
    if defense=='Infield in' and g['bases'][2] and g['outs']<2: hp*=1.13
    if defense=='Double-play depth' and g['bases'][0] and g['outs']<2: hp*=1.04
    hp=bounded(hp,.04,.58);wp=bounded(wp,.01,.32);kp=bounded(kp,.02,max(.02,1-hp-wp))
    return hp,wp,kp


def next_hitters(g): return [g['lineup'][(g['spot']+i)%9] for i in range(3)]


def compare_pitchers(g):
    hitters=next_hitters(g)
    options=[pitcher(g)]+[p for p in g['staff'] if p['id'] not in g['used']]
    result=[]
    for p in options:
        probabilities=[estimate(g,p,h) for h in hitters]
        avg_hit=sum(x[0] for x in probabilities)/3
        avg_walk=sum(x[1] for x in probabilities)/3
        avg_k=sum(x[2] for x in probabilities)/3
        # Classroom proxy only. A low value favors run prevention.
        risk=avg_hit+0.7*avg_walk-0.22*avg_k
        pid=p['id']
        state=('ON MOUND' if pid==g['pitcher'] else 'READY' if pid in g['ready']
               else 'WARMING' if pid in g['warming'] else 'COLD')
        result.append(dict(ID=pid,Pitcher=p['name'],Role=p['role'],Hand=throwing(p['throws']),
          Status=state,ERA=p['stats'].get('era','—'),WHIP=p['stats'].get('whip','—'),
          **{'Hit %':round(avg_hit*100,1),'Walk %':round(avg_walk*100,1),
             'K %':round(avg_k*100,1),'Risk proxy':round(risk*100,1)}))
    return sorted(result,key=lambda r:r['Risk proxy'])


def bring_in(g,pid,reason):
    if not legal_change(g) or pid not in g['ready']: return
    old=pitcher(g); new=next(x for x in g['staff'] if x['id']==pid)
    comparisons=compare_pitchers(g)
    old_risk=next(r['Risk proxy'] for r in comparisons if r['ID']==old['id'])
    new_risk=next(r['Risk proxy'] for r in comparisons if r['ID']==pid)
    g['decisions'].append(dict(Inning=g['inning'],Outs=g['outs'],Out=old['name'],In=new['name'],
      Hand=throwing(new['throws']),Reason=reason,**{'Old risk':old_risk,'New risk':new_risk,
      'Improved proxy':new_risk<old_risk,'Fatigue at change':round(fatigue(g,old)*100)}))
    g['pitcher']=pid;g['used'].append(pid);g['ready'].remove(pid);g['appearance']=0;g['entry_inning']=g['inning']
    g['log'].append(f"🔁 {new['name']} ({throwing(new['throws'])}) replaces {old['name']}.")


def pinch_hit(g):
    if g['inning']<7 or not g['bench'] or not (any(g['bases']) or g['inning']>=8):return
    ix=g['spot']%9; old=g['lineup'][ix]; current=pitcher(g)
    replacement=min(g['bench'],key=lambda h:estimate(g,current,h)[0]*-1)
    if estimate(g,current,replacement)[0]>estimate(g,current,old)[0]+.035:
        g['lineup'][ix]=replacement;g['bench'].remove(replacement)
        g['log'].append(f"📣 PINCH HITTER: {replacement['name']} replaces {old['name']}!")


def advance_hit(g,distance):
    old=g['bases'];new=[False]*3;runs=0
    if distance==4:runs=1+sum(old)
    else:
        # Process lead runners first; use a conservative fallback if a base is occupied.
        for i in (2,1,0):
            if not old[i]:continue
            advance=distance
            if distance==1 and i==1 and random.random()<(.72 if g['outs']==2 else .50): advance=2
            if distance==1 and i==0 and random.random()<.23: advance=2
            dest=i+advance
            if dest>=3:runs+=1
            else:
                while dest<3 and new[dest]:dest+=1
                if dest>=3:runs+=1
                else:new[dest]=True
        new[distance-1]=True
    g['bases']=new;g['runs']+=runs;return runs


def walk_bases(g):
    a,b,c=g['bases'];score=int(a and b and c)
    g['bases']=[True,a or b,c or (a and b)];g['runs']+=score;g['walks']+=1
    return score


def after_batter(g):
    g['spot']+=1;warm_tick(g)
    if g['outs']>=3:
        g['log'].append(f"End of inning {g['inning']} — {g['runs']} runs allowed.")
        g['outs']=0;g['bases']=[False]*3;g['inning']+=1
    if g['inning']>9:g['finished']=True


def intentional_walk(g):
    if g['finished'] or all(g['bases']):return
    p=pitcher(g);h=batter(g);scored=walk_bases(g)
    g['faced'][p['id']]+=1;g['appearance']+=1;g['intentional_walks']+=1
    g['strategy_log'].append(dict(Inning=g['inning'],Batter=h['name'],Pitcher=p['name'],
      Approach='Intentional walk',Defense='—',**{'Hit %':'—','Walk %':100}))
    g['play_result']='INTENTIONAL WALK';g['play_detail']=f"{h['name']} takes first base";g['play_runs']=scored
    g['last_play']=f"{p['name']} intentionally walks {h['name']}."+(' A run scores!' if scored else '')
    g['log'].append(g['last_play']);after_batter(g)


def simulate(g,approach,defense):
    if g['finished']:return
    pinch_hit(g);p=pitcher(g);h=batter(g)
    hp,wp,kp=estimate(g,p,h,approach,defense)
    g['strategy_log'].append(dict(Inning=g['inning'],Batter=h['name'],Pitcher=p['name'],
      Approach=approach,Defense=defense,**{'Hit %':round(hp*100,1),'Walk %':round(wp*100,1)}))
    roll=random.random();runs=0
    if roll<wp:runs=walk_bases(g);result='walks'
    elif roll<wp+hp:
        _,weights=batter_hit_rates(h);distance=random.choices([1,2,3,4],weights=weights,k=1)[0]
        runs=advance_hit(g,distance);g['hits']+=1
        result={1:'singles',2:'DOUBLES',3:'TRIPLES',4:'HOMERS'}[distance]
    elif roll<wp+hp+kp:g['outs']+=1;g['strikeouts']+=1;result='strikes out'
    else:
        if g['bases'][0] and g['outs']<2 and random.random()<(.27 if defense=='Double-play depth' else .11 if defense=='Infield in' else .16):
            g['outs']+=2;g['double_plays']+=1;g['bases'][0]=False;result='grounds into a DOUBLE PLAY'
        elif g['bases'][2] and g['outs']<2 and random.random()<(.04 if defense=='Infield in' else .17):
            g['outs']+=1;g['bases'][2]=False;g['runs']+=1;runs=1;result='hits a sacrifice fly'
        else:g['outs']+=1;result='is retired on a ball in play'
    pitches=random.randint(4,9) if approach=='Pitch carefully' else random.randint(2,6) if approach=='Attack the zone' else random.randint(3,8)
    g['pitches'][p['id']]+=pitches;g['faced'][p['id']]+=1;g['appearance']+=1
    g['play_result']={'walks':'WALK','singles':'SINGLE','DOUBLES':'DOUBLE','TRIPLES':'TRIPLE','HOMERS':'HOME RUN!','strikes out':'STRIKEOUT','grounds into a DOUBLE PLAY':'DOUBLE PLAY','hits a sacrifice fly':'SAC FLY','is retired on a ball in play':'OUT'}.get(result,'OUT')
    g['play_detail']=f"{h['name']} vs. {p['name']}";g['play_runs']=runs
    g['last_play']=f"Inning {g['inning']}: {h['name']} {result} vs. {p['name']} ({pitches} pitches)."+(f' {runs} run(s) score!' if runs else '')
    g['log'].append(g['last_play']);after_batter(g)


def danger(g):
    if g['finished']:return None
    if all(g['bases']):return '🚨 BASES LOADED'
    if g['bases'][1] or g['bases'][2]:return '⚠️ RUNNERS IN SCORING POSITION'
    if g['inning']>=7 and any(g['bases']):return '🔥 LATE-INNING PRESSURE'
    if fatigue(g,pitcher(g))>.90:return '🔋 FATIGUE WARNING'
    return None


def diamond(g):
    def marker(x, y, occupied):
        fill = '#ffb33d' if occupied else '#f5e8c9'
        halo = '<circle cx="%s" cy="%s" r="20" fill="#ffb33d" opacity=".2"/>' % (x,y) if occupied else ''
        return halo + f'<rect x="{x-10}" y="{y-10}" width="20" height="20" transform="rotate(45 {x} {y})" fill="{fill}" stroke="#153c35" stroke-width="2"/>'
    a,b,c=g['bases']
    headline=escape(g.get('play_result',''))
    detail=escape(g.get('play_detail',''))
    runs=g.get('play_runs',0)
    ribbon=f'<div class="score">+{runs} RUNS</div>' if runs else ''
    result=(f'<div class="result"><div class="big">{headline}</div><div class="detail">{detail}</div>{ribbon}</div>') if headline else ''
    html=f"""<html><head><style>
    *{{box-sizing:border-box}}body{{margin:0;background:transparent;font-family:Arial,sans-serif;overflow:hidden}}
    .stadium{{position:relative;width:100%;height:360px;overflow:hidden;border-radius:16px;background:#092c29}}
    svg{{width:100%;height:100%;display:block}}
    .result{{position:absolute;left:50%;top:54%;transform:translate(-50%,-50%);width:87%;background:rgba(6,22,35,.92);border:3px solid #ffb139;border-radius:16px;padding:17px;text-align:center;box-shadow:0 15px 30px #0009;animation:pop .38s ease-out}}
    .big{{font:900 clamp(27px,5vw,52px) Arial;color:#fff;letter-spacing:1.5px;text-shadow:0 3px 5px #000}}
    .detail{{font:14px Arial;color:#f3dbaf;margin-top:6px}}.score{{font:900 21px Arial;color:#ffb139;margin-top:8px}}
    @keyframes pop{{from{{opacity:0;transform:translate(-50%,-50%) scale(.8)}}to{{opacity:1;transform:translate(-50%,-50%) scale(1)}}}}
    </style></head><body><div class="stadium">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 550 360" preserveAspectRatio="xMidYMid meet">
      <defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="#082439"/><stop offset="1" stop-color="#145039"/></linearGradient>
      <pattern id="grass" width="48" height="48" patternUnits="userSpaceOnUse" patternTransform="rotate(23)"><rect width="48" height="48" fill="#236b48"/><rect width="24" height="48" fill="#2e7c52"/></pattern></defs>
      <rect width="550" height="360" fill="url(#sky)"/>
      <path d="M0 75 Q275 -18 550 75 L550 360 L0 360Z" fill="url(#grass)"/>
      <path d="M0 75 Q275 -18 550 75" stroke="#e6bf64" stroke-width="7" fill="none"/>
      <path d="M275 330 L86 177 L275 35 L464 177 Z" fill="#c58b55" stroke="#f3d6a6" stroke-width="3"/>
      <path d="M275 296 L122 177 L275 66 L428 177 Z" fill="#287247"/>
      <path d="M275 330 L8 110 M275 330 L542 110" stroke="#f6eed9" stroke-width="2"/>
      {marker(428,177,a)}{marker(275,66,b)}{marker(122,177,c)}{marker(275,324,False)}
      <ellipse cx="275" cy="199" rx="22" ry="13" fill="#c99a69"/>
      <rect x="266" y="197" width="18" height="5" rx="2" fill="#fff"/>
      <text x="15" y="24" fill="#ffcb80" font-size="15" font-weight="800">SPORTS BY THE NUMBERS</text>
      <text x="15" y="46" fill="#fff" font-size="12">SHUTOUT CHALLENGE • LIVE</text>
      <text x="535" y="24" text-anchor="end" fill="#fff" font-size="13">ORANGE = RUNNER ON BASE</text>
    </svg>{result}</div></body></html>"""
    components.html(html,height=368,scrolling=False)

def report_grade(g):
    changes=g['decisions']; evidence=sum(1 for x in changes if x['Improved proxy'])
    timing=sum(1 for x in changes if x['Fatigue at change']<=110)
    # Score never penalizes a well-founded decision solely for the random resulting hit.
    decision_score=round(100*(.5*evidence/max(1,len(changes))+.3*timing/max(1,len(changes))+.2*min(1,len(g['strategy_log'])/27))) if changes else round(20*min(1,len(g['strategy_log'])/27))
    return bounded(decision_score,0,100)


st.title("⚾ YOU'RE THE MANAGER — STADIUM EDITION")
st.caption('SPORTS BY THE NUMBERS  •  DOVER TIGERS  •  2000–2025 MLB  •  Historical player stats / simulated outcomes')
if 'game' not in st.session_state: st.session_state.game=None
if 'loaded' not in st.session_state: st.session_state.loaded=None

with st.expander('⚙️ NEW GAME • Season & matchup', expanded=st.session_state.game is None):
    year=st.selectbox('Season',list(range(2025,1999,-1)))
    try:
        teams={t['name']:t['id'] for t in team_list(year)}
        setup_a,setup_b=st.columns(2)
        own=setup_a.selectbox('Your pitching team',list(teams))
        opp=setup_b.selectbox('Opposing batting team',[x for x in teams if x!=own])
        if st.button('LOAD HISTORICAL ROSTERS',type='primary'):
            st.session_state.loaded=None
            with st.status('Preparing historical teams...',expanded=True) as status:
                progress=st.progress(0)
                st.write('⚾ Loading starters & relievers')
                staff=load_squad(teams[own],year,'pitching')
                progress.progress(50)
                st.write('🏏 Building batting lineup and bench')
                hitters=load_squad(teams[opp],year,'hitting')
                progress.progress(90)
                if staff and len(hitters)>=9:
                    st.session_state.loaded=dict(year=year,team=own,opponent=opp,staff=staff,hitters=hitters)
                    st.session_state.game=None
                    status.update(label='✅ Teams loaded',state='complete',expanded=False)
                else:
                    st.error('Not enough roster data loaded. Try another season or matchup.')
                    status.update(label='Roster incomplete',state='error',expanded=True)
                progress.progress(100)
    except requests.RequestException as err:
        st.error('MLB data unavailable right now.');st.caption(str(err))

loaded=st.session_state.loaded
if loaded and st.session_state.game is None:
    starters=[x for x in loaded['staff'] if x['role']=='SP'] or loaded['staff']
    by_id={p['id']:p for p in starters}
    s1,s2=st.columns(2)
    starter=s1.selectbox('Choose your starting pitcher',list(by_id),format_func=lambda pid:f"{by_id[pid]['name']} • {throwing(by_id[pid]['throws'])} • ERA {by_id[pid]['stats'].get('era','—')}")
    difficulty=s2.selectbox('Difficulty',['Rookie','Pro','Hall of Fame'])
    if st.button('⚾ PLAY BALL!',type='primary'):
        st.session_state.game=starting_game(loaded,starter,difficulty)
        st.rerun()

g=st.session_state.game
if g:
    p=pitcher(g);h=batter(g)
    st.markdown(f'''<div class="broadcast"><div class="eyebrow">LIVE GAME • {g['year']} SEASON • {escape(g['mode'].upper())}</div><div class="headline">{escape(g['team'])} vs. {escape(g['opponent'])}</div><div class="detail">Manage your staff. Protect the shutout. Record 27 outs.</div></div>''',unsafe_allow_html=True)
    metric_cols=st.columns(6)
    for col,label,value in zip(metric_cols,['INNING','OUTS','RUNS ALLOWED','HITS','PITCHES','STRIKEOUTS'],[min(9,g['inning']),g['outs'],g['runs'],g['hits'],g['pitches'][p['id']],g['strikeouts']]):
        col.metric(label,value)
    left,center,right=st.columns([1.05,1.9,1.05],gap='medium')
    with left:
        st.markdown('### 🧢 Your dugout')
        st.markdown(f'''<div class="pitcher-card"><div class="label">ON THE MOUND • {throwing(p['throws'])} • {p['role']}</div><div class="name">{escape(p['name'])}</div><div class="stats">ERA {escape(str(p['stats'].get('era','—')))} &nbsp; | &nbsp; WHIP {escape(str(p['stats'].get('whip','—')))} &nbsp; | &nbsp; Pitches {g['pitches'][p['id']]}</div></div>''',unsafe_allow_html=True)
        st.progress(bounded(fatigue(g,p),0,1),text=f"Fatigue {fatigue(g,p):.0%}")
        with st.expander('Pitching staff • SP / RP',expanded=False):
            status_rows=[]
            for x in g['staff']:
                pid=x['id'];status='MOUND' if pid==g['pitcher'] else 'USED' if pid in g['used'] else 'READY' if pid in g['ready'] else 'WARMING' if pid in g['warming'] else 'AVAILABLE'
                status_rows.append(dict(Pitcher=x['name'],Role=x['role'],Hand=throwing(x['throws']),ERA=x['stats'].get('era','—'),WHIP=x['stats'].get('whip','—'),Status=status))
            st.dataframe(pd.DataFrame(status_rows),hide_index=True,use_container_width=True,height=270)
        if not g['finished']:
            st.markdown('#### 🔥 Interactive bullpen')
            available=[x for x in g['staff'] if x['id'] not in g['used']]
            cold=[x for x in available if x['id'] not in g['warming'] and x['id'] not in g['ready']]
            if cold:
                options={x['id']:x for x in cold}
                pick=st.selectbox('Select reliever',list(options),format_func=lambda pid:f"{options[pid]['name']} • {throwing(options[pid]['throws'])}")
                if st.button('🔥 WARM UP',use_container_width=True):
                    warm(g,pick);st.rerun()
            for pid,progress in g['warming'].items():
                who=next(x['name'] for x in g['staff'] if x['id']==pid)
                st.warning(f'{who}: warming {progress}/2 batters')
            ready=[x for x in available if x['id'] in g['ready']]
            if ready:
                opts={x['id']:x for x in ready}
                incoming=st.selectbox('Ready pitcher',list(opts),format_func=lambda pid:f"{opts[pid]['name']} • {throwing(opts[pid]['throws'])}")
                reason=st.selectbox('Reason',['Fatigue','Handedness matchup','Strikeout ability','Runners on base','Protect shutout'])
                if st.button('🔁 BRING IN RELIEVER',disabled=not legal_change(g),use_container_width=True):
                    bring_in(g,incoming,reason);st.rerun()
            if not legal_change(g): st.caption('Three-batter minimum or end of half-inning.')
    with center:
        st.markdown('### 🏟️ Live stadium')
        diamond(g)
        st.markdown(f'''<div class="pitcher-card"><div class="label">CURRENT MATCHUP</div><div class="name">{escape(h['name'])} <span style="font-size:14px;color:#ffbd70">({escape(h['bats'])} BAT)</span></div><div class="stats">vs. {escape(p['name'])} • {throwing(p['throws'])} • Batter AVG {escape(str(h['stats'].get('avg','—')))}</div></div>''',unsafe_allow_html=True)
        if not g['finished']:
            approach=st.radio('Pitch approach',['Balanced','Attack the zone','Pitch carefully'],horizontal=True,key='pitch_strategy')
            defenses=['Normal']
            if g['bases'][0] and g['outs']<2:defenses.append('Double-play depth')
            if g['bases'][2] and g['outs']<2:defenses.append('Infield in')
            defense=st.selectbox('Defense',defenses)
            action1,action2=st.columns([2,1])
            with action1:
                if st.button('⚾ PITCH TO BATTER',type='primary',use_container_width=True):
                    simulate(g,approach,defense);st.rerun()
            with action2:
                if st.button('🚶 INT. WALK',disabled=all(g['bases']),use_container_width=True):
                    intentional_walk(g);st.rerun()
        else:
            if g['runs']==0:st.success('🏆 SHUTOUT! 27 outs. Zero runs.')
            else:st.info(f"GAME OVER • {g['runs']} runs allowed. Review your decisions below.")
        with st.expander('📻 Radio booth • play-by-play',expanded=False):
            for line in reversed(g['log'][-12:]):st.write(line)
    with right:
        st.markdown('### 🧢 Opposing lineup')
        current=g['spot']%9
        lineup=[]
        for i,x in enumerate(g['lineup']):
            label='🔴' if i==current else '🟡' if i==(current+1)%9 else '⚪' if i==(current+2)%9 else ''
            lineup.append({'#':i+1,'':label,'Batter':x['name'],'Hand':x['bats'],'AVG':x['stats'].get('avg','—')})
        st.dataframe(pd.DataFrame(lineup),hide_index=True,use_container_width=True,height=315)
        st.caption('🔴 At bat  •  🟡 On deck  •  ⚪ In the hole')
        alert=danger(g)
        if alert:st.error(alert)
        elif not g['finished']:st.success('No immediate danger')
        with st.expander('🔍 Scouting • current matchup',expanded=bool(alert)):
            if alert and g['mode']!='Hall of Fame':
                record=h2h(p['id'],h['id'],g['year'])
                if record and record.get('atBats') is not None:
                    st.write(f"Verified H2H: {record.get('hits','—')} hits in {record['atBats']} AB • AVG {record.get('avg','—')}")
                else:st.caption('Verified batter–pitcher history unavailable.')
            st.write(f"**Batter:** {h['name']} | AVG {h['stats'].get('avg','—')}")
            st.write(f"**Pitcher:** {p['name']} | ERA {p['stats'].get('era','—')} | WHIP {p['stats'].get('whip','—')}")
            if not g['finished']:
                st.markdown('**Actual historical batter splits**')
                for ph in ('L','R'):
                    rec=observed_batting_split(h,ph,g['year'])
                    if rec:
                        ab=int(number(rec.get('atBats')));hits=int(number(rec.get('hits')))
                        st.write(f"vs. {throwing(ph)}: **{hits}/{ab} (. {0 if ab==0 else hits/ab:.3f})**".replace('(. ', '('))
                    else:st.caption(f"vs. {throwing(ph)}: unavailable")
                st.caption('Historical splits feed the model when available; simulation rates are estimates.')
    with st.expander('📊 BULLPEN MATCHUP LAB • next three hitters',expanded=bool(danger(g))):
        st.caption('Modeled hit, walk and strikeout chances; NOT official MLB projections.')
        comparisons=compare_pitchers(g)
        if comparisons:
            st.dataframe(pd.DataFrame(comparisons).drop(columns=['ID']),hide_index=True,use_container_width=True)
            st.write('**Up next:** '+' → '.join(f"{x['name']} ({x['bats']})" for x in next_hitters(g)))
            st.info(f"Lowest modeled risk: {comparisons[0]['Pitcher']} ({comparisons[0]['Status']}). Not a guaranteed result.")
    with st.expander('🏆 MANAGER REPORT CARD',expanded=g['finished']):
        st.metric('Decision-process score (illustrative)',f'{report_grade(g)}/100')
        st.caption('Classroom heuristic based on documented reasoning and estimated matchup changes, not a validated measure of manager skill.')
        if g['decisions']:st.dataframe(pd.DataFrame(g['decisions']),hide_index=True,use_container_width=True)
        if g['strategy_log']:st.dataframe(pd.DataFrame(g['strategy_log']).tail(30),hide_index=True,use_container_width=True)
        st.write(f"Runs {g['runs']} • Hits {g['hits']} • Strikeouts {g['strikeouts']} • Double plays {g['double_plays']} • Intentional walks {g['intentional_walks']}")
        if g['finished']:
            st.download_button('Export decisions (CSV)',pd.DataFrame(g['decisions']).to_csv(index=False),'manager_decisions.csv','text/csv')
    if st.button('🔄 START NEW GAME'):
        st.session_state.game=None;st.session_state.loaded=None;st.rerun()
st.caption('Season-level MLB data, with observed L/R splits where available. The lineup is an approximation, and all at-bat outcomes and decision-risk projections are simulated.')
