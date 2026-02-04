import io
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd
import streamlit as st


# ===============================
# Config
# ===============================

REQUIRED_FIELDS = [
    "Invoice Number",
    "Invoice Date",
    "Taxable Amount",
    "Ledger Name",
]


# ===============================
# Data Structures
# ===============================

@dataclass
class ColumnMapping:
    invoice_number: str
    invoice_date: str
    taxable_amount: str
    ledger_name: str


# ===============================
# File Loading
# ===============================

def load_data(uploaded_file) -> pd.DataFrame:
    """Load CSV or Excel file."""
    if uploaded_file.name.lower().endswith(".csv"):
        return pd.read_csv(uploaded_file)
    return pd.read_excel(uploaded_file, engine="openpyxl")


# ===============================
# Column Mapping UI
# ===============================

def build_mapping(df: pd.DataFrame) -> Optional[ColumnMapping]:
    st.subheader("Column Mapping")

    cols = df.columns.tolist()

    col1, col2 = st.columns(2)

    with col1:
        invoice_number = st.selectbox("Invoice Number column", cols)
        invoice_date = st.selectbox("Invoice Date column", cols)

    with col2:
        taxable_amount = st.selectbox("Taxable Amount column", cols)
        ledger_name = st.selectbox("Ledger / Supplier / Customer column", cols)

    if len({invoice_number, invoice_date, taxable_amount, ledger_name}) < 4:
        st.warning("Each field must map to a different column.")
        return None

    return ColumnMapping(
        invoice_number,
        invoice_date,
        taxable_amount,
        ledger_name,
    )


# ===============================
# Helpers
# ===============================

def normalize_dates(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    return df


# =====================================================
# CORE LOGIC → Evenly distributed date-based sampling
# =====================================================

def select_evenly_distributed(df: pd.DataFrame, sample_size: int, date_col: str) -> pd.DataFrame:
    """
    Deterministic sampling across time buckets.
    NO randomness.
    Ensures spread across months/quarters.
    """

    if df.empty or sample_size <= 0:
        return df.head(0)

    df_sorted = df.sort_values(date_col).reset_index(drop=True)

    if sample_size >= len(df_sorted):
        return df_sorted

    bucket_size = len(df_sorted) / sample_size
    indices = []

    for i in range(sample_size):
        pos = int((i + 0.5) * bucket_size)
        pos = min(pos, len(df_sorted) - 1)
        indices.append(pos)

    return df_sorted.iloc[sorted(set(indices))]


# ===============================
# Method 1
# ===============================

def one_per_ledger(df: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    frames = []

    for _, ledger_df in df.groupby(mapping.ledger_name):
        frames.append(select_evenly_distributed(ledger_df, 1, mapping.invoice_date))

    return pd.concat(frames, ignore_index=True) if frames else df.head(0)


# ===============================
# Method 2
# ===============================

def specific_ledger_sampling(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    ledger_plan: Dict[str, int],
    remaining_count: int,
) -> pd.DataFrame:

    frames = []
    others = []

    for ledger, ledger_df in df.groupby(mapping.ledger_name):

        if ledger in ledger_plan:
            count = ledger_plan[ledger]
            frames.append(select_evenly_distributed(ledger_df, count, mapping.invoice_date))
        else:
            others.append(ledger_df)

    if others and remaining_count > 0:
        others_df = pd.concat(others)
        frames.append(select_evenly_distributed(others_df, remaining_count, mapping.invoice_date))

    return pd.concat(frames, ignore_index=True) if frames else df.head(0)


# ===============================
# Method 3
# ===============================

def proportionate_sampling(df: pd.DataFrame, mapping: ColumnMapping, total_samples: int) -> pd.DataFrame:

    if total_samples <= 0:
        return df.head(0)

    total_rows = len(df)

    if total_samples >= total_rows:
        return df

    rate = total_samples / total_rows
    frames = []

    for _, ledger_df in df.groupby(mapping.ledger_name):
        count = max(1, int(len(ledger_df) * rate))
        frames.append(select_evenly_distributed(ledger_df, count, mapping.invoice_date))

    return pd.concat(frames, ignore_index=True)


# ===============================
# Excel Export
# ===============================

def dataframe_to_excel(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sampled Data")

    return output.getvalue()


# ===============================
# UI Renders
# ===============================

def render_method_one(df, mapping):
    st.markdown("### Method 1 – One per unique ledger")
    return one_per_ledger(df, mapping)


def render_method_two(df, mapping):

    st.markdown("### Method 2 – Specific ledger selection")

    ledgers = sorted(df[mapping.ledger_name].unique())

    selected = st.multiselect("Search & select ledgers", ledgers)

    plan = {}

    for ledger in selected:
        count = len(df[df[mapping.ledger_name] == ledger])
        plan[ledger] = st.number_input(
            f"{ledger} (rows: {count})",
            0,
            count,
            1
        )

    remaining = st.number_input(
        "Samples for remaining ledgers",
        0,
        len(df),
        5
    )

    return specific_ledger_sampling(df, mapping, plan, remaining)


def render_method_three(df, mapping):

    st.markdown("### Method 3 – Proportionate sampling")

    st.write(f"Total rows: {len(df)}")

    total = st.number_input("Total sample size", 0, len(df), 10)

    return proportionate_sampling(df, mapping, total)


# ===============================
# Main App
# ===============================

def main():
    st.set_page_config(layout="wide", page_title="Audit Sampling Tool")

    st.title("📊 Audit Sampling Tool")
    st.write("Deterministic, date-spread sampling (no randomness).")

    file = st.file_uploader("Upload Excel/CSV", type=["xlsx", "csv"])

    if not file:
        return

    df = load_data(file)

    mapping = build_mapping(df)
    if not mapping:
        return

    df = normalize_dates(df, mapping.invoice_date)

    method = st.radio(
        "Sampling Method",
        [
            "Method 1",
            "Method 2",
            "Method 3",
        ],
    )

    if method == "Method 1":
        sampled = render_method_one(df, mapping)
    elif method == "Method 2":
        sampled = render_method_two(df, mapping)
    else:
        sampled = render_method_three(df, mapping)

    st.subheader("Sample Output")
    st.dataframe(sampled, use_container_width=True)

    if len(sampled) > 0:
        st.download_button(
            "Download Sample Excel",
            dataframe_to_excel(sampled),
            "audit_sample.xlsx"
        )


if __name__ == "__main__":
    main()
