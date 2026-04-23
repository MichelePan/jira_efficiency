import streamlit as st
import pandas as pd
import numpy as np

from datetime import date, timedelta
from requests.auth import HTTPBasicAuth

from jira_client import search_issues_jql_v3, get_issue_worklogs_v3

# ======================
# AUTH
# ======================
def check_auth():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.title("Login")

        u = st.text_input("Username")
        p = st.text_input("Password", type="password")

        if st.button("Login"):
            if u == st.secrets["auth"]["username"] and p == st.secrets["auth"]["password"]:
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
st.title("📊 Jira Efficiency Dashboard")

jira_domain = st.secrets["JIRA_DOMAIN"]
email = st.secrets["JIRA_EMAIL"]
api_token = st.secrets["JIRA_API_TOKEN"]

default_jql = st.secrets.get("DEFAULT_JQL", "project = KAN")
EPIC_LINK_FIELD_ID = st.secrets.get("EPIC_LINK_FIELD_ID", None)

BASE_URL = f"https://{jira_domain}/rest/api/3"
AUTH = HTTPBasicAuth(email, api_token)

# ======================
# SIDEBAR FILTERS
# ======================
st.sidebar.header("Filtri")

today = date.today()
date_from = st.sidebar.date_input("Dal", value=today - timedelta(days=30))
date_to = st.sidebar.date_input("Al", value=today)

if date_from > date_to:
    st.error("Range non valido")
    st.stop()

refresh = st.sidebar.button("Reset cache")
if refresh:
    st.cache_data.clear()

# ======================
# DATA FETCH (solo task chiusi)
# ======================
jql = (
    f"({default_jql}) "
    f'AND resolutiondate >= "{date_from.isoformat()}" '
    f'AND resolutiondate <= "{date_to.isoformat()}"'
)

@st.cache_data(ttl=3600)
def search_issues(jql):
    fields = [
        "summary",
        "issuetype",
        "timetracking",
        "assignee",
        "status",
        "parent",
        "resolutiondate"
    ]
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
        assignee = (f.get("assignee") or {}).get("displayName", "")
        resolution_date = f.get("resolutiondate")

        wls = get_worklogs(key)
        real = sum((w.get("timeSpentSeconds", 0) or 0) for w in wls) / 3600

        rows.append({
            "Issue": key,
            "Summary": f.get("summary"),
            "Epic": epic,
            "Assignee": assignee,
            "ResolutionDate": resolution_date,
            "Stima": est,
            "Ore": real
        })

    return pd.DataFrame(rows)


# ======================
# LOAD
# ======================
issues = search_issues(jql)
df = build_df(issues)

if df.empty:
    st.warning("Nessun task completato nel periodo selezionato")
    st.stop()

# ======================
# FILTERS (solo utili)
# ======================
st.sidebar.subheader("Filtri avanzati")

epics = st.sidebar.multiselect(
    "Epic",
    sorted(df["Epic"].unique()),
    default=df["Epic"].unique()
)

assignees = st.sidebar.multiselect(
    "Assignee",
    sorted(df["Assignee"].unique()),
    default=df["Assignee"].unique()
)

df = df[
    df["Epic"].isin(epics)
    & df["Assignee"].isin(assignees)
]

if df.empty:
    st.warning("Nessun dato con i filtri selezionati")
    st.stop()

# ======================
# METRICS
# ======================
tot_stima = df["Stima"].sum()
tot_ore = df["Ore"].sum()

if tot_ore == 0:
    eff = 0
    st.warning("Nessun worklog → efficienza non calcolabile")
else:
    eff = tot_stima / tot_ore

c1, c2, c3 = st.columns(3)
c1.metric("Ore stimate", f"{tot_stima:.2f}")
c2.metric("Ore effettive", f"{tot_ore:.2f}")
c3.metric("Efficienza globale", f"{eff*100:.1f}%")

# ======================
# EFFICIENZA
# ======================
df = df[df["Ore"] > 0].copy()

df["Efficienza"] = df["Stima"] / df["Ore"]
df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["Efficienza"])

st.divider()

# ======================
# TABLE
# ======================
st.subheader("Dettaglio efficienza")
st.dataframe(df.sort_values("Efficienza", ascending=False), use_container_width=True)

# ======================
# DOWNLOAD
# ======================
st.download_button(
    "Download CSV",
    data=df.to_csv(index=False).encode("utf-8"),
    file_name="jira_efficiency.csv",
    mime="text/csv"
)
