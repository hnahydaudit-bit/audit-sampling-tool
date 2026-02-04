import io
from dataclasses import dataclass
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
# ⭐ FORCE COMPLETE BLUE THEME (NO RED ANYWHERE)
# ======================================================

st.markdown("""
<style>

/* radio */
div[role="radiogroup"] label[data-checked="true"]{
    background:#2563eb !important;
    color:white !important;
}

/* checkbox */
input[type="checkbox"]{
    accent-color:#2563eb !important;
}

/* buttons */
.stButton>button{
    background:#2563eb !important;
    color:white !important;
}

/* tags / pills */
[data-baseweb="tag"]{
    background:#2563eb !important;
    color:white !important;
}

/* focus */
:focus{
    border-color:#2563eb !important;
}

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


def sort_financial_year(df, date_col):
    temp = df.copy()
    d = pd.to_datetime(temp[date_col])

    temp["_fy"] = (d.dt.month - 4) % 12
    temp["_yr"] = d.dt.year
    temp["_d"] = d

    temp = temp.sort_values(["_yr", "_fy", "_d"])

    return temp.drop(columns=["_fy", "_yr", "_d"])


def clean_date_format(df, date_col):
    temp = df.copy()
    temp[date_col] = pd.to_datetime(temp[date_col]).dt.strftime("%d-%m-%Y")
    return temp


def evenly_spread_indices(df, n, date_col):
    if n <= 0 or df.empty:
        return []

    df_sorted = df.sort_values(date_col)

    if n >= len(df_sorted):
        return df_sorted.index.tolist()

    bucket = len(df_sorted) / n
    idx = []

    for i in range(n):
        pos = int((i + 0.5) * bucket)
        pos = min(pos, len(df_sorted) - 1)
        idx.append(df_sorted.index[pos])

    return sorted(set(idx))


# ======================================================
# METHODS
# ======================================================

def method_one(df, mapping):
    idx = []
    for _, g in df.groupby(mapping.ledger_name):
        idx.extend(evenly_spread_indices(g, 1, mapping.invoice_date))
    return idx


# ======================================================
# ⭐ TRUE EXCEL-LIKE FILTER
# ======================================================

def method_two(df, mapping):

    st.subheader("Select Ledgers")

    # Master ledger list (never filtered)
    master = (
        df.groupby(mapping.ledger_name)
        .size()
        .reset_index(name="Rows")
        .sort_values(mapping.ledger_name)
    )

    # store selections separately (Excel style)
    if "selected_ledgers" not in st.session_state:
        st.session_state.selected_ledgers = []

    search = st.text_input("Filter")

    # filter only for display
    if search:
        display_df = master[
            master[mapping.ledger_name].str.contains(search, case=False)
        ].copy()
    else:
        display_df = master.copy()

    display_df["Select"] = display_df[mapping.ledger_name].isin(
        st.session_state.selected_ledgers
    )

    edited = st.data_editor(
        display_df,
        use_container_width=True,
        height=350,
        column_config={
            "Select": st.column_config.CheckboxColumn()
        },
        disabled=[mapping.ledger_name, "Rows"]
    )

    # update selections
    selected_now = edited.loc[edited["Select"], mapping.ledger_name].tolist()

    st.session_state.selected_ledgers = list(
        set(st.session_state.selected_ledgers + selected_now)
    )

    selected_ledgers = st.session_state.selected_ledgers

    # --------------------------------------------------

    plan = {}
    selected_rows = 0

    for l in selected_ledgers:
        cnt = len(df[df[mapping.ledger_name] == l])
        selected_rows += cnt

        plan[l] = st.number_input(
            f"{l} samples",
            0,
            cnt,
            1,
            key=f"s_{l}"
        )

    remaining_rows = len(df) - selected_rows
    st.info(f"Remaining rows: {remaining_rows}")

    rem = st.number_input(
        "Samples for remaining",
        0,
        remaining_rows,
        min(5, remaining_rows)
    )

    idx = []

    for ledger, g in df.groupby(mapping.ledger_name):
        if ledger in plan:
            idx.extend(evenly_spread_indices(g, plan[ledger], mapping.invoice_date))

    others = df[~df[mapping.ledger_name].isin(plan.keys())]
    idx.extend(evenly_spread_indices(others, rem, mapping.invoice_date))

    return idx


def method_three(df, mapping, total):

    total_rows = len(df)
    grouped = list(df.groupby(mapping.ledger_name))

    floors, fracs = [], []

    for _, g in grouped:
        val = len(g) * total / total_rows
        floors.append(int(val))
        fracs.append(val - int(val))

    rem = total - sum(floors)

    order = sorted(range(len(fracs)), key=lambda i: fracs[i], reverse=True)

    for i in order[:rem]:
        floors[i] += 1

    idx = []

    for (_, g), c in zip(grouped, floors):
        idx.extend(evenly_spread_indices(g, c, mapping.invoice_date))

    return idx


# ======================================================
# EXPORT
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

    st.title("📊 Audit Sampling Tool")

    file = st.file_uploader("Upload Excel/CSV", type=["xlsx", "csv"])

    if not file:
        return

    raw_df = load_data(file)

    with st.sidebar:

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
    sampled = clean_date_format(sampled, mapping.invoice_date)

    with st.sidebar:
        st.divider()
        st.metric("Total Rows", len(raw_df))
        st.metric("Sample Size", len(sampled))
        st.metric("Coverage %", f"{round(len(sampled)/len(raw_df)*100,2)}%")

    st.dataframe(sampled, use_container_width=True)

    if len(sampled):
        st.download_button("Download Excel", to_excel(sampled), "audit_sample.xlsx")


if __name__ == "__main__":
    main()
