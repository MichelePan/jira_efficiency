import streamlit as st
import pandas as pd
from datetime import date, timedelta
from requests.auth import HTTPBasicAuth
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

from jira_client import search_issues_jql_v3, get_issue_worklogs_v3

# ======================
# AUTH
# ======================
def check_auth():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.title("Login")

        username = st.text_input("Username")
        password = st.text_input("Password", type="password")

        if st.button("Login"):
            if (
                username == st.secrets["auth"]["username"]
                and password == st.secrets["auth"]["password"]
            ):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Credenziali non valide")

        st.stop()

check_auth()

# ======================
# CONFIG
# ======================
st.set_page_config(page_title="Jira Efficiency", layout="wide")
st.title("Jira Efficiency Dashboard")

jira_domain = st.secrets["JIRA_DOMAIN"]
email = st.secrets["JIRA_EMAIL"]
api_token = st.secrets["JIRA_API_TOKEN"]
default_jql = st.secrets.get("DEFAULT_JQL", "project = KAN")
EPIC_LINK_FIELD_ID = st.secrets.get("EPIC_LINK_FIELD_ID", None)

BASE_URL = f"https://{jira_domain}/rest/api/3"
AUTH = HTTPBasicAuth(email, api_token)

MARGIN_DAYS = 3

# ======================
# SIDEBAR
# ======================
st.sidebar.header("Filtri")

today = date.today()
date_from = st.sidebar.date_input("Dal", value=today - timedelta(days=30))
date_to = st.sidebar.date_input("Al", value=today)

if date_from > date_to:
    st.error("Intervallo non valido")
    st.stop()

refresh = st.sidebar.button("Aggiorna cache")
if refresh:
    st.cache_data.clear()

# ======================
# DATA FETCH
# ======================
jql = (
    f"({default_jql}) "
    f'AND updated >= "{(date_from - timedelta(days=MARGIN_DAYS)).isoformat()}"'
)

@st.cache_data(ttl=3600)
def search_issues(jql):
    fields = ["summary", "issuetype", "timetracking", "assignee", "parent"]
    if EPIC_LINK_FIELD_ID:
        fields.append(EPIC_LINK_FIELD_ID)

    return search_issues_jql_v3(BASE_URL, AUTH, jql, fields)


@st.cache_data(ttl=3600)
def get_worklogs(key):
    return get_issue_worklogs_v3(BASE_URL, AUTH, key)


def estimate_hours(fields):
    tt = fields.get("timetracking") or {}
    sec = tt.get("originalEstimateSeconds") or fields.get("timeoriginalestimate") or 0
    return sec / 3600


def extract_epic(fields):
    if EPIC_LINK_FIELD_ID:
        v = fields.get(EPIC_LINK_FIELD_ID)
        if isinstance(v, str):
            return v
    parent = fields.get("parent") or {}
    return parent.get("key", "")


def build_df(issues):
    rows = []

    for i in issues:
        key = i["key"]
        f = i["fields"]

        est = estimate_hours(f)
        epic = extract_epic(f)

        wls = get_worklogs(key)

        total = sum((w.get("timeSpentSeconds", 0) or 0) for w in wls) / 3600

        rows.append({
            "Issue": key,
            "Summary": f.get("summary"),
            "Epic": epic,
            "Stima": est,
            "Ore": total
        })

    return pd.DataFrame(rows)


# ======================
# LOAD
# ======================
issues = search_issues(jql)
df = build_df(issues)

if df.empty:
    st.stop()

# ======================
# EPIC FILTER (CORE FEATURE)
# ======================
epics = sorted(df["Epic"].dropna().unique().tolist())
selected_epics = st.sidebar.multiselect("Epic da includere", epics, default=epics)

df = df[df["Epic"].isin(selected_epics)]

if df.empty:
    st.warning("Nessun dato con questi filtri")
    st.stop()

# ======================
# KPI
# ======================
tot_stima = df["Stima"].sum()
tot_ore = df["Ore"].sum()

eff_glob = tot_stima / tot_ore if tot_ore > 0 else 0

c1, c2, c3 = st.columns(3)
c1.metric("Ore stimate", f"{tot_stima:.2f}")
c2.metric("Ore effettive", f"{tot_ore:.2f}")
c3.metric("Efficienza", f"{eff_glob*100:.1f}%")

st.divider()

# ======================
# TABLE
# ======================
df["Efficienza"] = df["Stima"] / df["Ore"]
df["Efficienza %"] = (df["Efficienza"] * 100).round(1)

df = df.sort_values("Efficienza", ascending=False)

st.subheader("Efficienza per issue")

st.dataframe(df, use_container_width=True)

# ======================
# DOWNLOAD
# ======================
st.download_button(
    "Download CSV",
    data=df.to_csv(index=False).encode("utf-8"),
    file_name="jira_efficiency.csv",
    mime="text/csv"
)
