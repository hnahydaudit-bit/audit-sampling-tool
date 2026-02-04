import io
from dataclasses import dataclass
from typing import Dict, Optional, List

import pandas as pd
import streamlit as st


# ======================================================
# Data structure
# ======================================================

@dataclass
class ColumnMapping:
    invoice_number: str
    invoice_date: str
    taxable_amount: str
    ledger_name: str


# ======================================================
# File loading
# ======================================================

def load_data(uploaded_file):
    if uploaded_file.name.lower().endswith(".csv"):
        return pd.read_csv(uploaded_file)
    return pd.read_excel(uploaded_file, engine="openpyxl")


# ======================================================
# Column mapping
# ======================================================

def build_mapping(df):
    cols = df.columns.tolist()

    c1, c2 = st.columns(2)

    with c1:
        inv = st.selectbox("Invoice Number", cols)
        date = st.selectbox("Invoice Date", cols)

    with c2:
        amt = st.selectbox("Taxable Amount", cols)
        led = st.selectbox("Ledger / Supplier / Customer", cols)

    if len({inv, date, amt, led}) < 4:
        st.warning("Each mapping must be different.")
        return None

    return ColumnMapping(inv, date, amt, led)


# ======================================================
# Helpers
# ======================================================

def normalize_dates(df, date_col):
    temp = df.copy()
    temp[date_col] = pd.to_datetime(temp[date_col], errors="coerce")
    return temp.dropna(subset=[date_col])


# ======================================================
# ⭐ NEW — Financial Year Sorting (Apr → Mar)
# ======================================================

def sort_financial_year(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """Sort dataframe in Apr → Mar order while keeping original format."""

    temp = df.copy()
    temp["_date"] = pd.to_datetime(temp[date_col], errors="coerce")

    # FY month index
    temp["_fy_month"] = (temp["_date"].dt.month - 4) % 12
    temp["_year"] = temp["_date"].dt.year

    temp = temp.sort_values(["_year", "_fy_month", "_date"])

    return temp.drop(columns=["_date", "_fy_month", "_year"])


# ======================================================
# Deterministic sampling → return indices only
# ======================================================

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
# Method 1
# ======================================================

def method_one(df, mapping):

    indices = []

    for _, g in df.groupby(mapping.ledger_name):
        indices.extend(evenly_spread_indices(g, 1, mapping.invoice_date))

    return indices


# ======================================================
# Method 2 (better filter + counts)
# ======================================================

def method_two(df, mapping):

    ledgers = sorted(df[mapping.ledger_name].astype(str).unique())

    if "selected_ledgers" not in st.session_state:
        st.session_state.selected_ledgers = []

    selected = st.multiselect(
        "Search & select ledgers",
        ledgers,
        default=st.session_state.selected_ledgers
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

    st.markdown(f"**Remaining ledgers rows: {remaining_rows}**")

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


# ======================================================
# Method 3 (exact count fix)
# ======================================================

def method_three(df, mapping, total_samples):

    if total_samples <= 0:
        return []

    total_rows = len(df)

    if total_samples >= total_rows:
        return df.index.tolist()

    grouped = list(df.groupby(mapping.ledger_name))

    raw = []
    floor = []
    frac = []

    for _, g in grouped:
        val = len(g) * total_samples / total_rows
        raw.append(val)
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
# Excel export
# ======================================================

def to_excel(df):
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df.to_excel(w, index=False)
    return bio.getvalue()


# ======================================================
# MAIN
# ======================================================

def main():

    st.set_page_config(layout="wide")
    st.title("Audit Sampling Tool")

    file = st.file_uploader("Upload Excel/CSV", type=["xlsx", "csv"])

    if not file:
        return

    raw_df = load_data(file)

    mapping = build_mapping(raw_df)
    if not mapping:
        return

    work_df = normalize_dates(raw_df, mapping.invoice_date)

    method = st.radio(
        "Method",
        ["One per ledger", "Specific ledgers", "Proportionate"],
        horizontal=True
    )

    if method == "One per ledger":
        idx = method_one(work_df, mapping)

    elif method == "Specific ledgers":
        idx = method_two(work_df, mapping)

    else:
        total = st.number_input("Total sample size", 0, len(work_df), 10)
        idx = method_three(work_df, mapping, total)

    sampled = raw_df.loc[idx]

    # ⭐ APPLY FY SORT
    sampled = sort_financial_year(sampled, mapping.invoice_date)

    st.write("Sample size:", len(sampled))
    st.dataframe(sampled, use_container_width=True)

    if len(sampled):
        st.download_button(
            "Download Sample Excel",
            to_excel(sampled),
            "audit_sample.xlsx"
        )


if __name__ == "__main__":
    main()

