import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import requests

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

MARGIN_DAYS = 3

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
# DATA FETCH
# ======================
jql = (
    f"({default_jql}) "
    f'AND updated >= "{(date_from - timedelta(days=MARGIN_DAYS)).isoformat()}"'
)

@st.cache_data(ttl=3600)
def search_issues(jql):
    fields = ["summary", "issuetype", "timetracking", "assignee", "status", "parent"]
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
        status = (f.get("status") or {}).get("name", "")
        itype = (f.get("issuetype") or {}).get("name", "")

        wls = get_worklogs(key)
        real = sum((w.get("timeSpentSeconds", 0) or 0) for w in wls) / 3600

        rows.append({
            "Issue": key,
            "Summary": f.get("summary"),
            "Epic": epic,
            "Assignee": assignee,
            "Status": status,
            "Type": itype,
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
    st.stop()

# ======================
# FILTERS (ADVANCED)
# ======================
st.sidebar.subheader("Filtri avanzati")

epics = st.sidebar.multiselect("Epic", sorted(df["Epic"].unique()), default=df["Epic"].unique())
assignees = st.sidebar.multiselect("Assignee", sorted(df["Assignee"].unique()), default=df["Assignee"].unique())
statuses = st.sidebar.multiselect("Stato", sorted(df["Status"].unique()), default=df["Status"].unique())
types = st.sidebar.multiselect("Tipo issue", sorted(df["Type"].unique()), default=df["Type"].unique())

df = df[
    df["Epic"].isin(epics)
    & df["Assignee"].isin(assignees)
    & df["Status"].isin(statuses)
    & df["Type"].isin(types)
]

if df.empty:
    st.warning("Nessun dato con i filtri selezionati")
    st.stop()

# ======================
# METRICS
# ======================
tot_stima = df["Stima"].sum()
tot_ore = df["Ore"].sum()

eff = tot_stima / tot_ore if tot_ore > 0 else 0

c1, c2, c3 = st.columns(3)
c1.metric("Ore stimate", f"{tot_stima:.2f}")
c2.metric("Ore effettive", f"{tot_ore:.2f}")
c3.metric("Efficienza globale", f"{eff*100:.1f}%")

df["Efficienza"] = df["Stima"] / df["Ore"]
df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["Efficienza"])

st.divider()

# ======================
# TABLE
# ======================
st.subheader("Dettaglio efficienza")
st.dataframe(df.sort_values("Efficienza", ascending=False), use_container_width=True)

# ======================
# VISUALS
# ======================

st.subheader("📊 Analisi visuale")

# Scatter Stima vs Effettivo
fig1, ax1 = plt.subplots()
ax1.scatter(df["Stima"], df["Ore"])
ax1.set_xlabel("Stima (h)")
ax1.set_ylabel("Ore effettive (h)")
ax1.set_title("Stima vs Effettivo")
st.pyplot(fig1)

# Distribuzione efficienza
fig2, ax2 = plt.subplots()
ax2.hist(df["Efficienza"], bins=20)
ax2.set_title("Distribuzione Efficienza")
ax2.set_xlabel("Efficienza")
st.pyplot(fig2)

# Top inefficienze
top_bad = df.sort_values("Efficienza").head(10)

fig3, ax3 = plt.subplots()
ax3.barh(top_bad["Issue"], top_bad["Ore"] - top_bad["Stima"])
ax3.set_title("Top sovra-consumo (Ore - Stima)")
st.pyplot(fig3)

# ======================
# DOWNLOAD
# ======================
st.download_button(
    "Download CSV",
    data=df.to_csv(index=False).encode("utf-8"),
    file_name="jira_efficiency.csv",
    mime="text/csv"
)
