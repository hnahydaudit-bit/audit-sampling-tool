import io
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st


REQUIRED_FIELDS = [
    "Invoice Number",
    "Invoice Date",
    "Taxable Amount",
    "Ledger Name",
]


@dataclass
class ColumnMapping:
    invoice_number: str
    invoice_date: str
    taxable_amount: str
    ledger_name: str


def load_data(uploaded_file: st.runtime.uploaded_file_manager.UploadedFile) -> pd.DataFrame:
    """Load CSV or Excel files into a dataframe."""
    if uploaded_file.name.lower().endswith(".csv"):
        return pd.read_csv(uploaded_file)
    return pd.read_excel(uploaded_file, engine="openpyxl")


def build_mapping(df: pd.DataFrame) -> Optional[ColumnMapping]:
    """Render column mapping inputs and return mapping when valid."""
    st.subheader("Column Mapping")
    cols = df.columns.tolist()
    col1, col2 = st.columns(2)
    with col1:
        invoice_number = st.selectbox("Invoice Number", cols, index=0)
        invoice_date = st.selectbox("Invoice Date", cols, index=min(1, len(cols) - 1))
    with col2:
        taxable_amount = st.selectbox("Taxable Amount", cols, index=min(2, len(cols) - 1))
        ledger_name = st.selectbox("Ledger Name", cols, index=min(3, len(cols) - 1))

    mapping = ColumnMapping(
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        taxable_amount=taxable_amount,
        ledger_name=ledger_name,
    )
    if len({mapping.invoice_number, mapping.invoice_date, mapping.taxable_amount, mapping.ledger_name}) < 4:
        st.warning("Please map each required field to a unique column.")
        return None
    return mapping


def normalize_dates(df: pd.DataFrame, date_column: str) -> pd.DataFrame:
    """Return dataframe with parsed invoice dates."""
    df = df.copy()
    df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
    df = df.dropna(subset=[date_column])
    return df


def select_evenly_distributed(df: pd.DataFrame, sample_size: int, date_column: str) -> pd.DataFrame:
    """Pick rows evenly across time buckets without randomness."""
    if df.empty or sample_size <= 0:
        return df.head(0)

    df_sorted = df.sort_values(date_column).reset_index(drop=True)
    if sample_size >= len(df_sorted):
        return df_sorted

    min_date = df_sorted[date_column].min()
    max_date = df_sorted[date_column].max()
    if min_date == max_date:
        bucket_size = len(df_sorted) / sample_size
        indices = []
        for i in range(sample_size):
            start = int(round(i * bucket_size))
            end = int(round((i + 1) * bucket_size))
            if end <= start:
                end = min(start + 1, len(df_sorted))
            pick = (start + end - 1) // 2
            indices.append(min(pick, len(df_sorted) - 1))
        return df_sorted.iloc[sorted(set(indices))]

    total_seconds = (max_date - min_date).total_seconds()
    bucket_seconds = total_seconds / sample_size
    offsets = (df_sorted[date_column] - min_date).dt.total_seconds()
    df_sorted = df_sorted.assign(
        _bucket=((offsets / bucket_seconds).fillna(0).astype(int)).clip(0, sample_size - 1)
    )

    indices = []
    for bucket in range(sample_size):
        bucket_df = df_sorted[df_sorted["_bucket"] == bucket]
        if bucket_df.empty:
            continue
        pick_position = bucket_df.index[len(bucket_df) // 2]
        indices.append(pick_position)

    indices = sorted(set(indices))
    if len(indices) < sample_size:
        remaining = [idx for idx in df_sorted.index if idx not in indices]
        bucket_size = len(df_sorted) / sample_size
        for i in range(sample_size):
            if len(indices) >= sample_size:
                break
            target = int(round((i + 0.5) * bucket_size)) - 1
            target = max(0, min(target, len(df_sorted) - 1))
            if target in indices:
                continue
            if target in remaining:
                indices.append(target)

    return df_sorted.loc[sorted(set(indices))].drop(columns="_bucket")


def one_per_ledger(df: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    """Select one record per ledger, spread across time."""
    results = []
    for _, ledger_df in df.groupby(mapping.ledger_name):
        sampled = select_evenly_distributed(ledger_df, 1, mapping.invoice_date)
        results.append(sampled)
    return pd.concat(results, ignore_index=True) if results else df.head(0)


def specific_ledger_sampling(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    ledger_sample_plan: Dict[str, int],
    remaining_sample_count: int,
) -> pd.DataFrame:
    """Sample specific ledgers with overrides, and apply a default for the rest."""
    sampled_frames = []
    remaining_ledgers = []

    for ledger_name, ledger_df in df.groupby(mapping.ledger_name):
        if ledger_name in ledger_sample_plan:
            count = ledger_sample_plan[ledger_name]
            sampled_frames.append(select_evenly_distributed(ledger_df, count, mapping.invoice_date))
        else:
            remaining_ledgers.append(ledger_df)

    if remaining_ledgers and remaining_sample_count > 0:
        remaining_df = pd.concat(remaining_ledgers, ignore_index=True)
        sampled_frames.append(
            select_evenly_distributed(remaining_df, remaining_sample_count, mapping.invoice_date)
        )

    return pd.concat(sampled_frames, ignore_index=True) if sampled_frames else df.head(0)


def proportionate_sampling(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    total_sample_size: int,
) -> pd.DataFrame:
    """Allocate samples proportionately to each ledger."""
    if total_sample_size <= 0:
        return df.head(0)

    total_rows = len(df)
    if total_sample_size >= total_rows:
        return df.sort_values(mapping.invoice_date)

    grouped = list(df.groupby(mapping.ledger_name))
    allocations = []
    remainders = []
    for ledger_name, ledger_df in grouped:
        ledger_count = len(ledger_df)
        raw_allocation = ledger_count * (total_sample_size / total_rows)
        base_allocation = int(raw_allocation)
        if base_allocation == 0:
            base_allocation = 1
        allocations.append(base_allocation)
        remainders.append((raw_allocation - base_allocation, ledger_name))

    total_allocated = sum(allocations)
    if total_allocated < total_sample_size:
        shortfall = total_sample_size - total_allocated
        remainder_order = sorted(
            range(len(remainders)),
            key=lambda idx: remainders[idx][0],
            reverse=True,
        )
        for idx in remainder_order[:shortfall]:
            allocations[idx] += 1
    elif total_allocated > total_sample_size:
        surplus = total_allocated - total_sample_size
        remainder_order = sorted(
            range(len(remainders)),
            key=lambda idx: remainders[idx][0],
        )
        for idx in remainder_order:
            if surplus <= 0:
                break
            if allocations[idx] > 1:
                allocations[idx] -= 1
                surplus -= 1

    sampled_frames = []
    for (ledger_name, ledger_df), count in zip(grouped, allocations):
        count = min(count, len(ledger_df))
        sampled_frames.append(select_evenly_distributed(ledger_df, count, mapping.invoice_date))

    return pd.concat(sampled_frames, ignore_index=True) if sampled_frames else df.head(0)


def dataframe_to_excel(df: pd.DataFrame) -> bytes:
    """Serialize dataframe to an Excel file in memory."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sampled Data")
    return output.getvalue()


def render_method_one(df: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    st.markdown("**Method 1 – One per unique ledger**")
    st.write("Selects one record per ledger, spread across time buckets.")
    return one_per_ledger(df, mapping)


def render_method_two(df: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    st.markdown("**Method 2 – Specific ledger selection**")
    st.write("Choose specific ledgers and sample counts, with a default for remaining ledgers.")

    ledger_names = sorted(df[mapping.ledger_name].dropna().unique().tolist())
    selected_ledgers = st.multiselect("Select ledgers", ledger_names)

    ledger_sample_plan: Dict[str, int] = {}
    for ledger_name in selected_ledgers:
        ledger_count = len(df[df[mapping.ledger_name] == ledger_name])
        ledger_sample_plan[ledger_name] = st.number_input(
            f"Samples for {ledger_name} (rows: {ledger_count})",
            min_value=0,
            max_value=ledger_count,
            value=min(1, ledger_count),
            step=1,
        )

    remaining_sample_count = st.number_input(
        "Samples for remaining ledgers",
        min_value=0,
        max_value=len(df),
        value=min(5, len(df)),
        step=1,
    )

    return specific_ledger_sampling(df, mapping, ledger_sample_plan, remaining_sample_count)


def render_method_three(df: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    st.markdown("**Method 3 – Proportionate sampling**")
    st.write("Allocate samples proportionately across ledgers.")
    st.write(f"Total rows: {len(df)}")

    total_sample_size = st.number_input(
        "Total sample size",
        min_value=0,
        max_value=len(df),
        value=min(10, len(df)),
        step=1,
    )
    return proportionate_sampling(df, mapping, total_sample_size)


def main() -> None:
    st.set_page_config(page_title="Audit Sampling Tool", layout="wide")
    st.title("Audit Sampling Tool")
    st.write("Upload your transaction data and generate a deterministic sample spread across time.")

    uploaded_file = st.file_uploader("Upload Excel or CSV", type=["xlsx", "csv"])
    if not uploaded_file:
        st.info("Upload a file to begin.")
        return

    try:
        raw_df = load_data(uploaded_file)
    except Exception as exc:
        st.error(f"Unable to read file: {exc}")
        return

    if raw_df.empty:
        st.warning("The uploaded file has no rows.")
        return

    mapping = build_mapping(raw_df)
    if not mapping:
        return

    normalized_df = normalize_dates(raw_df, mapping.invoice_date)
    if normalized_df.empty:
        st.warning("No valid invoice dates found. Please check your mapping.")
        return

    st.subheader("Sampling Method")
    method = st.radio(
        "Choose a method",
        [
            "Method 1 – One per unique ledger",
            "Method 2 – Specific ledger selection",
            "Method 3 – Proportionate sampling",
        ],
    )

    if method == "Method 1 – One per unique ledger":
        sampled_df = render_method_one(normalized_df, mapping)
    elif method == "Method 2 – Specific ledger selection":
        sampled_df = render_method_two(normalized_df, mapping)
    else:
        sampled_df = render_method_three(normalized_df, mapping)

    st.subheader("Sampled Results")
    st.write(f"Sampled rows: {len(sampled_df)}")
    st.dataframe(sampled_df, use_container_width=True)

    if len(sampled_df) == 0:
        st.info("No rows selected based on the chosen settings.")
        return

    excel_data = dataframe_to_excel(sampled_df)
    st.download_button(
        "Download sampled Excel",
        data=excel_data,
        file_name="audit_sample.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    main()
