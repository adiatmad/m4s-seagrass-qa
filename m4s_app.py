"""
M4S Field Data QA Checker — Streamlit front end
================================================
Enhanced version with QA columns for easy filtering of issues.
"""

import io
import pandas as pd
import streamlit as st
from datetime import datetime
import tempfile
import pathlib
import uuid

import m4s_seagrass_qa as qa
from m4s_seagrass_qa import (
    load_data, standardize, validate, correct, generate_qa_report,
    add_qa_columns, enumerator_summary, configure,
    save_run_history, load_run_history
)

st.set_page_config(page_title="M4S Field Data QA Checker", layout="wide")
st.title("🌿 M4S Field Data QA Checker")
st.caption("Upload the raw Kobo export (.xlsx). Nothing is saved to a server — "
           "everything happens in this session and outputs are yours to download.")

with st.sidebar:
    st.subheader("⚙️ Project configuration")
    st.caption(
        "Defaults to the bundled config.yaml. Upload a different one to "
        "run this same QA pipeline against another site or survey "
        "(different species list, boundary, thresholds) — no code changes."
    )
    config_upload = st.file_uploader(
        "Custom config.yaml (optional)", type=["yaml", "yml"], key="config_upload"
    )
    if config_upload is not None:
        tmp_cfg = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False)
        tmp_cfg.write(config_upload.getvalue())
        tmp_cfg.close()
        configure(tmp_cfg.name)
        st.success(f"Using uploaded config: {config_upload.name}")
    else:
        configure()  # reset to bundled defaults (e.g. after a previous upload)

    st.markdown(
        f"- **Site:** {qa.CANONICAL_ADMIN_POST}, {qa.CANONICAL_VILLAGE}\n"
        f"- **Species tracked:** {len(qa.SPECIES_LIST)}\n"
        f"- **Boundary radius:** {qa.SITE_RADIUS_M} m\n"
        f"- **GPS precision warn/error:** {qa.GPS_PRECISION_WARN_M}m / "
        f"{qa.GPS_PRECISION_ERROR_M}m"
    )

    st.divider()
    st.subheader("📈 QA history")
    st.caption(
        "Tracking trends across survey rounds needs a run log — but this "
        "app doesn't persist anything server-side, so the log has to be "
        "something *you* carry. Upload last session's qa_history.db "
        "below (skip this on the very first run), and download the "
        "updated one at the end to bring next time."
    )
    history_upload = st.file_uploader(
        "Previous qa_history.db (optional)", type=["db"], key="history_upload"
    )
    history_db_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    if history_upload is not None:
        with open(history_db_path, "wb") as f:
            f.write(history_upload.getvalue())

uploaded = st.file_uploader("Raw Kobo export (.xlsx)", type=["xlsx"])

if uploaded is None:
    st.info("📤 Upload a file to run the pipeline.")
    st.stop()

# ---- run the pipeline ----
with st.spinner("Running QA pipeline..."):
    try:
        raw_df, df = load_data(uploaded)
    except ValueError as e:
        st.error(f"❌ Error loading data: {str(e)}")
        st.stop()

    df = standardize(df)
    issues = validate(df)
    
    # Add QA columns to the raw data
    raw_with_qa = add_qa_columns(raw_df, issues)
    
    clean_df, correction_log = correct(df)

# ---- QA history: log this run into the session's history file ----
current_run_id = save_run_history(
    clean_df, issues, correction_log,
    db_path=history_db_path, source_file=uploaded.name
)
history = load_run_history(history_db_path)

# ---- summary metrics ----
total = len(clean_df)
flagged = issues.row_index.nunique() if len(issues) else 0
errors = (issues.severity == "error").sum() if len(issues) else 0
warnings = (issues.severity == "warning").sum() if len(issues) else 0

st.divider()
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("📊 Total records", total)
col2.metric("✅ Clean records", total - flagged)
col3.metric("❌ Errors", int(errors), help="Must be fixed before analysis")
col4.metric("⚠️ Warnings", int(warnings), help="Review and decide if action needed")
col5.metric("🔧 Corrections applied", len(correction_log))

st.divider()

# ---- QA COLUMNS OVERVIEW ----
st.subheader("🔍 QA Columns — Quick Issue Identification")
st.markdown("""
The raw data now includes QA flag columns and a Message column. Each flag column indicates if a row has a specific type of issue.
The Message column provides detailed descriptions of all issues for each row.
**Filter by any column to find rows that need fixing!**
""")

# Show QA columns summary
qa_cols = ['qa_gps', 'qa_species', 'qa_logic', 'qa_photo', 'qa_duplicate', 'qa_coverage_mismatch']

qa_summary = {}
for col in qa_cols:
    if col in raw_with_qa.columns:
        count = (raw_with_qa[col] == 'Yes').sum()
        qa_summary[col.replace('qa_', '').replace('_', ' ').title()] = count

if sum(qa_summary.values()) > 0:
    cols = st.columns(len(qa_summary))
    for i, (name, count) in enumerate(qa_summary.items()):
        if count > 0:
            cols[i].metric(f"⚠️ {name}", count, delta=None)
        else:
            cols[i].metric(f"✅ {name}", 0, delta=None)
else:
    st.success("🎉 No QA flags triggered! All data looks clean.")

st.divider()

# ---- FILTERABLE DATA VIEW ----
st.subheader("📊 Filter Raw Data by QA Flags")

# Show the raw data with QA columns
display_df = raw_with_qa.copy()

# Add filter options
filter_cols = st.columns(4)
with filter_cols[0]:
    show_only_with_issues = st.checkbox("Show only rows with issues", value=False)
with filter_cols[1]:
    if show_only_with_issues:
        qa_cols_for_filter = st.multiselect(
            "Filter by QA Flag",
            options=qa_cols,
            default=qa_cols
        )
with filter_cols[2]:
    if not show_only_with_issues:
        search = st.text_input("Search in data", placeholder="Search any column...")
with filter_cols[3]:
    show_message_only = st.checkbox("Show only rows with Message", value=False)

# Apply filters
if show_only_with_issues:
    # Show only rows with any QA flag
    mask = pd.Series(False, index=display_df.index)
    for col in qa_cols_for_filter:
        if col in display_df.columns:
            mask = mask | (display_df[col] == 'Yes')
    display_df = display_df[mask]
elif show_message_only:
    display_df = display_df[display_df['Message'] != '']
elif search:
    # Simple search across all columns
    mask = pd.Series(False, index=display_df.index)
    for col in display_df.select_dtypes(include=['object', 'string']).columns:
        mask = mask | display_df[col].astype(str).str.contains(search, case=False, na=False)
    display_df = display_df[mask]

# Show count
st.caption(f"Showing {len(display_df)} rows")

# Display the data - highlight QA columns and Message
st.dataframe(
    display_df,
    width='stretch',
    column_config={
        col: st.column_config.Column(
            col,
            help=f"QA flag: 'Yes' indicates this row has the issue",
            width='small'
        ) for col in qa_cols if col in display_df.columns
    }
)

# ---- DOWNLOAD BUTTONS ----
st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    st.download_button(
        "📥 Download Raw Data with QA Flags & Messages",
        raw_with_qa.to_csv(index=False),
        file_name="raw_with_qa_flags.csv"
    )
with col2:
    st.download_button(
        "📥 Download QA Issues List",
        issues.to_csv(index=False),
        file_name="qa_issues.csv"
    )
with col3:
    st.download_button(
        "📥 Download Filtered View",
        display_df.to_csv(index=False),
        file_name=f"filtered_data_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    )

st.divider()

# ---- ORIGINAL TABS ----
tab_report, tab_issues, tab_corrections, tab_clean, tab_enum, tab_history = st.tabs(
    ["📋 QA Report", "📊 Issue List", "🔧 Correction Log", "✨ Clean Dataset",
     "🧑‍🤝‍🧑 Enumerator Quality", "📈 QA History"]
)

with tab_report:
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
        generate_qa_report(clean_df, issues, correction_log, tmp.name)
        report_text = pathlib.Path(tmp.name).read_text(encoding="utf-8")
    st.markdown(report_text)
    st.download_button("📥 Download qa_report.md", report_text, file_name="qa_report.md")

with tab_issues:
    st.dataframe(issues, width='stretch')
    st.download_button(
        "📥 Download qa_issues.csv",
        issues.to_csv(index=False),
        file_name="qa_issues.csv"
    )

with tab_corrections:
    st.dataframe(correction_log, width='stretch')
    st.download_button(
        "📥 Download correction_log.csv",
        correction_log.to_csv(index=False),
        file_name="correction_log.csv"
    )

with tab_clean:
    st.dataframe(clean_df, width='stretch')
    st.download_button(
        "📥 Download clean_dataset.csv",
        clean_df.to_csv(index=False),
        file_name="clean_dataset.csv"
    )

with tab_enum:
    enum_summary, enum_breakdown = enumerator_summary(clean_df, issues)
    if len(enum_summary) == 0:
        st.info("No 'Collector Name' column found in this dataset.")
    else:
        st.caption(
            "Ranked by error rate (worst first). Names are the corrected/normalized "
            "versions from the pipeline, so typo'd or underscore'd variants of the "
            "same person are already merged together."
        )
        st.dataframe(enum_summary, width='stretch')

        st.download_button(
            "📥 Download enumerator_summary.csv",
            enum_summary.to_csv(),
            file_name="enumerator_summary.csv"
        )

        if enum_summary["Error Rate (%)"].max() > 0:
            st.bar_chart(enum_summary["Error Rate (%)"])

        if len(enum_breakdown):
            st.subheader("Issues by category, per enumerator")
            st.dataframe(enum_breakdown, width='stretch')

with tab_history:
    runs_df = history["runs"]
    n_runs = runs_df["run_id"].nunique() if len(runs_df) else 0

    st.caption(
        f"This history covers {n_runs} run(s): whatever was in the file you "
        "uploaded (if any), plus this one. Download the updated file below "
        "and upload it again next session to keep the log going."
    )
    st.dataframe(
        runs_df.assign(**{"This run": runs_df["run_id"] == current_run_id})
        if len(runs_df) else runs_df,
        width='stretch'
    )

    if n_runs > 1:
        enum_hist = history["enumerator_stats"]
        merged = enum_hist.merge(runs_df[["run_id", "run_timestamp"]], on="run_id")
        pivot = merged.pivot_table(index="run_timestamp", columns="collector_name",
                                    values="error_rate")
        st.subheader("Error rate (%) by enumerator, across runs")
        st.line_chart(pivot)
    else:
        st.info(
            "Only this run is in the log so far. Download it below, and "
            "next time you run the pipeline, upload it in the sidebar to "
            "start seeing trends across survey rounds."
        )

    with open(history_db_path, "rb") as f:
        history_bytes = f.read()
    st.download_button(
        "📥 Download qa_history.db (bring this to your next session)",
        history_bytes,
        file_name="qa_history.db"
    )

# ---- QUICK REFERENCE ----
with st.expander("📖 QA Column Reference"):
    st.markdown("""
    ### QA Flag Columns Explained
    
    | Column | Description | When triggered |
    |--------|-------------|----------------|
    | **qa_gps** | GPS issue | Missing coordinates, low/zero precision, or outside the project boundary |
    | **qa_species** | Species logic error | Species marked present but 0% cover, or vice versa |
    | **qa_logic** | Form logic error | Missing mandatory field, invalid percent range, or an implausible survey duration |
    | **qa_photo** | Missing photo | No quadrat photo attached |
    | **qa_duplicate** | Duplicate submission | Same UUID, or same collector/transect/quadrat/date, as another record |
    | **qa_coverage_mismatch** | Coverage mismatch | Individual species % don't sum to Epicover Percentage |
    
    ### Message Column
    The `Message` column provides detailed descriptions of all issues for each row.
    Multiple issues are separated by semicolons (;).
    
    ### How to Use This Tool
    1. **Review the summary** to understand what issues exist
    2. **Filter by QA columns** to find all rows with specific issues
    3. **Check the Message column** for detailed error descriptions
    4. **Download raw_with_qa_flags.csv** to fix issues in Excel
    5. **Fix issues in the raw data** and re-upload
    6. **Use the Message column** as a checklist during data cleaning
    """)

st.caption(f"📁 Dataset: {uploaded.name} | 📊 Records: {total} | ⚠️ Issues: {len(issues)} | 🔧 Corrections: {len(correction_log)}")
