import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="You're the Manager", page_icon="⚾", layout="wide")
st.markdown("""<style>.block-container{max-width:1800px;padding-top:.55rem}h1{color:#f47721}div[data-testid=stMetric]{background:#17364a;padding:8px;border-radius:9px}div[data-testid=stMetric] *{color:white!important}</style>""", unsafe_allow_html=True)
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
      intentional_walks=0,last_play='Game begins!')


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
    # Transparent educational model; NOT measured pitcher/batter matchup probabilities.
    ps=p['stats']; bf=max(1,number(ps.get('battersFaced'),1)); avg,_=batter_hit_rates(h)
    hand_adjust=-.017 if h['bats']==p['throws'] and h['bats'] in ('L','R') else .010
    tired=max(0,fatigue(g,p)-.65)
    hp=bounded(avg+hand_adjust+(number(ps.get('whip'),1.3)-1.3)*.045+(number(ps.get('era'),4.2)-4.2)*.006+tired*.065,.08,.48)
    wp=bounded(number(ps.get('baseOnBalls'))/bf+tired*.03,.025,.18)
    kp=bounded(number(ps.get('strikeOuts'))/bf-tired*.03,.06,.40)
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
    def base(x,y,on):
        fill='#FFBA32' if on else '#FFFFFF'
        return f'<rect x="{x-9}" y="{y-9}" width="18" height="18" transform="rotate(45 {x} {y})" fill="{fill}" stroke="#174832" stroke-width="2"/>'
    a,b,c=g['bases']
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 245" style="width:100%;height:240px"><rect width="400" height="245" rx="13" fill="#16563D"/><path d="M200 222 L63 112 L200 12 L337 112 Z" fill="#BA8C58" stroke="#F0DDB4" stroke-width="3"/><path d="M200 200 L91 112 L200 34 L309 112 Z" fill="#267950"/><path d="M200 222 L20 76 M200 222 L380 76" stroke="white" stroke-width="2"/>{base(302,112,a)}{base(200,29,b)}{base(98,112,c)}{base(200,216,False)}<circle cx="200" cy="124" r="12" fill="#D7B27A"/><rect x="193" y="122" width="14" height="4" fill="white"/><text x="12" y="18" fill="white" font-size="12">Yellow = occupied base</text></svg>'''
    components.html(svg,height=247,scrolling=False)


def report_grade(g):
    changes=g['decisions']; evidence=sum(1 for x in changes if x['Improved proxy'])
    timing=sum(1 for x in changes if x['Fatigue at change']<=110)
    # Score never penalizes a well-founded decision solely for the random resulting hit.
    decision_score=round(100*(.5*evidence/max(1,len(changes))+.3*timing/max(1,len(changes))+.2*min(1,len(g['strategy_log'])/27))) if changes else round(20*min(1,len(g['strategy_log'])/27))
    return bounded(decision_score,0,100)


st.title("⚾ YOU'RE THE MANAGER: SHUTOUT CHALLENGE")
st.caption('Sports by the Numbers • Historical MLB 2000–2025 • Real statistics, simulated baseball')
if 'game' not in st.session_state:st.session_state.game=None
if 'loaded' not in st.session_state:st.session_state.loaded=None

with st.expander('⚙️ Select season and teams',expanded=st.session_state.game is None):
    year=st.selectbox('Season',list(range(2025,1999,-1)))
    try:
        choices={t['name']:t['id'] for t in team_list(year)}
        a,b=st.columns(2);own=a.selectbox('Your pitching team',list(choices));opp=b.selectbox('Opposing batting team',[x for x in choices if x!=own])
        if st.button('LOAD MLB ROSTERS',type='primary'):
            with st.status('Preparing historical matchup...',expanded=True) as status:
                bar=st.progress(0);st.write('⚾ Loading pitching staff...')
                staff=load_squad(choices[own],year,'pitching');bar.progress(50)
                st.write('🏏 Loading hitters and bench...')
                hitters=load_squad(choices[opp],year,'hitting');bar.progress(90)
                if staff and len(hitters)>=9:
                    st.session_state.loaded=dict(year=year,team=own,opponent=opp,staff=staff,hitters=hitters)
                    st.session_state.game=None;st.write('✅ Rosters ready')
                else:st.error('Insufficient historical roster data. Try another season/team.')
                bar.progress(100);status.update(label='Roster request complete',state='complete',expanded=False)
    except requests.RequestException as error:st.error('MLB data temporarily unavailable.');st.caption(str(error))

loaded=st.session_state.loaded
if loaded and st.session_state.game is None:
    starters=[x for x in loaded['staff'] if x['role']=='SP'] or loaded['staff'];byid={p['id']:p for p in starters}
    a,b=st.columns(2)
    chosen=a.selectbox('Starting pitcher',list(byid),format_func=lambda pid:f"{byid[pid]['name']} ({throwing(byid[pid]['throws'])}) — ERA {byid[pid]['stats'].get('era','—')}")
    difficulty=b.selectbox('Difficulty',['Rookie','Pro','Hall of Fame'])
    if st.button('⚾ PLAY BALL!',type='primary'):
        st.session_state.game=starting_game(loaded,chosen,difficulty);st.rerun()

g=st.session_state.game
if g:
    p=pitcher(g);h=batter(g)
    st.subheader(f"{g['year']} {g['team']} vs. {g['opponent']} • {g['mode']}")
    metrics=st.columns(6)
    for col,label,value in zip(metrics,['Inning','Outs','Runs','Hits','Pitches','K'],[min(9,g['inning']),g['outs'],g['runs'],g['hits'],g['pitches'][p['id']],g['strikeouts']]):col.metric(label,value)
    left,center,right=st.columns([1.15,1.32,1.15],gap='medium')
    with left:
        st.markdown('### ⚾ Pitching staff')
        staff_rows=[]
        for x in g['staff']:
            pid=x['id'];status='🔵 MOUND' if pid==g['pitcher'] else 'USED' if pid in g['used'] else '🟢 READY' if pid in g['ready'] else '🟡 WARMING' if pid in g['warming'] else 'AVAILABLE'
            staff_rows.append(dict(Pitcher=x['name'],Role=x['role'],Hand=throwing(x['throws']),ERA=x['stats'].get('era','—'),WHIP=x['stats'].get('whip','—'),Status=status))
        st.dataframe(pd.DataFrame(staff_rows),hide_index=True,use_container_width=True,height=265)
        st.write(f"**On mound:** {p['name']} ({throwing(p['throws'])})")
        st.progress(bounded(fatigue(g,p),0,1),text=f"Fatigue {fatigue(g,p):.0%}")
        if not g['finished']:
            st.markdown('#### 🔥 Bullpen')
            available=[x for x in g['staff'] if x['id'] not in g['used']]
            cold=[x for x in available if x['id'] not in g['warming'] and x['id'] not in g['ready']]
            if cold:
                options={x['id']:x for x in cold}
                pick=st.selectbox('Warm up',list(options),format_func=lambda pid:f"{options[pid]['name']} • {throwing(options[pid]['throws'])}")
                if st.button('🔥 WARM UP'):warm(g,pick);st.rerun()
            for pid,progress in g['warming'].items():st.caption(f"🟡 {next(x['name'] for x in g['staff'] if x['id']==pid)}: {progress}/2 batters")
            ready=[x for x in available if x['id'] in g['ready']]
            if ready:
                options={x['id']:x for x in ready}
                chosen=st.selectbox('Ready pitcher',list(options),format_func=lambda pid:f"{options[pid]['name']} • {throwing(options[pid]['throws'])}")
                reason=st.selectbox('Reason',['Fatigue','Matchup','Strikeout ability','Runners on base','Protect shutout'])
                if st.button('🔁 MAKE CHANGE',disabled=not legal_change(g)):
                    bring_in(g,chosen,reason);st.rerun()
            if not legal_change(g):st.caption('Three-batter minimum, or finish the half-inning.')
    with center:
        st.markdown('### 🏟️ Live diamond');diamond(g)
        st.write(f"**At bat:** {h['name']} ({h['bats']}) | **Pitcher:** {p['name']} ({throwing(p['throws'])})")
        if not g['finished']:
            approach=st.radio('Pitch approach',['Balanced','Attack the zone','Pitch carefully'],horizontal=True)
            defense=['Normal']
            if g['bases'][0] and g['outs']<2:defense.append('Double-play depth')
            if g['bases'][2] and g['outs']<2:defense.append('Infield in')
            alignment=st.selectbox('Defense',defense)
            if not all(g['bases']) and st.button('🚶 Intentional walk'):
                intentional_walk(g);st.rerun()
            if st.button('⚾ PITCH TO BATTER',type='primary',use_container_width=True):simulate(g,approach,alignment);st.rerun()
        else:
            st.success('🏆 SHUTOUT!' if g['runs']==0 else f"Game over: {g['runs']} runs allowed")
        st.info(g['last_play'])
        for note in reversed(g['log'][-5:]):st.caption(note)
    with right:
        st.markdown('### 🧢 Opposing lineup')
        spot=g['spot']%9
        lineup=[{'#':i+1,'Status':'🔴 AT BAT' if i==spot else '🟡 ON DECK' if i==(spot+1)%9 else '⚪ NEXT' if i==(spot+2)%9 else '', 'Batter':x['name'],'Bats':x['bats'],'AVG':x['stats'].get('avg','—')} for i,x in enumerate(g['lineup'])]
        st.dataframe(pd.DataFrame(lineup),hide_index=True,use_container_width=True,height=265)
        st.markdown('### 🧠 Situation room')
        alert=danger(g)
        if alert:st.error(alert)
        else:st.success('No immediate danger')
        if alert and g['mode']!='Hall of Fame':
            record=h2h(p['id'],h['id'],g['year'])
            if record and record.get('atBats') is not None:
                st.write(f"Verified H2H: {record.get('hits','—')} H / {record['atBats']} AB • AVG {record.get('avg','—')}")
            else:st.caption('Verified player-vs-player statistics unavailable; not estimated.')
        st.write(f"Batter AVG: **{h['stats'].get('avg','—')}** • Pitcher ERA: **{p['stats'].get('era','—')}**")
        if alert and g['mode']=='Rookie':st.info('Compare the relievers below, but remember: estimated probabilities are not guarantees.')
    st.markdown('### 📊 Bullpen Matchup Lab')
    st.caption('Estimated outcome rates against the NEXT THREE hitters. These are an educational model using season stats and hand, NOT official historical matchup splits. Lower risk proxy is preferable; it is not an actual chance of allowing a run.')
    comparison=compare_pitchers(g)
    table=pd.DataFrame(comparison).drop(columns=['ID'])
    st.dataframe(table,hide_index=True,use_container_width=True)
    upcoming=' → '.join(f"{x['name']} ({x['bats']})" for x in next_hitters(g))
    st.write(f"**Coming up:** {upcoming}")
    best=comparison[0]
    if not g['finished']:
        st.info(f"Model's lowest-risk option: **{best['Pitcher']}** ({best['Hand']}, {best['Status']}). Consider warmup time, three-batter minimum, and small differences before making a change.")
    with st.expander('📋 Manager Report Card',expanded=g['finished']):
        st.metric('Decision-process score (approximate)',f'{report_grade(g)}/100')
        st.caption('Learning-oriented scoring rewards documented strategies, timely pitching changes, and changes with lower estimated matchup risk. It is not based solely on random game outcomes and is not a validated grade.')
        if g['decisions']:st.dataframe(pd.DataFrame(g['decisions']),hide_index=True,use_container_width=True)
        if g['strategy_log']:st.dataframe(pd.DataFrame(g['strategy_log']).tail(30),hide_index=True,use_container_width=True)
        st.write(f"Runs allowed: {g['runs']} • Hits: {g['hits']} • Ks: {g['strikeouts']} • Double plays: {g['double_plays']} • Intentional walks: {g['intentional_walks']}")
        if g['finished']:
            st.download_button('Download manager decisions CSV',pd.DataFrame(g['decisions']).to_csv(index=False),'manager_decisions.csv','text/csv')
    if st.button('START NEW GAME'):
        st.session_state.game=None;st.session_state.loaded=None;st.rerun()
st.caption('MLB Stats API season statistics; playing situations and matchup probabilities are simulated. Rosters are season-level approximations, not actual single-game lineups.')
