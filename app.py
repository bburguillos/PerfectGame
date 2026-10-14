import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
import json
from io import BytesIO
from datetime import datetime, timezone
from uuid import uuid4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import inch
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
.matchup-tile{background:linear-gradient(135deg,#102a40,#1a4852);color:#fff;border:1px solid #47616d;border-radius:14px;padding:12px;margin:5px 0}
.matchup-tile .eyebrow{color:#ffbd75;font:800 11px Arial;letter-spacing:1.2px}
.matchup-tile .head{font:800 18px Arial;margin:5px 0}
.matchup-tile .data{color:#d3e8ea;font:13px Arial}
.matchup-tile .status{float:right;color:#a5f0ba;font:800 11px Arial}
.compact-note{font:12px Arial;color:#d8e8ea;background:#173447;border-left:4px solid #f58b31;padding:8px 11px;border-radius:6px}


.score-strip{display:flex;justify-content:space-around;align-items:center;gap:8px;background:#15364b;color:#c9e5eb;border-radius:10px;padding:9px 11px;margin:4px 0 12px;font:700 12px Arial;flex-wrap:wrap}
.score-strip b{color:#ffbc75;font-size:20px;margin-left:5px}
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


# Phase 6B: park artwork is an illustrative overhead rendering, not aerial photography.
# Dimensions are simplified styling cues, not official survey measurements.
PARKS = {
    'Boston Red Sox': ('Fenway Park', 'fenway', 1.05),
    'New York Yankees': ('Yankee Stadium', 'short_right', 1.05),
    'Colorado Rockies': ('Coors Field', 'wide', 1.12),
    'Los Angeles Dodgers': ('Dodger Stadium', 'balanced', .98),
    'Chicago Cubs': ('Wrigley Field', 'balanced', 1.02),
    'San Francisco Giants': ('Oracle Park', 'deep_right', .96),
    'Houston Astros': ('Minute Maid Park / Daikin Park', 'short_left', 1.01),
    'New York Mets': ('Citi Field', 'balanced', .98),
    'Baltimore Orioles': ('Oriole Park at Camden Yards', 'deep_left', .99),
    'Pittsburgh Pirates': ('PNC Park', 'wide', .98),
    'San Diego Padres': ('Petco Park', 'wide', .95),
    'Seattle Mariners': ('T-Mobile Park', 'balanced', .96),
    'Arizona Diamondbacks': ('Chase Field', 'balanced', 1.01),
    'Texas Rangers': ('Globe Life Field', 'balanced', 1.00),
    'St. Louis Cardinals': ('Busch Stadium', 'balanced', .98),
    'Philadelphia Phillies': ('Citizens Bank Park', 'balanced', 1.04),
    'Cincinnati Reds': ('Great American Ball Park', 'balanced', 1.07),
    'Milwaukee Brewers': ('American Family Field', 'balanced', 1.01),
    'Cleveland Guardians': ('Progressive Field', 'balanced', .99),
    'Detroit Tigers': ('Comerica Park', 'wide', .97),
    'Minnesota Twins': ('Target Field', 'balanced', 1.00),
    'Kansas City Royals': ('Kauffman Stadium', 'wide', .99),
    'Chicago White Sox': ('Guaranteed Rate Field', 'balanced', 1.01),
    'Atlanta Braves': ('Truist Park', 'balanced', 1.00),
    'Miami Marlins': ('loanDepot park', 'balanced', .97),
    'Tampa Bay Rays': ('Tropicana Field', 'balanced', .97),
    'Toronto Blue Jays': ('Rogers Centre', 'balanced', 1.02),
    'Washington Nationals': ('Nationals Park', 'balanced', .99),
    'Oakland Athletics': ('Oakland Coliseum', 'wide', .95),
    'Athletics': ('Sutter Health Park (temporary)', 'balanced', 1.00),
    'Los Angeles Angels': ('Angel Stadium', 'balanced', .99),
}

def park_info(team, year):
    name, shape, sample_factor = PARKS.get(team, (str(team)+' home park', 'balanced', 1.0))
    # The historical venue for a relocating/renamed franchise can differ.
    if team == 'Athletics' and year < 2025:
        name, shape, sample_factor = 'Oakland Coliseum', 'wide', .95
    if team == 'Houston Astros' and year < 2025:
        name = 'Minute Maid Park'
    if team == 'Miami Marlins' and year < 2021:
        name = 'Marlins Park'
    if team == 'Tampa Bay Rays' and year == 2025:
        name = 'George M. Steinbrenner Field (temporary)'
    return {'name':name, 'shape':shape, 'factor':sample_factor}

@st.cache_data(ttl=86400, show_spinner=False)
def team_defense(team_id,year):
    # MLB team-level fielding data may be absent for some historical seasons.
    # Missing inputs deliberately return an explicit neutral modeled rating.
    try:
        payload=fetch(f'teams/{int(team_id)}/stats', {'stats':'season','group':'fielding','season':year})
        entries=[part.get('stat',{}) for section in payload.get('stats',[])
                 for part in section.get('splits',[]) if part.get('stat')]
        stats=entries[0] if entries else {}
        pct=number(stats.get('fielding'),0)
        if pct<=0:
            pct=number(stats.get('fieldingPercentage'),0)
        if not .95<=pct<=1.0:
            return {'rating':5, 'fielding_pct':None, 'source':'Neutral estimate (MLB team fielding unavailable)'}
        rating=int(bounded(round(5+(.985-pct)*-180),1,10))
        return {'rating':rating,'fielding_pct':pct,'source':'MLB team fielding percentage; 1–10 rating modeled'}
    except (requests.RequestException,ValueError,TypeError,KeyError):
        return {'rating':5,'fielding_pct':None,'source':'Neutral estimate (MLB team fielding unavailable)'}

# Small bounded effects preserve the preexisting hitting/pitching model.
def defense_factor(g):
    rating=number(g.get('team_defense',{}).get('rating'),5)
    return bounded(1.0+(5-rating)*.018,.91,1.09)

def event_alert(g, result, runs, inning_before, outs_before, bases_before):
    alerts=[]
    if result=='grounds into a DOUBLE PLAY' and sum(bool(x) for x in bases_before)>=2:
        alerts.append('🧱 DOUBLE-PLAY ESCAPE — defense turns two under pressure!')
    if all(bases_before) and runs==0 and result in ('strikes out','grounds into a DOUBLE PLAY','is retired on a ball in play'):
        alerts.append('🚨 BASES-LOADED PRESSURE — huge out recorded!')
    if g['strikeouts']>=10 and result=='strikes out' and g['strikeouts']==10:
        alerts.append('🔥 DOUBLE-DIGIT STRIKEOUTS — 10 Ks!')
    if g['hits']==0 and inning_before>=5 and g['outs']==0 and g['inning']>inning_before:
        alerts.append('✨ NO-HITTER WATCH — no hits allowed through '+str(inning_before)+' innings!')
    if g['runs']==0 and inning_before>=7 and g['outs']==0 and g['inning']>inning_before:
        alerts.append('🔒 SHUTOUT WATCH — '+str(inning_before)+' scoreless innings in the books!')
    if g.get('last_inning_ended') is not None:
        completed=g['last_inning_ended']
        inning_r= 'scoreless' if g['runs']==g.get('inning_start_runs',g['runs']) else 'complete'
        alerts.insert(0, f'⚾ END OF INNING {completed} — three outs recorded!')
    if g['finished'] and g['runs']==0:
        alerts.append('🏆 SHUTOUT COMPLETE — 27 outs, zero runs allowed!')
    if alerts:
        g['dramatic_alert']=alerts[-1]
        g['event_history'].extend(alerts)
        g['highlights'].extend(alerts)
    else:
        g['dramatic_alert']=''

def starting_game(data, starter, mode):
    staff=data['staff']
    return dict(year=data['year'],team=data['team'],opponent=data['opponent'],staff=staff,
      lineup=data['hitters'][:9].copy(),bench=data['hitters'][9:].copy(),pitcher=starter,
      used=[starter],pitches={p['id']:0 for p in staff},faced={p['id']:0 for p in staff},
      appearance=0,entry_inning=1,warming={},ready=[],inning=1,outs=0,runs=0,
      hits=0,walks=0,bases=[False]*3,spot=0,finished=False,mode=mode,
      log=['PLAY BALL!'],decisions=[],strategy_log=[],strikeouts=0,double_plays=0,
      intentional_walks=0,last_play='Game begins!',play_result='',play_detail='',play_runs=0,
      prior_bases=[False]*3,base_runners=[None]*3,runner_note='',opponent_moves=[],play_serial=0,highlights=[],achievements=[],
      inning_start_runs=0,inning_reliever=False,jam_escaped=False,
      relief_scoreless=0,relief_inning_outs=0,career_id=str(uuid4()),career_saved=False,
      scouting=data.get('scouting',{}),confidence={x['id']:70 for x in staff},
      mound_visits=5,visit_boost={x['id']:0 for x in staff},confidence_events=[],
      park=data.get('park',park_info(data['team'],data['year'])),
      team_defense=data.get('team_defense',{'rating':5,'source':'Neutral modeled rating'}),
      errors=0,dramatic_alert='',event_history=[],last_inning_ended=None,
      outfield_assists=0,extra_base_attempts=0,basepath_outs=0,play_field='')


def pitcher(g): return next(p for p in g['staff'] if p['id']==g['pitcher'])
def batter(g): return g['lineup'][g['spot']%9]


def pitcher_limit(p, mode):
    s=p['stats']; starts=number(s.get('gamesStarted')); ip=number(s.get('inningsPitched'))
    if p['role']=='SP': limit=bounded(ip/max(1,starts)*18 if starts else 105,85,140)
    else: limit=bounded(ip/max(1,number(s.get('gamesPlayed'))-starts)*23,20,48)
    return limit*(1.35 if mode=='Rookie' else .90 if mode=='Hall of Fame' else 1)


def fatigue(g,p): return g['pitches'][p['id']]/pitcher_limit(p,g['mode'])
def pitcher_confidence(g,p):
    return g.get('confidence',{}).get(p['id'],70)


def mound_visit(g):
    if g['finished'] or g.get('mound_visits',0)<=0:
        return False
    p=pitcher(g)
    g['mound_visits']-=1
    before=pitcher_confidence(g,p)
    g['confidence'][p['id']]=min(95,before+12)
    g['visit_boost'][p['id']]=2
    event=f"Mound visit to {p['name']} in inning {g['inning']}: confidence {before} to {g['confidence'][p['id']]}"
    g['confidence_events'].append(event)
    g['log'].append('🧢 '+event)
    return True


def update_confidence(g,p,result,runs):
    adjustment={'strikes out':6,'grounds into a DOUBLE PLAY':9,
       'walks':-6,'singles':-5,'DOUBLES':-9,'TRIPLES':-12,
       'HOMERS':-16}.get(result,3)
    adjustment-=min(12,runs*5)
    pid=p['id']
    g['confidence'][pid]=int(bounded(pitcher_confidence(g,p)+adjustment,15,95))
    g['visit_boost'][pid]=max(0,g['visit_boost'].get(pid,0)-1)


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
    # Confidence is a clearly simulated effect, separate from workload fatigue.
    confidence=pitcher_confidence(g,p) if p['id']==g['pitcher'] else 70
    confidence_shift=(confidence-70)/100
    hp*=1-.12*confidence_shift
    wp*=1-.24*confidence_shift
    kp*=1+.15*confidence_shift
    if p['id']==g['pitcher'] and g.get('visit_boost',{}).get(p['id'],0)>0:
        wp*=.88
        hp*=.97
    # A modest fielding adjustment changes ball-in-play hit likelihood.
    hp*=defense_factor(g)
    hp*=g.get('park',{}).get('factor',1.0)
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
    g['inning_reliever']=True
    if new_risk < old_risk and 'Matchup Master' not in g['achievements']:
        g['achievements'].append('Matchup Master')


def runner_speed(h):
    """Classroom proxy from historical stolen-base totals, not actual sprint speed."""
    if not h:
        return .50
    stats=h.get('stats',{})
    sb=number(stats.get('stolenBases'))
    cs=number(stats.get('caughtStealing'))
    attempts=sb+cs
    # Small speed boost for frequent, successful base stealers; regress to neutral.
    rate=(sb+3)/(attempts+6)
    return bounded(.36 + min(sb,45)/110 + (rate-.65)*.12, .30, .85)


def lineup_runner(g,base_index):
    # Track actual runner identities when possible. Existing saved games still work.
    identities=g.setdefault('base_runners',[None,None,None])
    return identities[base_index]


def pinch_hit(g):
    """Computer uses bench only when leverage and matchup justify the permanent swap."""
    if g['inning']<7 or not g['bench']:
        return
    ix=g['spot']%9
    old=g['lineup'][ix]
    current=pitcher(g)
    runners=sum(bool(x) for x in g['bases'])
    scoring=g['bases'][1] or g['bases'][2]
    leverage=(g['inning']>=8 and (runners>0 or g['outs']<2)) or scoring
    if not leverage:
        return
    # Current season batting model + matchup, rewarding reaching base and contact.
    def value(h):
        hit,walk,k=estimate(g,current,h)
        stats=h['stats']; ab=max(1,number(stats.get('atBats')))
        extra=(number(stats.get('doubles'))+2*number(stats.get('triples'))+3*number(stats.get('homeRuns')))/ab
        return hit*(1+.55*extra)+walk*.55-k*.12
    old_value=value(old)
    replacement=max(g['bench'],key=value)
    gain=value(replacement)-old_value
    # Preserve bench for more important later moments; no gratuitous changes.
    required=.038 if g['inning']==7 else .028 if g['inning']==8 else .018
    if not scoring:
        required+=.012
    if gain>=required:
        g['lineup'][ix]=replacement
        g['bench'].remove(replacement)
        g.setdefault('opponent_moves',[]).append({
            'inning':g['inning'],'outs':g['outs'],
            'move':'Pinch hitter','in':replacement['name'],'out':old['name'],
            'reason':f'Projected matchup improvement {gain:+.3f}; {runners} runner(s) aboard.'})
        g['log'].append(f"📣 OPPOSING MANAGER: {replacement['name']} pinch hits for {old['name']} — favorable matchup with {runners} runners aboard.")


OUTFIELD_ASSIST_RATE = 0.04  # Classroom assumption PER contested extra-base try, not every hit.


def ball_location(distance, result_kind='hit'):
    """Simulated directions, NOT recorded historical hit locations."""
    if result_kind=='ground':
        return random.choice([('toward third base','third baseman'),('to shortstop','shortstop'),
                              ('toward second base','second baseman'),('to first base','first baseman'),
                              ('back to the mound','pitcher')])
    if result_kind=='popup':
        return random.choice([('behind second base','second baseman'),('in foul territory near first','first baseman'),
                              ('near the third-base dugout','third baseman'),('shallow center field','center fielder')])
    if result_kind=='fly':
        return random.choice([('deep left field','left fielder'),('left-center field','center fielder'),
                              ('deep center field','center fielder'),('right-center field','right fielder'),
                              ('deep right field','right fielder')])
    places=[('down the left-field line','left fielder'),('into left field','left fielder'),
            ('to left-center field','center fielder'),('up the middle into center','center fielder'),
            ('to right-center field','right fielder'),('into right field','right fielder'),
            ('down the right-field line','right fielder')]
    if distance==4: return random.choice(places)
    if distance==3: return random.choice(places[1:5])
    return random.choice(places)


def assist_probability(g,runner,field_position):
    """4% baseline for each attempt; modest speed/defense adjustments."""
    speed=runner_speed(runner)
    rating=number(g.get('team_defense',{}).get('rating'),5)
    adjustment=(1+.045*(rating-5))*(1+.4*(.5-speed))
    return bounded(OUTFIELD_ASSIST_RATE*adjustment,.015,.075)


def advance_hit(g,distance,hitter=None,location=None,thrower=None):
    """Process leading runners first; allow realistic, risky extra-base tries.

    An outfielder gets an assist on approximately 4% of extra-base attempts,
    using the teacher's assumption. An assist produces a real baserunning out.
    All outcomes are illustrative simulations, not historical play-by-play.
    """
    old=g['bases'][:]
    runners=g.setdefault('base_runners',[None,None,None])[:]
    if not any(runners) and any(old):
        runners=[{'name':'Runner','stats':{}} if occupied else None for occupied in old]
    new=[False,False,False]; new_runners=[None,None,None]
    runs=0; notes=[]
    if distance==4:
        runs=1+sum(old)
        g['bases']=new;g['base_runners']=new_runners;g['runs']+=runs
        g['runner_note']='All runners score on the home run.'
        return runs
    if distance==3:
        runs=sum(old)
        new[2]=True;new_runners[2]=hitter
        g['bases']=new;g['base_runners']=new_runners;g['runs']+=runs
        g['runner_note']='All existing runners score on the triple.' if runs else ''
        return runs
    outs=g['outs']; field_rating=number(g.get('team_defense',{}).get('rating'),5)
    thrower=thrower or 'outfielder'
    for i in (2,1,0):
        if not old[i]: continue
        runner=runners[i]; speed=runner_speed(runner)
        runner_name=(runner or {}).get('name','Runner') if isinstance(runner,dict) else 'Runner'
        origin=['first','second','third'][i]
        mandatory= (3 if i>=1 else 2) if distance==2 else i+1
        optional_dest=None; dest=mandatory
        if distance==2 and i==0:
            chance=bounded(.31+.35*speed+(.16 if outs==2 else 0)-.018*(field_rating-5),.20,.83)
            if random.random()<chance: optional_dest=3
        elif distance==1 and i==1:
            chance=bounded(.35+.45*speed+(.16 if outs==2 else 0)-.02*(field_rating-5),.25,.92)
            if random.random()<chance: optional_dest=3
        elif distance==1 and i==0:
            chance=bounded(.08+.40*speed+(.11 if outs==2 else 0)-.018*(field_rating-5),.07,.60)
            if random.random()<chance: optional_dest=2
        # Optional extra-base tries may be cut down. Never send a trailing
        # runner past another runner, and never score on an inning-ending out.
        if optional_dest is not None and g['outs']<3:
            blocked=optional_dest<3 and any(new[j] for j in range(optional_dest,3))
            if not blocked:
                g['extra_base_attempts']=g.get('extra_base_attempts',0)+1
                base_name={2:'third',3:'home'}[optional_dest]
                if random.random()<assist_probability(g,runner,thrower):
                    g['outs']+=1;g['outfield_assists']=g.get('outfield_assists',0)+1
                    g['basepath_outs']=g.get('basepath_outs',0)+1
                    relay=('catcher' if optional_dest==3 else 'third baseman')
                    notes.append(f'OUTFIELD ASSIST! {thrower.title()} throws to the {relay}; {runner_name} is tagged OUT trying for {base_name}!')
                    if g['outs']>=3:
                        notes.append('The throw ends the inning!')
                        # A run that crossed earlier in this play is scored only
                        # if not negated by a third-out force/batter-runner out.
                    continue
                dest=optional_dest
                notes.append(f'{runner_name} takes the extra base and reaches {base_name} safely.')
        # Existing runners must stay in correct order.
        if dest<3:
            while dest>=0 and new[dest]: dest-=1
            if dest<=i: dest=i+1
        if dest>=3:
            runs+=1
            notes.append(f'{runner_name} scores from {origin}.')
        else:
            new[dest]=True;new_runners[dest]=runner
    new[distance-1]=True;new_runners[distance-1]=hitter
    g['bases']=new;g['base_runners']=new_runners;g['runs']+=runs
    g['runner_note']=' '.join(notes)
    return runs


def walk_bases(g):
    a,b,c=g['bases'];score=int(a and b and c)
    oldr=g.setdefault('base_runners',[None,None,None])[:]
    entering=g.get('_plate_hitter')
    g['base_runners']=[entering,oldr[0] if a else oldr[1],oldr[2] if c else oldr[1] if (a and b) else None]
    g['bases']=[True,a or b,c or (a and b)];g['runs']+=score;g['walks']+=1
    g['runner_note']='Bases loaded walk forces in a run.' if score else ''
    return score


def after_batter(g):
    g['spot']+=1;warm_tick(g)
    g['play_serial']+=1
    g['last_inning_ended']=None
    if g['outs']>=3:
        g['last_inning_ended']=g['inning']
        g['log'].append(f"End of inning {g['inning']} — {g['runs']} runs allowed.")
        if g.get('jam_escaped') and g['runs']==g['inning_start_runs'] and 'Escape Artist' not in g['achievements']:
            g['achievements'].append('Escape Artist')
            g['highlights'].append('ESCAPE ARTIST: escaped a scoring-position jam!')
        if g.get('inning_reliever') and g['runs']==g['inning_start_runs']:
            g['relief_scoreless']+=1
            if g['relief_scoreless']>=3 and 'Bullpen Lockdown' not in g['achievements']:
                g['achievements'].append('Bullpen Lockdown')
        elif g['runs']>g['inning_start_runs']:
            g['relief_scoreless']=0
        g['outs']=0;g['bases']=[False]*3;g['base_runners']=[None]*3;g['inning']+=1
        g['inning_start_runs']=g['runs'];g['inning_reliever']=(pitcher(g)['role']=='RP')
        g['jam_escaped']=False
    if g['inning']>9:
        g['finished']=True
        if g['runs']==0 and 'Shutout Legend' not in g['achievements']:
            g['achievements'].append('Shutout Legend')


def intentional_walk(g):
    if g['finished'] or all(g['bases']):return
    g['prior_bases']=g['bases'].copy()
    p=pitcher(g);h=batter(g);g['_plate_hitter']=h;scored=walk_bases(g)
    g['faced'][p['id']]+=1;g['appearance']+=1;g['intentional_walks']+=1
    g['strategy_log'].append(dict(Inning=g['inning'],Batter=h['name'],Pitcher=p['name'],
      Approach='Intentional walk',Defense='—',**{'Hit %':'—','Walk %':100}))
    g['play_result']='INTENTIONAL WALK';g['play_detail']=f"{h['name']} takes first base";g['play_runs']=scored
    g['last_play']=f"{p['name']} intentionally walks {h['name']}."+(' A run scores!' if scored else '')
    g['log'].append(g['last_play']);after_batter(g)


def simulate(g,approach,defense):
    if g['finished']:return
    g['prior_bases']=g['bases'].copy()
    pinch_hit(g);p=pitcher(g);h=batter(g)
    g['_plate_hitter']=h;g['runner_note']=''
    if (g['bases'][1] or g['bases'][2]) and g['outs']<=2:
        g['jam_escaped']=True
    hp,wp,kp=estimate(g,p,h,approach,defense)
    g['strategy_log'].append(dict(Inning=g['inning'],Batter=h['name'],Pitcher=p['name'],
      Approach=approach,Defense=defense,**{'Hit %':round(hp*100,1),'Walk %':round(wp*100,1)}))
    inning_before=g['inning'];outs_before=g['outs'];bases_before=g['bases'].copy()
    roll=random.random();runs=0;action='';location='';fielder=''
    if roll<wp:
        runs=walk_bases(g);result='walks';action=f'{h["name"]} draws a walk.'
    elif roll<wp+hp:
        _,weights=batter_hit_rates(h);distance=random.choices([1,2,3,4],weights=weights,k=1)[0]
        location,fielder=ball_location(distance)
        runs=advance_hit(g,distance,h,location,fielder);g['hits']+=1
        result={1:'singles',2:'DOUBLES',3:'TRIPLES',4:'HOMERS'}[distance]
        hit_type={1:'a single',2:'a double',3:'a triple',4:'a home run'}[distance]
        action=f'{h["name"]} drives {hit_type} {location}. {fielder.title()} fields the ball.' if distance<4 else f'{h["name"]} launches a home run {location}!'
    elif roll<wp+hp+kp:
        g['outs']+=1;g['strikeouts']+=1;result='strikes out'
        action=f'{p["name"]} strikes out {h["name"]}; catcher completes the putout.'
    else:
        if g['bases'][0] and g['outs']<2 and random.random()<(.27 if defense=='Double-play depth' else .11 if defense=='Infield in' else .16):
            location,fielder=ball_location(1,'ground')
            g['outs']+=2;g['double_plays']+=1;g['bases'][0]=False
            g.setdefault('base_runners',[None,None,None])[0]=None
            result='grounds into a DOUBLE PLAY'
            action=f'{h["name"]} grounds {location}. {fielder.title()} starts the double play; the defense gets the force at second and completes the throw to first.'
        elif g['bases'][2] and g['outs']<2 and random.random()<(.04 if defense=='Infield in' else .17):
            location,fielder=ball_location(1,'fly')
            g['outs']+=1;g['bases'][2]=False;g.setdefault('base_runners',[None,None,None])[2]=None
            g['runs']+=1;runs=1;result='hits a sacrifice fly'
            action=f'{h["name"]} flies out {location}. {fielder.title()} makes the catch; the runner tags and scores.'
        else:
            kind=random.choices(['ground','fly','popup'],weights=[49,43,8])[0]
            location,fielder=ball_location(1,kind)
            field_rating=number(g.get('team_defense',{}).get('rating'),5)
            error_chance=bounded(.018+(5-field_rating)*.004,.005,.045)
            if random.random()<error_chance:
                runs=advance_hit(g,1,h,location,fielder)
                g['errors']=g.get('errors',0)+1
                result='reaches on a fielding ERROR'
                action=f'{h["name"]} hits a ball {location}; {fielder} misplays it. Error charged to the {fielder}.'
            else:
                g['outs']+=1
                result='is retired on a ball in play'
                if kind=='ground':
                    action=f'{h["name"]} grounds {location}. {fielder.title()} fields it and throws to first baseman for the putout.'
                else:
                    action=f'{h["name"]} hits a {"pop-up" if kind=="popup" else "fly ball"} {location}. {fielder.title()} makes the catch for the putout.'
    pitches=random.randint(4,9) if approach=='Pitch carefully' else random.randint(2,6) if approach=='Attack the zone' else random.randint(3,8)
    g['pitches'][p['id']]+=pitches;g['faced'][p['id']]+=1;g['appearance']+=1
    update_confidence(g,p,result,runs)
    g['play_result']={'walks':'WALK','singles':'SINGLE','DOUBLES':'DOUBLE','TRIPLES':'TRIPLE','HOMERS':'HOME RUN!','strikes out':'STRIKEOUT','grounds into a DOUBLE PLAY':'DOUBLE PLAY','hits a sacrifice fly':'SAC FLY','is retired on a ball in play':'OUT','reaches on a fielding ERROR':'ERROR'}.get(result,'OUT')
    if 'OUTFIELD ASSIST!' in g.get('runner_note',''):
        g['play_result']='OUTFIELD ASSIST!'
    g['play_field']=action
    g['play_detail']=action+(f' {g["runner_note"]}' if g.get('runner_note') else '')
    g['play_runs']=runs
    g['last_play']=f'Inning {g["inning"]}: {action} ({pitches} pitches).'+(f' {runs} run(s) score!' if runs else '')+((' '+g.get('runner_note','')) if g.get('runner_note') else '')
    g['log'].append(g['last_play'])
    if g['play_result'] in ('HOME RUN!','TRIPLE','DOUBLE','STRIKEOUT','DOUBLE PLAY','OUTFIELD ASSIST!'):
        g['highlights'].append(g['last_play'])
    after_batter(g)
    event_alert(g,result,runs,inning_before,outs_before,bases_before)


def danger(g):
    if g['finished']:return None
    if all(g['bases']):return '🚨 BASES LOADED'
    if g['bases'][1] or g['bases'][2]:return '⚠️ RUNNERS IN SCORING POSITION'
    if g['inning']>=7 and any(g['bases']):return '🔥 LATE-INNING PRESSURE'
    if fatigue(g,pitcher(g))>.90:return '🔋 FATIGUE WARNING'
    return None


# Phase 6B.1 stadium gallery. Geometries are original, schematic illustrations.
# Each park has a separate wall perimeter, grandstand footprint and signature feature.
STADIUM_BLUEPRINTS = {
 'fenway': {'wall':'M18 134 L18 66 L101 66 L101 45 L184 28 L248 15 L338 32 L419 51 L532 98', 'bowl':'M5 166 L5 39 L82 39 L82 15 Q260 -48 438 19 L545 72', 'accent':'#bd3039', 'label':'GREEN MONSTER', 'landmark':'<rect x="18" y="64" width="13" height="89" fill="#17653b" stroke="#9acc9e" stroke-width="2"/><g fill="#c5eed4" font-size="10" font-weight="bold"><text x="36" y="84">GREEN</text><text x="36" y="97">MONSTER</text></g>', 'shape':'LEFT-FIELD WALL'},
 'yankee': {'wall':'M18 112 Q100 28 230 23 Q325 5 392 29 L531 144', 'bowl':'M3 132 Q92 -15 238 3 Q355 -32 424 12 L545 164', 'accent':'#c2c8d8', 'label':'SHORT RIGHT PORCH', 'landmark':'<path d="M386 30 Q472 54 529 143" fill="none" stroke="#f8f5e9" stroke-width="9" stroke-dasharray="6 5"/><text x="388" y="75" font-size="11" fill="white">RF PORCH</text>', 'shape':'ASYMMETRIC RIGHT FIELD'},
 'citi': {'wall':'M16 125 L55 101 L55 65 L152 41 L225 33 L280 10 L360 33 L454 47 L535 120', 'bowl':'M2 148 L33 131 L33 45 L147 22 L223 14 L279 -11 L365 14 L473 25 L548 105', 'accent':'#f58220', 'label':'CITI FIELD', 'landmark':'<rect x="218" y="12" width="94" height="18" rx="5" fill="#124f89"/><text x="265" y="24" text-anchor="middle" font-weight="bold" font-size="11" fill="#fff">METS</text>', 'shape':'STEPPED OUTFIELD'},
 'wrigley': {'wall':'M20 100 L116 57 L201 31 L346 31 L438 53 L531 105', 'bowl':'M6 124 L98 33 L190 10 L360 10 L458 31 L545 129', 'accent':'#b72434', 'label':'IVY WALL / BLEACHERS', 'landmark':'<path d="M20 100 L116 57 L201 31 L346 31 L438 53 L531 105" fill="none" stroke="#46ad58" stroke-width="10" stroke-dasharray="9 3"/><rect x="232" y="6" width="86" height="23" rx="5" fill="#c82936"/><text x="275" y="21" text-anchor="middle" font-size="10" font-weight="bold" fill="white">WRIGLEY</text>', 'shape':'RECTANGULAR BLEACHERS'},
 'dodger': {'wall':'M20 123 Q67 47 157 17 Q275 -28 393 17 Q483 47 530 123', 'bowl':'M4 157 Q31 21 159 -11 Q275 -54 392 -11 Q519 24 546 157', 'accent':'#397bd3', 'label':'CLASSIC SYMMETRIC BOWL', 'landmark':'<path d="M151 18 Q275 -42 398 18" stroke="#86a8ec" stroke-width="15" fill="none"/><text x="275" y="22" font-size="11" text-anchor="middle" fill="white">DODGER BLUE</text>', 'shape':'SYMMETRIC OUTFIELD'},
 'coors': {'wall':'M12 105 L69 65 L145 17 L235 3 L305 2 L403 23 L483 64 L538 104', 'bowl':'M2 142 L43 40 L130 -7 L230 -25 L312 -26 L417 -2 L502 38 L548 142', 'accent':'#7658a1', 'label':'DEEP OUTFIELD / ROCKPILE', 'landmark':'<path d="M219 8 L240 -14 L254 3 L274 -21 L297 6 L315 -9 L341 17 Z" fill="#a6aabb" stroke="#eee" stroke-width="2"/><text x="275" y="49" font-size="11" text-anchor="middle" fill="white">ROCKPILE</text>', 'shape':'SPACIOUS ALLEYS'},
 'oracle': {'wall':'M16 119 L91 70 L174 32 Q270 -8 354 16 L427 59 L471 59 L471 98 L532 130', 'bowl':'M2 145 L72 42 L158 9 Q283 -28 366 -6 L438 31 L486 34 L486 68 L547 103', 'accent':'#ef843d', 'label':'MCCOVEY COVE', 'landmark':'<path d="M470 10 L550 0 L550 130 L471 97 L471 59 Z" fill="#176c9e" opacity=".94"/><g fill="none" stroke="#7cd1f5" opacity=".7"><path d="M493 20 Q522 10 548 21"/><path d="M484 43 Q518 30 546 43"/><path d="M480 65 Q513 53 545 67"/></g><text x="507" y="90" fill="white" font-size="11" text-anchor="middle">COVE</text>', 'shape':'RIGHT FIELD WATERFRONT'},
 'camden': {'wall':'M18 116 L83 68 L139 48 L139 26 L225 16 L309 24 L388 48 L465 66 L531 118', 'bowl':'M3 150 L62 47 L120 30 L120 7 L227 -5 L322 4 L402 25 L491 47 L548 140', 'accent':'#ee7822', 'label':'WAREHOUSE BEYOND RF', 'landmark':'<rect x="433" y="9" width="104" height="33" fill="#b34533" transform="rotate(9 433 9)"/><g stroke="#efb399" stroke-width="2"><path d="M442 12 L438 37 M457 15 L454 40 M472 18 L470 43 M487 20 L485 46 M502 22 L500 48 M517 24 L515 50"/></g><text x="444" y="16" font-size="9" fill="white">WAREHOUSE</text>', 'shape':'CAMDEN WAREHOUSE'},
 'pnc': {'wall':'M19 115 L91 63 L174 25 L278 20 L365 33 L444 66 L531 115', 'bowl':'M3 142 L73 41 L163 1 L279 -4 L380 9 L460 42 L548 141', 'accent':'#f0b934', 'label':'RIVER AND BRIDGE VIEW', 'landmark':'<path d="M72 7 Q250 -20 459 8" stroke="#2997ba" stroke-width="16" fill="none"/><path d="M150 4 L391 4 M164 -1 L164 11 M379 -1 L379 11" stroke="#f4c541" stroke-width="5"/><text x="269" y="32" text-anchor="middle" font-size="11" fill="white">ALLEGHENY</text>', 'shape':'RIVERSIDE PARK'},
 'petco': {'wall':'M20 125 L85 81 L148 35 L245 26 L307 11 L397 48 L475 60 L532 122', 'bowl':'M5 154 L63 60 L125 14 L235 5 L304 -12 L417 26 L494 39 L547 146', 'accent':'#e0bf86', 'label':'WESTERN METAL BUILDING', 'landmark':'<rect x="12" y="43" width="74" height="67" fill="#997e5d" stroke="#dbc5a0" stroke-width="3"/><path d="M20 60 H78 M20 75 H78 M20 90 H78 M35 47 V103 M55 47 V103" stroke="#efd9b0" stroke-width="3"/><text x="18" y="41" font-size="9" fill="white">WESTERN METAL</text>', 'shape':'CITY-BLOCK CORNER'},
}
STADIUM_KEY_BY_NAME = {
    'Fenway Park':'fenway', 'Yankee Stadium':'yankee', 'Citi Field':'citi',
    'Wrigley Field':'wrigley','Dodger Stadium':'dodger','Coors Field':'coors',
    'Oracle Park':'oracle','Oriole Park at Camden Yards':'camden',
    'PNC Park':'pnc','Petco Park':'petco',
}

def fenway_architecture_backdrop():
    """Original stylized Fenway overhead art, not an aerial photograph or survey."""
    parts = [
       '<rect width="550" height="360" fill="#173e3b"/>',
       '<path d="M0 0 H550 V157 L486 112 L418 72 L342 41 L253 31 L167 50 L99 84 L29 134 L0 153 Z" fill="#414b49"/>',
       '<path d="M0 0 H550 V107 L487 87 L417 52 L334 19 L244 9 L145 29 L70 74 L0 109 Z" fill="#686b61"/>',
       '<g fill="#986b53" stroke="#c49c80" stroke-width="1">'
       '<rect x="11" y="4" width="81" height="44" rx="2"/><rect x="455" y="5" width="77" height="41" rx="2"/>'
       '<rect x="96" y="-8" width="52" height="41"/><rect x="373" y="0" width="54" height="29"/></g>',
       '<g stroke="#d6b198" stroke-width="1" opacity=".7">'
       '<path d="M11 14 H92 M11 25 H92 M11 36 H92 M455 17 H532 M455 30 H532"/>'
       '<path d="M25 5 V47 M40 5 V47 M55 5 V47 M70 5 V47 M470 5 V45 M485 5 V45 M500 5 V45 M515 5 V45"/></g>',
       '<path d="M-2 169 L-2 45 Q75 18 126 27 L163 10 Q265 -26 345 12 Q450 22 550 91 L550 179" fill="none" stroke="#0c2528" stroke-width="46"/>',
       '<path d="M0 166 L0 52 Q72 29 124 38 L164 23 Q264 -13 344 23 Q446 34 550 105 L550 178" fill="none" stroke="#a42734" stroke-width="33"/>',
       '<path d="M0 166 L0 52 Q72 29 124 38 L164 23 Q264 -13 344 23 Q446 34 550 105 L550 178" fill="none" stroke="#143b41" stroke-width="26" stroke-dasharray="1 5"/>',
       '<path d="M0 166 L0 52 Q72 29 124 38 L164 23 Q264 -13 344 23 Q446 34 550 105 L550 178" fill="none" stroke="#f0c9b2" stroke-width="2"/>',
       '<g stroke="#9ea6a2" stroke-width="2" opacity=".75">'
       '<path d="M50 32 V135 M480 43 V135 M120 18 V75 M409 22 V73"/></g>',
       '<g fill="#fff3cd"><circle cx="50" cy="32" r="5"/><circle cx="480" cy="43" r="5"/></g>',
       '<g><rect x="56" y="2" width="55" height="24" rx="3" fill="#f1ebdb" stroke="#c62d36" stroke-width="2"/>'
       '<text x="83" y="18" text-anchor="middle" font-size="12" font-weight="bold" fill="#ce3038">CITGO</text></g>'
    ]
    return ''.join(parts)


def fenway_architecture_foreground():
    """Distinctive Fenway features anchored around the existing interactive diamond."""
    return """<g>
       <path d="M18 134 L18 66 L101 66 L101 45 L184 28 L248 15 L338 32 L419 51 L532 98" fill="none" stroke="#0b2728" stroke-width="16" stroke-linejoin="round"/>
       <path d="M18 134 L18 66 L101 66" fill="none" stroke="#17633c" stroke-width="19" stroke-linejoin="miter"/>
       <path d="M18 134 L18 66 L101 66" fill="none" stroke="#62a777" stroke-width="2"/>
       <path d="M101 66 L101 45 L184 28 L248 15 L338 32 L419 51 L532 98" fill="none" stroke="#1c6752" stroke-width="10"/>
       <g transform="translate(17,75)"><rect width="72" height="35" rx="2" fill="#164b33" stroke="#f4dfad"/>
       <text x="36" y="12" text-anchor="middle" font-size="9" font-weight="bold" fill="#fff2c0">GREEN</text>
       <text x="36" y="23" text-anchor="middle" font-size="9" font-weight="bold" fill="#fff2c0">MONSTER</text>
       <text x="36" y="32" text-anchor="middle" font-size="6" fill="#d6e7bc">37 FT WALL</text></g>
       <path d="M105 45 V84" stroke="#fed245" stroke-width="4"/>
       <g transform="translate(283,26)"><rect x="-45" y="-13" width="90" height="23" rx="3" fill="#183d32" stroke="#cdddbb"/>
       <text x="0" y="1" text-anchor="middle" fill="white" font-size="9" font-weight="bold">FENWAY PARK</text></g>
       <g fill="#d8dfcc" opacity=".95"><circle cx="154" cy="44" r="2"/><circle cx="171" cy="40" r="2"/><circle cx="189" cy="37" r="2"/><circle cx="390" cy="51" r="2"/></g>
       <path d="M529 98 L545 110" stroke="#ffe4a4" stroke-width="4"/>
       <g transform="translate(443,83)"><rect width="84" height="17" rx="2" fill="#223d43" stroke="#f4d2a0"/>
       <text x="42" y="11" text-anchor="middle" font-size="9" font-weight="bold" fill="white">PESKY'S POLE →</text></g>
       </g>"""


def diamond(g):
    def marker(x, y, occupied):
        fill = '#ffb33d' if occupied else '#f5e8c9'
        halo = f'<circle cx="{x}" cy="{y}" r="20" fill="#ffb33d" opacity=".2"/>' if occupied else ''
        return halo + f'<rect x="{x-10}" y="{y-10}" width="20" height="20" transform="rotate(45 {x} {y})" fill="{fill}" stroke="#153c35" stroke-width="2"/>'
    park=g.get('park',{})
    park_name=park.get('name','STADIUM')
    stadium_key=STADIUM_KEY_BY_NAME.get(park_name)
    blueprint=STADIUM_BLUEPRINTS.get(stadium_key)
    if blueprint is None:
        blueprint={'wall':'M18 115 Q275 -40 532 115','bowl':'M5 147 Q275 -70 545 147','accent':'#93b4ad','label':'HOME BALLPARK','landmark':'','shape':'CLASSIC BALLPARK'}
    fence=blueprint['wall']
    bowl=blueprint['bowl']
    fenway_mode=stadium_key=='fenway'
    stadium_back=fenway_architecture_backdrop() if fenway_mode else ''
    stadium_front=fenway_architecture_foreground() if fenway_mode else blueprint['landmark']
    a,b,c=g['bases']
    previous=g.get('prior_bases',[False,False,False])
    points=[(428,177),(275,66),(122,177)]
    runner_trails=''
    if g.get('play_result') in ('SINGLE','DOUBLE','TRIPLE','HOME RUN!','WALK','INTENTIONAL WALK'):
        incoming=[points[i] for i,occupied in enumerate(previous) if occupied]
        incoming.append((275,324))
        destinations=[points[i] for i,occupied in enumerate(g['bases']) if occupied]
        destinations += [(275,324)]*(max(0,len(incoming)-len(destinations)))
        for ix,(x,y) in enumerate(incoming):
            tx,ty=destinations[min(ix,len(destinations)-1)]
            runner_trails+=(f'<circle class="moving-runner" cx="{x}" cy="{y}" r="8" fill="#ffbd4e" '
                f'style="--dx:{tx-x}px;--dy:{ty-y}px;animation-delay:{ix*0.13}s"/>')
    headline=escape(g.get('play_result',''))
    detail=escape(g.get('play_detail',''))
    runs=g.get('play_runs',0)
    ribbon=f'<div class="score">+{runs} RUNS</div>' if runs else ''
    inning_end=g.get('last_inning_ended')
    if inning_end is not None:
        ribbon += f'<div class="inning-finish">⚾ END OF INNING {int(inning_end)} <span>· THREE OUTS</span></div>'
        if g.get('finished'):
            ribbon += '<div class="game-finish">FINAL — GAME OVER</div>'
    result=(f'<div class="result"><div class="broadcast-tag">⚾ LAST PLAY</div><div class="big">{headline}</div><div class="detail">{detail}</div>{ribbon}</div>') if headline else ''
    if g.get('dramatic_alert'):
        result+='<div class="event-strip">'+escape(g["dramatic_alert"])+ '</div>'
    hit_result=g.get('play_result','')
    fly_ball=hit_result in ('SINGLE','DOUBLE','TRIPLE','HOME RUN!')
    ball_effect='<div class="ball-flight">⚾</div>' if fly_ball else ''
    result+=ball_effect
    result=result.replace('class="result"', f'class="result {"home-run" if headline=="HOME RUN!" else ""}"')
    html=f"""<html><head><style>
    *{{box-sizing:border-box}}body{{margin:0;background:transparent;font-family:Arial,sans-serif;overflow:hidden}}
    .event-strip{{position:absolute;bottom:5px;left:3%;width:94%;text-align:center;background:#102c43ee;color:#ffc16a;padding:5px;border:1px solid #e9a236;border-radius:7px;font:800 11px Arial;z-index:4}}
    .stadium{{position:relative;width:100%;height:310px;overflow:hidden;border-radius:16px;background:#092c29}}
    .inning-finish{{margin-top:12px;padding:10px 5px;background:#bd3039;border:1px solid #ffdfac;color:white;border-radius:9px;font:900 18px Arial;letter-spacing:1.1px;box-shadow:0 3px 18px #0009}}
    .inning-finish span{{font:700 11px Arial;color:#fff0bf}}
    .game-finish{{font:900 15px Arial;margin-top:8px;color:#f7d68f}}
    svg{{width:100%;height:100%;display:block}}
    .result{{position:absolute;left:50%;top:54%;transform:translate(-50%,-50%);width:87%;background:rgba(6,22,35,.92);border:3px solid #ffb139;border-radius:16px;padding:17px;text-align:center;box-shadow:0 15px 30px #0009;animation:pop .38s ease-out, fadeAway .35s ease-in 3.2s forwards;z-index:5}}
    .broadcast-tag{{color:#ffb33d;font:800 10px Arial;letter-spacing:2px;margin-bottom:6px}}
    .moving-runner{{filter:drop-shadow(0px 1px 3px #000);animation:runbase 1.45s ease-in-out both;transform-box:fill-box;transform-origin:center}}
    @keyframes runbase{{0%{{opacity:0;transform:translate(0,0) scale(.6)}}20%{{opacity:1}}85%{{opacity:1}}100%{{opacity:0;transform:translate(var(--dx),var(--dy)) scale(.8)}}}}
    .home-run{{border-color:#ffe06c;box-shadow:0 0 45px #e69e2699;animation:pop .38s ease-out,fadeAway .35s ease-in 3.2s forwards}}
    .ball-flight{{position:absolute;left:48%;top:73%;font-size:26px;pointer-events:none;opacity:0;animation:flight 1.8s ease-in-out .15s forwards}}
    @keyframes flight{{0%{{opacity:0;transform:translate(0,0) scale(.5)}}20%{{opacity:.9}}90%{{opacity:.7}}100%{{opacity:0;transform:translate(105px,-185px) scale(.25)}}}}
    @keyframes fadeAway{{to{{opacity:0;visibility:hidden}}}}
    .big{{font:900 clamp(27px,5vw,52px) Arial;color:#fff;letter-spacing:1.5px;text-shadow:0 3px 5px #000}}
    .detail{{font:14px Arial;color:#f3dbaf;margin-top:6px}}.score{{font:900 21px Arial;color:#ffb139;margin-top:8px}}
    @keyframes pop{{from{{opacity:0;transform:translate(-50%,-50%) scale(.8)}}to{{opacity:1;transform:translate(-50%,-50%) scale(1)}}}}
    </style></head><body><div class="stadium">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 550 360" preserveAspectRatio="xMidYMid meet">
      <defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="#082439"/><stop offset="1" stop-color="#145039"/></linearGradient>
      <pattern id="grass" width="48" height="48" patternUnits="userSpaceOnUse" patternTransform="rotate(23)"><rect width="48" height="48" fill="#236b48"/><rect width="24" height="48" fill="#2e7c52"/></pattern>
      <pattern id="seats" width="11" height="11" patternUnits="userSpaceOnUse"><rect width="11" height="11" fill="#233f51"/><path d="M0 4 H11 M4 0 V11" stroke="{blueprint['accent']}" opacity=".65" stroke-width="1.5"/></pattern></defs>
      <rect width="550" height="360" fill="url(#sky)"/>
      <path d="{bowl} L548 178 L2 178 Z" fill="url(#seats)" stroke="{blueprint['accent']}" stroke-width="7"/>
      <path d="{bowl}" fill="none" stroke="#8294a0" stroke-width="20" opacity=".6"/>
      <path d="{bowl}" fill="none" stroke="#132331" stroke-width="3" stroke-dasharray="4 8"/>
      <path d="{fence} L550 360 L0 360Z" fill="url(#grass)"/>
      <path d="{fence}" stroke="#f1d18a" stroke-width="7" fill="none"/>
      {stadium_front}
      <path d="M275 330 L86 177 L275 35 L464 177 Z" fill="#c58b55" stroke="#f3d6a6" stroke-width="3"/>
      <path d="M275 296 L122 177 L275 66 L428 177 Z" fill="#287247"/>
      <path d="M275 330 L8 110 M275 330 L542 110" stroke="#f6eed9" stroke-width="2"/>
      {marker(428,177,a)}{marker(275,66,b)}{marker(122,177,c)}{marker(275,324,False)}{runner_trails}
      <ellipse cx="275" cy="199" rx="22" ry="13" fill="#c99a69"/>
      <rect x="266" y="197" width="18" height="5" rx="2" fill="#fff"/>
      <rect x="8" y="7" width="218" height="45" rx="8" fill="#071c28" opacity=".96"/>
      <text x="17" y="24" fill="#ffcb80" font-size="12" font-weight="800">{escape(park_name.upper())}</text>
      <text x="17" y="41" fill="#fff" font-size="10">{escape(blueprint['label'])} · ILLUSTRATED AERIAL</text>
      <rect x="388" y="7" width="152" height="30" rx="6" fill="#071c28" opacity=".92"/>
      <text x="534" y="26" text-anchor="end" fill="#ffc978" font-size="11">{escape(blueprint['shape'])}</text>
    </svg>{result}</div></body></html>"""
    components.html(html,height=316,scrolling=False)

def player_headshot(pid):
    # Official MLB image CDN; missing photos are hidden by the browser.
    return f"https://img.mlbstatic.com/mlb-photos/image/upload/w_180,q_auto:good/v1/people/{int(pid)}/headshot/67/current"


def portrait_card(p, kicker, subline=''):
    src=player_headshot(p['id'])
    return (f'<div class="face-card"><img src="{src}" alt="" onerror="this.style.display=\'none\'"/>'
            f'<div><div class="face-kicker">{escape(kicker)}</div>'
            f'<div class="face-name">{escape(p["name"])}</div>'
            f'<div class="face-sub">{escape(subline)}</div></div></div>')


def report_grade(g):
    changes=g['decisions']; evidence=sum(1 for x in changes if x['Improved proxy'])
    timing=sum(1 for x in changes if x['Fatigue at change']<=110)
    # Score never penalizes a well-founded decision solely for the random resulting hit.
    decision_score=round(100*(.5*evidence/max(1,len(changes))+.3*timing/max(1,len(changes))+.2*min(1,len(g['strategy_log'])/27))) if changes else round(20*min(1,len(g['strategy_log'])/27))
    return bounded(decision_score,0,100)



# ==========================================
# CAREER MODE AND PDF REPORT
# ==========================================

def clean_manager_name(name):
    return (str(name).strip()[:45] or 'Manager')


def grade_letter(score):
    return 'A' if score >= 90 else 'B' if score >= 80 else 'C' if score >= 70 else 'D' if score >= 60 else 'F'


def manager_assessment(g):
    """Transparent classroom rubric: decisions matter more than simulated luck."""
    changes = g.get('decisions', [])
    actions = g.get('strategy_log', [])
    total_pa = max(1, len(actions))
    meaningful = sum(1 for a in actions if a.get('Approach') not in ('Balanced', None) or a.get('Defense') not in ('Normal', None, '—'))
    # Strategy choices are evidence of active management, not automatically 'correct'.
    engagement = min(20, round(16 * min(1, total_pa / 27) + 4 * min(1, meaningful / 6)))
    # No bullpen change is not automatically a failure; baseline credit applies.
    if changes:
        good_matchups = sum(bool(x.get('Improved proxy')) for x in changes)
        timely = sum(number(x.get('Fatigue at change')) <= 110 for x in changes)
        bullpen = round(10 + 12 * good_matchups / len(changes) + 8 * timely / len(changes))
    else:
        bullpen = 24
    bullpen = min(30, bullpen)
    # Recognize reading the situation, without claiming to judge intent from a dropdown.
    dangerous_actions = sum(1 for a in actions if a.get('Approach') in ('Pitch carefully', 'Attack the zone', 'Intentional walk') or a.get('Defense') in ('Infield in','Double-play depth'))
    strategy = min(30, round(18 + 12 * min(1, dangerous_actions / 7)))
    # Runs permitted carry only 20% of the final score.
    outcome = max(0, 20 - min(20, int(g.get('runs',0)) * 3))
    parts = {'Engagement': engagement, 'Bullpen decisions': bullpen,
             'Situational strategy': strategy, 'Run prevention': outcome}
    score = min(100, max(0, sum(parts.values())))
    notes = []
    if not changes:
        notes.append('No pitching changes: the rubric awards baseline bullpen credit instead of assuming a mistake.')
    elif any(x.get('Improved proxy') for x in changes):
        notes.append('At least one pitching change improved the modelled matchup-risk index.')
    else:
        notes.append('Pitching changes did not lower the modelled matchup-risk index; discuss the reasons for those choices.')
    if g.get('runs',0) == 0:
        notes.append('Shutout achieved. Remember: a good decision can still lead to a hit, and luck matters.')
    else:
        notes.append('Runs allowed affect just 20% of the grade, so decisions matter more than random results.')
    scout=g.get('scouting',{})
    if scout:
        threats=set(scout.get('threat_ids',[]))
        top_three={x['id'] for x in sorted(g['lineup'],key=lambda x:(number(x['stats'].get('ops')),number(x['stats'].get('homeRuns'))),reverse=True)[:3]}
        overlap=len(threats & top_three)
        notes.append(f"Pregame scouting: {overlap} of 3 selected threats matched the top three lineup hitters by the selected statistical ranking. This is feedback, not a guarantee of game outcomes.")
        notes.append(f"Pregame plan: {scout.get('plan','Not recorded')}. Mound visits used: {5-g.get('mound_visits',5)}.")
    return {'score':score,'letter':grade_letter(score),'parts':parts,'notes':notes}


def career_record(g, name):
    assessment = manager_assessment(g)
    return {
        'id':g.get('career_id') or str(uuid4()),
        'date_utc':datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'manager':clean_manager_name(name), 'season':int(g['year']),
        'team':str(g['team']), 'opponent':str(g['opponent']),
        'difficulty':str(g['mode']), 'runs':int(g['runs']),
        'hits':int(g['hits']), 'strikeouts':int(g['strikeouts']),
        'shutout':g['runs']==0, 'errors':g.get('errors',0),
        'park':g.get('park',{}).get('name',''),
        'defense_rating':g.get('team_defense',{}).get('rating',5),
        'grade':assessment['score'], 'letter':assessment['letter'],
        'rubric':assessment['parts'],
        'achievements':list(g.get('achievements',[])),
        'pitching_changes':len(g.get('decisions',[])),
        'strategies':len(g.get('strategy_log',[])),
        'scouting':g.get('scouting',{}),'mound_visits_used':5-g.get('mound_visits',5)
    }


def career_stats(records):
    if not records:
        return {'Games':0,'Shutouts':0,'Avg grade':'—','Best grade':'—'}
    return {'Games':len(records),
            'Shutouts':sum(bool(x.get('shutout')) for x in records),
            'Avg grade':f"{sum(int(x.get('grade',0)) for x in records)/len(records):.1f}",
            'Best grade':max(int(x.get('grade',0)) for x in records)}


def pdf_escape(value):
    return escape(str(value))


def make_game_pdf(g, name):
    assessment = manager_assessment(g)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf,pagesize=(612,792),rightMargin=38,leftMargin=38,
                            topMargin=38,bottomMargin=38)
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Brand',parent=styles['Heading1'],fontName='Helvetica-Bold',
                              fontSize=19,leading=23,textColor=colors.HexColor('#E67727'),spaceAfter=5))
    styles.add(ParagraphStyle(name='SmallCustom',parent=styles['Normal'],fontSize=9,leading=13,spaceAfter=6))
    styles.add(ParagraphStyle(name='SubtitleCustom',parent=styles['Heading2'],fontSize=12,
                              textColor=colors.HexColor('#14374A'),spaceBefore=10,spaceAfter=7))
    story=[Paragraph('SPORTS BY THE NUMBERS | MANAGER REPORT',styles['Brand']),
           Paragraph(f"Manager: <b>{pdf_escape(clean_manager_name(name))}</b>  |  "
                     f"Game: <b>{pdf_escape(g['year'])} {pdf_escape(g['team'])} vs. {pdf_escape(g['opponent'])}</b>  |  "
                     f"Mode: {pdf_escape(g['mode'])}",styles['SmallCustom']),
           Spacer(1,8)]
    header=Table([[f"{assessment['score']}/100", f"GRADE {assessment['letter']}",
                   'SHUTOUT' if g['runs']==0 else f"{g['runs']} RUNS ALLOWED"]],
                 colWidths=[170,170,170],rowHeights=[44])
    header.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#14374A')),
                                ('TEXTCOLOR',(0,0),(-1,-1),colors.white),
                                ('FONTNAME',(0,0),(-1,-1),'Helvetica-Bold'),
                                ('FONTSIZE',(0,0),(-1,-1),14),
                                ('ALIGN',(0,0),(-1,-1),'CENTER'),
                                ('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
    story += [header,Spacer(1,12),Paragraph('How the grade was calculated',styles['SubtitleCustom'])]
    rubric=[['Category','Points earned','Maximum']]
    for k,v in assessment['parts'].items():
        rubric.append([k,str(v),str({'Engagement':20,'Bullpen decisions':30,
                                     'Situational strategy':30,'Run prevention':20}[k])])
    t=Table(rubric,colWidths=[280,115,115],hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E7EEF2')),
                           ('GRID',(0,0),(-1,-1),.35,colors.HexColor('#B3C5CC')),
                           ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
                           ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F8FAFB')]),
                           ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    story += [t,Spacer(1,8)]
    for note in assessment['notes']:
        story.append(Paragraph('• '+pdf_escape(note),styles['SmallCustom']))
    scout=g.get('scouting',{})
    if scout:
        story.append(Paragraph('Pregame scouting plan',styles['SubtitleCustom']))
        story.append(Paragraph('Plan: '+pdf_escape(scout.get('plan','Not recorded')),styles['SmallCustom']))
        names={p['id']:p['name'] for p in g['lineup']}
        selected=', '.join(names.get(pid,str(pid)) for pid in scout.get('threat_ids',[]))
        story.append(Paragraph('Selected threats: '+pdf_escape(selected),styles['SmallCustom']))
        story.append(Paragraph('Starter rationale: '+pdf_escape(scout.get('rationale','')),styles['SmallCustom']))
        story.append(Paragraph(f"Mound visits used: {5-g.get('mound_visits',5)}",styles['SmallCustom']))
    story.append(Paragraph('Ballpark and defense',styles['SubtitleCustom']))
    story.append(Paragraph('Park: '+pdf_escape(g.get('park',{}).get('name','Unknown'))+' | Defensive rating: '+str(g.get('team_defense',{}).get('rating',5))+'/10 | Errors: '+str(g.get('errors',0)),styles['SmallCustom']))
    story += [Paragraph('Game statistics',styles['SubtitleCustom']),
              Paragraph(f"Hits allowed: <b>{g['hits']}</b> | Strikeouts: <b>{g['strikeouts']}</b> | "
                        f"Double plays: <b>{g['double_plays']}</b> | Intentional walks: <b>{g['intentional_walks']}</b> | "
                        f"Pitching changes: <b>{len(g['decisions'])}</b>",styles['SmallCustom'])]
    if g['achievements']:
        story.append(Paragraph('Achievements: '+pdf_escape(', '.join(g['achievements'])),styles['SmallCustom']))
    story.append(Paragraph('Evidence from managerial decisions',styles['SubtitleCustom']))
    if g['decisions']:
        for decision in g['decisions'][:7]:
            story.append(Paragraph(
                f"Inning {pdf_escape(decision.get('Inning','?'))}: "
                f"{pdf_escape(decision.get('Out','?'))} to {pdf_escape(decision.get('In','?'))}. "
                f"Reason: {pdf_escape(decision.get('Reason','—'))}. "
                f"Modelled risk index: {pdf_escape(decision.get('Old risk','—'))} to "
                f"{pdf_escape(decision.get('New risk','—'))}.",styles['SmallCustom']))
    else:
        story.append(Paragraph('No pitching substitutions were made.',styles['SmallCustom']))
    story.append(Paragraph('Major plays and dramatic moments',styles['SubtitleCustom']))
    for highlight in g.get('highlights',[])[-4:]:
        story.append(Paragraph('• '+pdf_escape(highlight),styles['SmallCustom']))
    story += [Paragraph('Student reflection (complete after the game)',styles['SubtitleCustom'])]
    for q in ['1. Which managerial decision mattered most? Explain using at least two statistics.',
              '2. Did you make a good decision that produced an unlucky outcome? Explain.',
              '3. What would you change in your next game?']:
        story.append(Paragraph(pdf_escape(q),styles['SmallCustom']))
        story.append(Paragraph('_'*96,styles['SmallCustom']))
    story.append(Spacer(1,5))
    story.append(Paragraph('This is an instructional rubric, not a validated estimate of MLB managerial skill. '
                           'Historical player statistics inform an educational simulation; outcomes and risk indices are modelled.',
                           styles['SmallCustom']))
    doc.build(story)
    return buf.getvalue()


def safe_career_import(raw):
    data=json.loads(raw)
    if not isinstance(data,dict) or data.get('format')!='sbtn-career-v1':
        raise ValueError("This is not a compatible career file.")
    records=data.get('games',[])
    if not isinstance(records,list) or len(records)>2000:
        raise ValueError('Invalid game history.')
    cleaned=[]
    for record in records:
        if not isinstance(record,dict) or not record.get('id'):
            continue
        cleaned.append({k:v for k,v in record.items() if k in {
            'id','date_utc','manager','season','team','opponent','difficulty',
            'runs','hits','strikeouts','shutout','grade','letter','rubric',
            'achievements','pitching_changes','strategies'}})
    return clean_manager_name(data.get('manager','Manager')),cleaned


st.title("⚾ YOU'RE THE MANAGER · STADIUM EDITION 6B.4")
st.caption('SPORTS BY THE NUMBERS  •  DOVER TIGERS  •  2000–2025 MLB  •  Historical player stats / simulated outcomes')
if 'game' not in st.session_state: st.session_state.game=None
if 'loaded' not in st.session_state: st.session_state.loaded=None
if 'selected_relief_id' not in st.session_state: st.session_state.selected_relief_id=None
if 'career_games' not in st.session_state: st.session_state.career_games=[]
if 'career_name' not in st.session_state: st.session_state.career_name='Manager'

# Record completed games once, even on Streamlit reruns.
active_game=st.session_state.game
if active_game and active_game.get('finished') and not active_game.get('career_saved',False):
    record=career_record(active_game,st.session_state.career_name)
    ids={x.get('id') for x in st.session_state.career_games}
    if record['id'] not in ids:
        st.session_state.career_games.append(record)
    active_game['career_saved']=True

with st.expander('🏆 CAREER MANAGER • Profile, history, and backups',expanded=st.session_state.game is None):
    st.text_input('Manager name / student identifier',key='career_name',max_chars=45,
                  help='Avoid full student names if career files will be shared.')
    summary=career_stats(st.session_state.career_games)
    mini=st.columns(4)
    for col,(label,value) in zip(mini,summary.items()):col.metric(label,value)
    if st.session_state.career_games:
        st.dataframe(pd.DataFrame(st.session_state.career_games)[
            ['date_utc','season','team','opponent','difficulty','runs','grade','letter','shutout']
        ],hide_index=True,use_container_width=True,height=200)
    career_payload={'format':'sbtn-career-v1','manager':clean_manager_name(st.session_state.career_name),
                    'games':st.session_state.career_games}
    st.download_button('💾 SAVE CAREER FILE (JSON)',
        data=json.dumps(career_payload,indent=2),file_name='manager_career.json',
        mime='application/json',key='download_career')
    uploaded=st.file_uploader('Load a previously saved career file',type=['json'],key='career_upload')
    if uploaded is not None and st.button('📂 IMPORT / MERGE CAREER',key='import_career'):
        try:
            imported_name,imported_records=safe_career_import(uploaded.getvalue().decode('utf-8'))
            existing={x.get('id') for x in st.session_state.career_games}
            count=0
            for item in imported_records:
                if item['id'] not in existing:
                    st.session_state.career_games.append(item)
                    existing.add(item['id'])
                    count+=1
            # Keep the current manager name; it can be edited in the profile field.
            st.success(f'Imported {count} previously unrecorded games.')
            st.rerun()
        except (ValueError,UnicodeError,TypeError,KeyError) as error:
            st.error(f'Career import failed: {error}')
    st.caption('Career progress lives in this browser session until you export it. '
               'For future visits or different devices, download the JSON and import it next time. '
               'Streamlit Community Cloud does not guarantee persistent local storage.')


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
                    st.session_state.loaded=dict(year=year,team=own,opponent=opp,staff=staff,hitters=hitters,
                       park=park_info(own,year),team_defense=team_defense(teams[own],year))
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
    st.markdown('#### 🕵️ Pregame Scouting Room')
    st.caption('Rankings use real season statistics. Predictions and managerial tactics remain your choices.')
    scout_hitters=loaded['hitters'][:9]
    scout_rows=[]
    for player_obj in scout_hitters:
        stats=player_obj['stats']
        scout_rows.append({'Hitter':player_obj['name'],'Bats':player_obj['bats'],
          'AVG':stats.get('avg','—'),'OBP':stats.get('obp','—'),
          'OPS':stats.get('ops','—'),'HR':int(number(stats.get('homeRuns')))})
    st.dataframe(pd.DataFrame(scout_rows),hide_index=True,use_container_width=True,height=235)
    ids={x['id']:x for x in scout_hitters}
    threats=st.multiselect('Pick the THREE hitters most dangerous to your shutout',
       options=list(ids),max_selections=3,format_func=lambda pid:ids[pid]['name'])
    plan=st.selectbox('Pregame strategy',[
       'Attack hitters early', 'Pitch carefully to power hitters',
       'Lean on ground-ball defense', 'Prepare the bullpen early'])
    rationale=st.text_input('Why did you choose this starter? (one statistical reason)',
       max_chars=180,placeholder='Example: Low WHIP and strong strikeout rate')
    if st.button('⚾ PLAY BALL!',type='primary',disabled=len(threats)!=3 or not rationale.strip()):
        loaded['scouting']={'threat_ids':threats,'plan':plan,'rationale':rationale.strip(),
                           'starter_id':starter}
        st.session_state.game=starting_game(loaded,starter,difficulty)
        st.session_state.selected_relief_id=None
        st.rerun()

g=st.session_state.game
if g:
    p=pitcher(g)
    h=batter(g)
    st.markdown(f"**{g['year']} {g['team']} vs. {g['opponent']}** · {g['mode']} · Shutout Challenge")
    if g.get('dramatic_alert'):
        st.markdown(f"<div class='broadcast'><div class='eyebrow'>LIVE GAME ALERT</div><div class='headline'>{escape(g['dramatic_alert'])}</div></div>",unsafe_allow_html=True)
    # One single-row compact scoreboard, instead of six large metric cards.
    scoreboard=(f"<div class='score-strip'>"
                f"<span>INNING <b>{min(9,g['inning'])}</b></span>"
                f"<span>OUTS <b>{g['outs']}</b></span>"
                f"<span>RUNS <b>{g['runs']}</b></span>"
                f"<span>HITS <b>{g['hits']}</b></span>"
                f"<span>PITCHES <b>{g['pitches'][p['id']]}</b></span>"
                f"<span>K <b>{g['strikeouts']}</b></span></div>")
    st.markdown(scoreboard,unsafe_allow_html=True)
    left,center,right=st.columns([1.25,2.65,1.35],gap='small')
    available=[x for x in g['staff'] if x['id'] not in g['used']]
    available_ids={x['id']:x for x in available}
    if st.session_state.selected_relief_id not in available_ids:
        st.session_state.selected_relief_id=available[0]['id'] if available else None
    selected_id=st.session_state.selected_relief_id
    selected=available_ids.get(selected_id)
    with left:
        st.markdown('##### 🔥 BULLPEN')
        st.caption(f"🧤 Team defense {g.get('team_defense',{}).get('rating',5)}/10 · {g.get('team_defense',{}).get('source','Estimated')}")
        st.markdown(f"**{escape(p['name'])}** · {throwing(p['throws'])} · {p['role']}")
        st.caption(f"ON MOUND · ERA {p['stats'].get('era','—')} · WHIP {p['stats'].get('whip','—')}")
        st.progress(bounded(fatigue(g,p),0,1),text=f"Fatigue {fatigue(g,p):.0%}")
        st.progress(pitcher_confidence(g,p)/100,text=f"Confidence {pitcher_confidence(g,p)} / 100 (simulated)")
        if not g['finished']:
            if st.button(f"🧢 MOUND VISIT · {g.get('mound_visits',5)} left",key='side_mound_visit',
                         use_container_width=True,disabled=g.get('mound_visits',0)<=0):
                mound_visit(g)
                st.rerun()
            if g.get('visit_boost',{}).get(p['id'],0):
                st.caption('Visit boost active: reduced modeled hit/walk risk for the next 2 batters')

        if available and not g['finished']:
            st.caption('Tap a pitcher to scout or manage')
            # Exactly four immediately visible compact card buttons; additional pitchers via selectbox.
            for x in available[:4]:
                pid=x['id']
                state='🟢 READY' if pid in g['ready'] else f"🟡 {g['warming'][pid]}/2" if pid in g['warming'] else '⚪ COLD'
                label=f"{'▶ ' if pid==selected_id else ''}{x['name']} · {throwing(x['throws'])} · {state}"
                if st.button(label,key=f'side_pitcher_{pid}',use_container_width=True,type='primary' if pid==selected_id else 'secondary'):
                    st.session_state.selected_relief_id=pid
                    st.rerun()
            if len(available)>4:
                extra_lookup={x['id']:x for x in available[4:]}
                extra_options=[None]+list(extra_lookup)
                extra_id=st.selectbox('More relievers',extra_options,format_func=lambda pid:'Choose another pitcher' if pid is None else f"{extra_lookup[pid]['name']} ({throwing(extra_lookup[pid]['throws'])})",key='side_more_relief')
                if extra_id is not None and st.button('Select additional pitcher',key='side_select_more',use_container_width=True):
                    st.session_state.selected_relief_id=extra_id
                    st.rerun()
            if selected:
                pid=selected['id']
                st.markdown(f"**Selected:** {escape(selected['name'])}")
                st.caption(f"{selected['role']} · {throwing(selected['throws'])} · ERA {selected['stats'].get('era','—')} · WHIP {selected['stats'].get('whip','—')}")
                if pid in g['warming']:
                    st.warning(f"Warming: {g['warming'][pid]}/2 batters")
                elif pid in g['ready']:
                    reason=st.selectbox('Reason',['Better matchup','Fatigue','Handedness advantage','Strikeout ability','Prevent runs'],key='side_reason')
                    if st.button('🔁 BRING IN',disabled=not legal_change(g),type='primary',use_container_width=True,key='side_bring'):
                        bring_in(g,pid,reason)
                        st.session_state.selected_relief_id=None
                        st.rerun()
                    if not legal_change(g):st.caption('Three-batter minimum applies')
                else:
                    if st.button('🔥 WARM UP',use_container_width=True,key='side_warm'):
                        warm(g,pid)
                        st.rerun()
        elif not available: st.caption('No unused pitchers remain')
    with center:
        st.markdown(f"**⚾ {escape(h['name'])}** · {escape(h['bats'])} BAT · AVG {h['stats'].get('avg','—')}")
        if not g['finished']:
            approach=st.radio('Pitch approach',['Balanced','Attack the zone','Pitch carefully'],horizontal=True,key='pitch_strategy',label_visibility='collapsed')
            defenses=['Normal']
            if g['bases'][0] and g['outs']<2:defenses.append('Double-play depth')
            if g['bases'][2] and g['outs']<2:defenses.append('Infield in')
            dcol,icol=st.columns([1.4,1])
            with dcol: defense=st.selectbox('Defense',defenses,key='side_defense')
            with icol:
                if st.button('🚶 INTENTIONAL WALK',disabled=all(g['bases']),use_container_width=True,key='side_iw'):
                    intentional_walk(g)
                    st.rerun()
            if st.button('⚾ PITCH TO BATTER',type='primary',use_container_width=True,key='side_pitch'):
                simulate(g,approach,defense)
                st.rerun()
        else:
            st.success('🏆 SHUTOUT!' if g['runs']==0 else f"FINAL · {g['runs']} runs allowed")
        st.caption(f"🏟️ {g.get('park',{}).get('name','Stadium')} · stylized overhead · modeled park factor {g.get('park',{}).get('factor',1):.2f}×")
        diamond(g)
        if g.get('last_play') and g['last_play']!='Game begins!':
            st.caption('📻 '+g['last_play'])
    with right:
        st.markdown('##### 🧢 NEXT BATTERS')
        for i,x in enumerate(next_hitters(g)):
            marker=['🔴','🟡','⚪'][i]
            st.markdown(f"{marker} **{escape(x['name'])}** · {escape(x['bats'])} · {x['stats'].get('avg','—')}")
        alert=danger(g)
        if alert:st.warning(alert)
        st.markdown('##### 📊 MATCHUP')
        if selected and not g['finished']:
            current_probs=[estimate(g,p,b) for b in next_hitters(g)]
            selected_probs=[estimate(g,selected,b) for b in next_hitters(g)]
            current_hit=sum(x[0] for x in current_probs)/3*100
            reliever_hit=sum(x[0] for x in selected_probs)/3*100
            current_k=sum(x[2] for x in current_probs)/3*100
            reliever_k=sum(x[2] for x in selected_probs)/3*100
            st.markdown(f"**Current:** {current_hit:.1f}% hit · {current_k:.1f}% K")
            st.markdown(f"**{escape(selected['name'])}:** {reliever_hit:.1f}% hit · {reliever_k:.1f}% K")
            change=reliever_hit-current_hit
            st.caption(f"{abs(change):.1f} percentage points {'lower' if change<0 else 'higher' if change>0 else 'different'} modeled hit risk with selected reliever")
            if selected_id in g['ready']:st.caption('🟢 Reliever ready')
            elif selected_id in g['warming']:st.caption('🟡 Warming up')
            else:st.caption('⚪ Must warm up')
        else:
            st.caption('Select a reliever for a quick comparison.')
        st.caption('Modeled estimates; not MLB forecasts.')
        st.caption(f"🎯 Outfield assists: {g.get('outfield_assists',0)} / {g.get('extra_base_attempts',0)} contested extra-base tries · 4% baseline per try")
    # Only optional, nonessential details below the main game.
    history_tab,scout_tab,lab_tab,report_tab=st.tabs(['📻 Play-by-play','🔍 Historical splits','📊 Full Matchup Lab','🏆 Manager Report'])
    with history_tab:
        for line in reversed(g['log'][-20:]):st.write(line)
    with scout_tab:
        st.markdown(f"**Batter:** {h['name']} · **Pitcher:** {p['name']}")
        for side in ('L','R'):
            rec=observed_batting_split(h,side,g['year'])
            if rec:
                ab=int(number(rec.get('atBats')));hits=int(number(rec.get('hits')))
                st.write(f"Against {throwing(side)}: {hits}/{ab} ({hits/ab:.3f})" if ab else f"Against {throwing(side)}: no AB")
            else:st.write(f'Against {throwing(side)}: unavailable')
        if danger(g) and g['mode']!='Hall of Fame':
            record=h2h(p['id'],h['id'],g['year'])
            if record:st.write(f"Verified head-to-head: {record.get('hits','—')} hits in {record.get('atBats','—')} AB")
            else:st.caption('Verified head-to-head unavailable.')
    with lab_tab:
        comparisons=compare_pitchers(g)
        if comparisons:
            st.dataframe(pd.DataFrame(comparisons).drop(columns=['ID']),hide_index=True,use_container_width=True)
            if selected:
                rows=[]
                for b in next_hitters(g):
                    cp=estimate(g,p,b);rp=estimate(g,selected,b)
                    rows.append({'Batter':b['name'],'Current hit %':round(cp[0]*100,1),'Reliever hit %':round(rp[0]*100,1),'Current K %':round(cp[2]*100,1),'Reliever K %':round(rp[2]*100,1)})
                st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
            st.caption('Lower risk proxy is better in this educational model; it is not probability of runs.')
    with report_tab:
        assessment=manager_assessment(g)
        st.metric('MANAGER GRADE',f"{assessment['letter']} · {assessment['score']}/100")
        st.caption('Rubric: engagement 20, bullpen decisions 30, situational strategy 30, run prevention 20. Outcomes have only 20% weight.')
        st.dataframe(pd.DataFrame([{'Category':k,'Points':v} for k,v in assessment['parts'].items()]),hide_index=True,use_container_width=True)
        for note in assessment['notes']:st.caption('• '+note)
        for achievement in g['achievements']:st.success('🏅 '+achievement)
        if g.get('event_history'):
            st.markdown('#### 🎙️ Dramatic game moments')
            for event in reversed(g['event_history'][-12:]):st.write(event)
        if g['highlights']:
            st.markdown('#### Broadcast highlights')
            for highlight in reversed(g['highlights'][-8:]):st.write(highlight)
        if g['decisions']:st.dataframe(pd.DataFrame(g['decisions']),hide_index=True,use_container_width=True)
        if g['strategy_log']:st.dataframe(pd.DataFrame(g['strategy_log']).tail(30),hide_index=True,use_container_width=True)
        st.caption('Educational grading heuristic, not an objective measure of management skill.')
        if g['finished']:
            st.download_button('📄 DOWNLOAD POSTGAME PDF + GRADE',
                data=make_game_pdf(g,st.session_state.career_name),
                file_name=f"manager_report_{g['year']}_{g['career_id'][:8]}.pdf",
                mime='application/pdf',key='game_pdf')
            st.caption('Your completed game is added to the career summary automatically. Save the career JSON above to keep it across visits.')
            st.download_button('Export pitching decisions',pd.DataFrame(g['decisions']).to_csv(index=False),'manager_decisions.csv','text/csv')
    if st.button('🔄 START NEW GAME'):
        st.session_state.game=None
        st.session_state.loaded=None
        st.session_state.selected_relief_id=None
        st.rerun()
st.caption('Real MLB historical season statistics; game results, stamina and matchup projections are simulated.')
