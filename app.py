import io
from dataclasses import dataclass
from typing import Dict

import pandas as pd
import streamlit as st


# ======================================================
# PAGE CONFIG
# ======================================================

st.set_page_config(
    page_title="Audit Sampling Tool",
    layout="wide",
    page_icon="📊"
)


# ======================================================
# STYLING (simple professional theme)
# ======================================================

st.markdown("""
<style>
.block-container {padding-top: 1.5rem;}
.stMetric {background-color:#f7f9fc;padding:10px;border-radius:10px;}
</style>
""", unsafe_allow_html=True)


# ======================================================
# DATA STRUCTURE
# ======================================================

@dataclass
class ColumnMapping:
    invoice_number: str
    invoice_date: str
    taxable_amount: str
    ledger_name: str


# ======================================================
# FILE LOADING
# ======================================================

def load_data(uploaded_file):
    if uploaded_file.name.lower().endswith(".csv"):
        return pd.read_csv(uploaded_file)
    return pd.read_excel(uploaded_file, engine="openpyxl")


# ======================================================
# HELPERS
# ======================================================

def normalize_dates(df, date_col):
    temp = df.copy()
    temp[date_col] = pd.to_datetime(temp[date_col], errors="coerce")
    return temp.dropna(subset=[date_col])


def sort_financial_year(df: pd.DataFrame, date_col: str):
    temp = df.copy()
    temp["_date"] = pd.to_datetime(temp[date_col], errors="coerce")
    temp["_fy_month"] = (temp["_date"].dt.month - 4) % 12
    temp["_year"] = temp["_date"].dt.year
    temp = temp.sort_values(["_year", "_fy_month", "_date"])
    return temp.drop(columns=["_date", "_fy_month", "_year"])


def evenly_spread_indices(df, sample_size, date_col):
    if sample_size <= 0 or df.empty:
        return []

    df_sorted = df.sort_values(date_col)

    if sample_size >= len(df_sorted):
        return df_sorted.index.tolist()

    bucket = len(df_sorted) / sample_size
    idx = []

    for i in range(sample_size):
        pos = int((i + 0.5) * bucket)
        pos = min(pos, len(df_sorted) - 1)
        idx.append(df_sorted.index[pos])

    return sorted(set(idx))


# ======================================================
# METHODS
# ======================================================

def method_one(df, mapping):
    indices = []
    for _, g in df.groupby(mapping.ledger_name):
        indices.extend(evenly_spread_indices(g, 1, mapping.invoice_date))
    return indices


def method_two(df, mapping):

    ledgers = sorted(df[mapping.ledger_name].astype(str).unique())

    st.subheader("🎯 Select Specific Ledgers")

    if "selected_ledgers" not in st.session_state:
        st.session_state.selected_ledgers = []

    selected = st.multiselect(
        "Search ledger name",
        ledgers,
        default=st.session_state.selected_ledgers,
        help="Type to filter like Excel search"
    )

    st.session_state.selected_ledgers = selected

    plan = {}
    selected_rows = 0

    for l in selected:
        cnt = len(df[df[mapping.ledger_name] == l])
        selected_rows += cnt

        plan[l] = st.number_input(
            f"{l} (rows: {cnt})",
            0,
            cnt,
            1,
            key=f"s_{l}"
        )

    remaining_rows = len(df) - selected_rows

    st.info(f"Remaining rows: {remaining_rows}")

    remaining_count = st.number_input(
        "Samples for remaining ledgers",
        0,
        remaining_rows,
        min(5, remaining_rows)
    )

    indices = []

    for ledger, g in df.groupby(mapping.ledger_name):
        if ledger in plan:
            indices.extend(evenly_spread_indices(g, plan[ledger], mapping.invoice_date))

    others = df[~df[mapping.ledger_name].isin(plan.keys())]
    indices.extend(evenly_spread_indices(others, remaining_count, mapping.invoice_date))

    return indices


def method_three(df, mapping, total_samples):

    total_rows = len(df)

    if total_samples >= total_rows:
        return df.index.tolist()

    grouped = list(df.groupby(mapping.ledger_name))

    raw = []
    floor = []
    frac = []

    for _, g in grouped:
        val = len(g) * total_samples / total_rows
        floor.append(int(val))
        frac.append(val - int(val))

    remaining = total_samples - sum(floor)

    order = sorted(range(len(frac)), key=lambda i: frac[i], reverse=True)

    for i in order[:remaining]:
        floor[i] += 1

    indices = []

    for (_, g), cnt in zip(grouped, floor):
        indices.extend(evenly_spread_indices(g, cnt, mapping.invoice_date))

    return indices


# ======================================================
# EXCEL EXPORT
# ======================================================

def to_excel(df):
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df.to_excel(w, index=False)
    return bio.getvalue()


# ======================================================
# MAIN UI
# ======================================================

def main():

    st.title("📊 Audit Sampling Tool")
    st.caption("Deterministic • Date-Spread • Audit Safe • No Random Sampling")

    file = st.file_uploader("Upload Excel/CSV file", type=["xlsx", "csv"])

    if not file:
        st.info("Upload a file to start.")
        return

    raw_df = load_data(file)

    # ================= Sidebar =================
    with st.sidebar:
        st.header("⚙️ Settings")

        cols = raw_df.columns.tolist()

        inv = st.selectbox("Invoice Number", cols)
        date = st.selectbox("Invoice Date", cols)
        amt = st.selectbox("Taxable Amount", cols)
        led = st.selectbox("Ledger Name", cols)

        mapping = ColumnMapping(inv, date, amt, led)

        method = st.radio(
            "Sampling Method",
            ["One per ledger", "Specific ledgers", "Proportionate"]
        )

    # ==========================================

    work_df = normalize_dates(raw_df, mapping.invoice_date)

    if method == "One per ledger":
        idx = method_one(work_df, mapping)

    elif method == "Specific ledgers":
        idx = method_two(work_df, mapping)

    else:
        total = st.number_input("Total sample size", 0, len(work_df), 10)
        idx = method_three(work_df, mapping, total)

    sampled = raw_df.loc[idx]
    sampled = sort_financial_year(sampled, mapping.invoice_date)

    # ================= Dashboard Metrics =================

    c1, c2, c3 = st.columns(3)

    c1.metric("Total Rows", len(raw_df))
    c2.metric("Sample Size", len(sampled))
    c3.metric("Coverage %", f"{round(len(sampled)/len(raw_df)*100,2)}%")

    st.divider()

    # ================= Output =================

    st.subheader("📄 Sampled Data")
    st.dataframe(sampled, use_container_width=True, height=500)

    if len(sampled):
        st.download_button(
            "⬇ Download Sample Excel",
            to_excel(sampled),
            "audit_sample.xlsx"
        )


if __name__ == "__main__":
    main()


