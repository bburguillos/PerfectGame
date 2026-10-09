
import streamlit as st

st.set_page_config(
    page_title="You're the Manager",
    page_icon="⚾",
    layout="wide"
)

st.title("⚾ SPORTS BY THE NUMBERS")
st.subheader("YOU'RE THE MANAGER: SHUTOUT CHALLENGE")

st.markdown(
    "### Your Mission: Get 27 Outs Without Allowing a Run!"
)

st.info(
    "Use real baseball statistics, manage your bullpen, "
    "and make decisions like an MLB manager."
)

teams = [
    "New York Mets",
    "New York Yankees",
    "Los Angeles Dodgers",
    "Philadelphia Phillies",
    "Atlanta Braves",
    "Boston Red Sox",
    "Chicago Cubs",
    "San Diego Padres"
]

col1, col2 = st.columns(2)

with col1:
    your_team = st.selectbox("Choose Your Team", teams)

with col2:
    opponent_options = [
        team for team in teams if team != your_team
    ]
    opponent = st.selectbox(
        "Choose Your Opponent", opponent_options
    )

st.divider()
st.subheader("⚾ Game Dashboard")

c1, c2, c3, c4 = st.columns(4)

c1.metric("Inning", "1")
c2.metric("Outs", "0")
c3.metric("Runs Allowed", "0")
c4.metric("Outs Remaining", "27")

st.divider()

if st.button("⚾ START SHUTOUT CHALLENGE"):
    st.session_state["started"] = True

if st.session_state.get("started", False):
    st.success("You're in the dugout, Manager!")
    st.write(f"Your team: {your_team}")
    st.write(f"Opponent: {opponent}")
    st.warning(
        "Next up: Choosing your starting pitcher "
        "and building your bullpen!"
    )
