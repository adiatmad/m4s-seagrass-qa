"""
M4S Field Data QA Checker
=========================
Turns a raw KoboToolbox export into an analysis-ready dataset with QA flags.

Pipeline stages:
    1. load_data          -> read the raw .xlsx, keep an untouched copy
    2. standardize         -> parse dates, trim whitespace, fix dtypes
    3. validate             -> run every QA rule, collect issues
    4. add_qa_columns      -> add QA flag columns + Message column to raw data
    5. correct               -> apply ONLY the safe, reversible corrections
    6. generate_qa_report -> one Markdown report summarizing everything
    7. export_outputs      -> write clean dataset + correction log + raw copy with QA flags

Run directly:  python3 m4s_seagrass_qa.py raw_export.xlsx
Outputs land in ./output/
"""

import sys
import re
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np
import yaml
from rapidfuzz import fuzz, process

# ----------------------------------------------------------------------
# PROJECT-SPECIFIC REFERENCE DATA
# ----------------------------------------------------------------------
# All of the values below live in config.yaml (next to this script) and
# are loaded into the module-level constants that the QA rules use.
# This means the same pipeline can run for a different site or survey
# (different species list, boundary, thresholds) just by pointing it at
# a different config file — no code changes.
#
# If config.yaml is missing, _BUILTIN_DEFAULTS below (the original M4S
# Seagrass / Metinaro values) are used, so the pipeline still runs
# out of the box.

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

_BUILTIN_DEFAULTS = {
    "species_list": [
        "Halophila ovalis", "Halophila minor", "Halodule pinifolia",
        "Halodule uninervis", "Halophila decipiens", "Halophila beccarii",
        "Halophila spinulosa", "Enhalus acoroides", "Thalassia hemprichii",
        "Cymodocea rotundata", "Cymodocea serrulata", "Syringodium isoetifolium",
        "Ruppia maritima", "Thalassodendron ciliatum",
    ],
    "geography": {
        "admin_post": "Metinaro",
        "village": ["Sabuli", "Wenunuc"],
    },
    "site": {
        "centroid": [-8.51967, 125.7174723],   # [lat, lon], WGS84
        "radius_m": 500,
    },
    "thresholds": {
        "enumerator_match": 85,
        "geo_match": 80,
        "gps_precision_warn_m": 5,
        "gps_precision_error_m": 5,
        "duration_too_fast_s": 60,
        "duration_long_session_s": 8 * 3600,
    },
}


def load_config(path=None) -> dict:
    """
    Load project configuration from a YAML file. Falls back to
    config.yaml next to this script, and if that doesn't exist either,
    falls back to _BUILTIN_DEFAULTS (the original M4S Seagrass values).
    A config file only needs to override the keys it wants to change —
    missing top-level sections fall back to the defaults.
    """
    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        cfg = {**_BUILTIN_DEFAULTS, **loaded}
    else:
        cfg = _BUILTIN_DEFAULTS
    return cfg


def configure(path=None) -> dict:
    """
    (Re)load project configuration and update the module-level constants
    every QA rule reads from (SPECIES_LIST, CANONICAL_ADMIN_POST, etc.).
    Called automatically at import time with the default config.yaml.
    Call again with a different path to point the whole pipeline at a
    different site/survey without touching any code.
    """
    global SPECIES_LIST, CANONICAL_ADMIN_POST, CANONICAL_VILLAGE, CANONICAL_VILLAGES
    global SITE_CENTROID, SITE_RADIUS_M
    global ENUMERATOR_MATCH_THRESHOLD, GEO_MATCH_THRESHOLD
    global GPS_PRECISION_WARN_M, GPS_PRECISION_ERROR_M
    global DURATION_TOO_FAST_S, DURATION_LONG_SESSION_S

    cfg = load_config(path)
    SPECIES_LIST = cfg["species_list"]
    CANONICAL_ADMIN_POST = cfg["geography"]["admin_post"]
    # 'village' accepts a single name or a list — normalize to a list.
    village_cfg = cfg["geography"]["village"]
    CANONICAL_VILLAGES = village_cfg if isinstance(village_cfg, list) else [village_cfg]
    CANONICAL_VILLAGE = CANONICAL_VILLAGES[0]  # kept for backward-compat / display
    SITE_CENTROID = tuple(cfg["site"]["centroid"])
    SITE_RADIUS_M = cfg["site"]["radius_m"]
    t = cfg["thresholds"]
    ENUMERATOR_MATCH_THRESHOLD = t["enumerator_match"]
    GEO_MATCH_THRESHOLD = t["geo_match"]
    GPS_PRECISION_WARN_M = t["gps_precision_warn_m"]
    GPS_PRECISION_ERROR_M = t["gps_precision_error_m"]
    DURATION_TOO_FAST_S = t["duration_too_fast_s"]
    DURATION_LONG_SESSION_S = t["duration_long_session_s"]
    return cfg


configure()  # load config.yaml (or built-in defaults) at import time


# ----------------------------------------------------------------------
# STAGE 1 — LOAD
# ----------------------------------------------------------------------

def load_data(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the raw Kobo export. Returns (raw_df, working_df)."""
    raw = pd.read_excel(path)
    required = [
        "start", "end", "Collector Name", "Date and time",
        "Administration Post", "Village", "_Local GPS_latitude",
        "_Local GPS_longitude", "_Local GPS_precision", "_uuid",
    ]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise ValueError(
            f"Form schema changed — missing expected columns: {missing}. "
            "Stopping rather than guessing; update SPECIES_LIST/column "
            "references at the top of this file if the form was "
            "intentionally redesigned."
        )
    return raw, raw.copy()


# ----------------------------------------------------------------------
# STAGE 2 — STANDARDIZE
# ----------------------------------------------------------------------

def _strip_all_strings(df: pd.DataFrame) -> pd.DataFrame:
    obj_cols = df.select_dtypes(include="object").columns
    for c in obj_cols:
        df[c] = df[c].apply(lambda v: v.strip() if isinstance(v, str) else v)
        df[c] = df[c].apply(
            lambda v: re.sub(r"\s+", " ", v) if isinstance(v, str) else v
        )
    return df


def standardize(df: pd.DataFrame) -> pd.DataFrame:
    df = _strip_all_strings(df)
    for col in ["start", "end", "Date and time", "_submission_time"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    df["_duration_seconds"] = (df["end"] - df["start"]).dt.total_seconds()
    numeric_like = [c for c in df.columns if "(%)" in c or c in (
        "Water Level (Cm)", "_Local GPS_latitude", "_Local GPS_longitude",
        "_Local GPS_altitude", "_Local GPS_precision",
    )]
    for c in numeric_like:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ----------------------------------------------------------------------
# STAGE 3 — VALIDATE
# ----------------------------------------------------------------------

def _new_issue(idx, uuid, category, field, severity, message):
    return dict(row_index=idx, uuid=uuid, category=category, field=field,
                severity=severity, message=message)


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def validate_gps(df):
    issues = []
    for i, r in df.iterrows():
        uid = r["_uuid"]
        lat, lon, prec = r["_Local GPS_latitude"], r["_Local GPS_longitude"], r["_Local GPS_precision"]
        if pd.isna(lat) or pd.isna(lon):
            issues.append(_new_issue(i, uid, "gps", "_Local GPS", "error",
                                      "Missing GPS coordinates"))
            continue
        if pd.notna(prec):
            if prec >= GPS_PRECISION_ERROR_M:
                issues.append(_new_issue(i, uid, "gps", "_Local GPS_precision", "error",
                                          f"GPS precision {prec:.1f}m — unusable, record location manually"))
            elif prec >= GPS_PRECISION_WARN_M:
                issues.append(_new_issue(i, uid, "gps", "_Local GPS_precision", "warning",
                                          f"GPS precision {prec:.1f}m — below recommended accuracy"))
        dist = _haversine_m(*SITE_CENTROID, lat, lon)
        if dist > SITE_RADIUS_M:
            issues.append(_new_issue(i, uid, "gps", "_Local GPS", "error",
                                      f"{dist:.0f}m from survey site centroid — outside project boundary"))
    coord_cols = ["_Local GPS_latitude", "_Local GPS_longitude"]
    dup_mask = df.duplicated(subset=coord_cols, keep=False) & df[coord_cols].notna().all(axis=1)
    for i, r in df[dup_mask].iterrows():
        issues.append(_new_issue(i, r["_uuid"], "gps", "_Local GPS", "warning",
                                  "Coordinates identical to another record — check GPS was refreshed between quadrats"))
    return issues


def validate_species_logic(df):
    issues = []
    for i, r in df.iterrows():
        uid = r["_uuid"]
        for sp in SPECIES_LIST:
            sel_col = f"Seagrass species present in the Quadrat/{sp}"
            pct_col = f"Percent {sp} (%)"
            if sel_col not in df.columns or pct_col not in df.columns:
                continue
            selected = r[sel_col] == 1
            pct = r[pct_col]
            if selected and (pd.isna(pct) or pct == 0):
                issues.append(_new_issue(i, uid, "species", pct_col, "error",
                                          f"{sp} marked present but no % cover recorded"))
            if pd.notna(pct) and pct > 0 and not selected:
                issues.append(_new_issue(i, uid, "species", pct_col, "error",
                                          f"% cover recorded for {sp} but species not marked present"))
            if pd.notna(pct) and (pct < 0 or pct > 100):
                issues.append(_new_issue(i, uid, "species", pct_col, "error",
                                          f"{sp} % cover {pct} outside 0-100 range"))
        presence = r.get("Presence of Seagrass")
        any_species = any(
            r.get(f"Seagrass species present in the Quadrat/{sp}") == 1
            for sp in SPECIES_LIST
        )
        if presence == "No" and any_species:
            issues.append(_new_issue(i, uid, "species", "Presence of Seagrass", "error",
                                      "Presence of Seagrass = No but species were recorded"))
        if presence == "Yes" and not any_species:
            issues.append(_new_issue(i, uid, "species", "Presence of Seagrass", "warning",
                                      "Presence of Seagrass = Yes but no species selected"))
    return issues


def validate_percent_fields(df):
    issues = []
    pct_cols = [c for c in ["Percentage of Algal Cover (%)", "Epicover Percentage (%)"] if c in df.columns]
    for i, r in df.iterrows():
        for c in pct_cols:
            v = r[c]
            if pd.notna(v) and (v < 0 or v > 100):
                issues.append(_new_issue(i, r["_uuid"], "logic", c, "error",
                                          f"{c} = {v}, outside 0-100 range"))
    return issues


def validate_mandatory_fields(df):
    mandatory = ["Collector Name", "Date and time", "Transect Number",
                 "Quadrat Number", "Presence of Seagrass"]
    issues = []
    for i, r in df.iterrows():
        for f in mandatory:
            if f in df.columns and pd.isna(r[f]):
                issues.append(_new_issue(i, r["_uuid"], "logic", f, "error",
                                          f"Mandatory field '{f}' is missing"))
    return issues


def validate_timestamps(df):
    issues = []
    for i, r in df.iterrows():
        dur = r["_duration_seconds"]
        if pd.isna(dur):
            issues.append(_new_issue(i, r["_uuid"], "logic", "start/end", "error",
                                      "start/end timestamp missing or unparsable"))
            continue
        if dur <= 0:
            issues.append(_new_issue(i, r["_uuid"], "logic", "start/end", "error",
                                      "End time is before or equal to start time"))
        elif dur < DURATION_TOO_FAST_S:
            issues.append(_new_issue(i, r["_uuid"], "logic", "start/end", "warning",
                                      f"Survey completed in {dur:.0f}s — unusually fast, check for auto-fill"))
        elif dur > DURATION_LONG_SESSION_S:
            issues.append(_new_issue(i, r["_uuid"], "logic", "start/end", "warning",
                                      f"Session lasted {dur/3600:.1f}h — form likely left open/resumed later"))
    return issues


def validate_photos(df):
    issues = []
    for i, r in df.iterrows():
        if "Quadrat Photo_URL" in df.columns and pd.isna(r["Quadrat Photo_URL"]):
            issues.append(_new_issue(i, r["_uuid"], "photo", "Quadrat Photo_URL", "error",
                                      "No quadrat photo attached"))
    return issues


def validate_duplicate_submissions(df):
    issues = []
    dup = df.duplicated(subset=["_uuid"], keep=False)
    for i, r in df[dup].iterrows():
        issues.append(_new_issue(i, r["_uuid"], "duplicate", "_uuid", "error",
                                  "Duplicate _uuid — same submission ingested twice"))
    logical_key = ["Collector Name", "Transect Number", "Quadrat Number", "Date and time"]
    if all(k in df.columns for k in logical_key):
        dup2 = df.duplicated(subset=logical_key, keep=False)
        for i, r in df[dup2].iterrows():
            issues.append(_new_issue(i, r["_uuid"], "duplicate", "combo", "warning",
                                      "Same collector/transect/quadrat/date as another record — possible re-entry"))
    return issues


def validate_coverage_mismatch(df):
    """
    Check if individual species percentages add up to total epicover percentage.
    Uses "Epicover Percentage (%)" as the reference column.
    """
    issues = []
    
    # Get species percent columns
    pct_cols = [col for col in df.columns if col.startswith("Percent ") and " (%)" in col]
    # Use Epicover Percentage (%) as the reference column
    total_col = "Epicover Percentage (%)"
    
    if total_col in df.columns:
        for i, r in df.iterrows():
            total_cover = r[total_col]
            if pd.isna(total_cover):
                continue
            
            # Sum up individual species percentages
            species_total = 0
            for col in pct_cols:
                val = r[col]
                if pd.notna(val):
                    species_total += val
            
            # Check if species total is significantly different from total cover
            # Allow 5% tolerance for rounding/estimation errors
            if abs(species_total - total_cover) > 5:
                issues.append(_new_issue(
                    i, r["_uuid"], "coverage_mismatch", total_col, "error",
                    f"Individual species percentages sum to {species_total:.1f}% but Epicover Percentage is {total_cover:.1f}% (difference: {abs(species_total - total_cover):.1f}%)"
                ))
    
    return issues


def validate(df) -> pd.DataFrame:
    all_issues = []
    for fn in (validate_gps, validate_species_logic,
               validate_percent_fields, validate_mandatory_fields, validate_timestamps,
               validate_photos, validate_duplicate_submissions,
               validate_coverage_mismatch):
        all_issues.extend(fn(df))
    return pd.DataFrame(all_issues)


# ----------------------------------------------------------------------
# STAGE 3b — ENUMERATOR QUALITY SUMMARY
# ----------------------------------------------------------------------

def enumerator_summary(df: pd.DataFrame, issues: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Per-enumerator QA summary: record counts, error/warning rates, and a
    breakdown of issues by category.

    `df` should be the POST-CORRECTION dataframe (i.e. clean_df from
    correct()), so that enumerator names are already canonicalized —
    otherwise the same person's typo'd/underscore'd name variants would
    get split into separate rows here.

    Returns (summary, category_breakdown):
      - summary: one row per enumerator — Records, Errors, Warnings,
        Records with Error, Records with Warning, Error Rate (%),
        Warning Rate (%). Sorted worst error rate first.
      - category_breakdown: one row per enumerator, one column per issue
        category (gps, species, logic, photo, duplicate, coverage_mismatch),
        counts of issues in each.
    """
    if "Collector Name" not in df.columns:
        return pd.DataFrame(), pd.DataFrame()

    name_by_row = df["Collector Name"]
    total_by_enum = name_by_row.value_counts()
    total_by_enum.name = "Records"

    empty_cols = ["Errors", "Warnings", "Records with Error", "Records with Warning",
                  "Error Rate (%)", "Warning Rate (%)"]
    if len(issues) == 0:
        summary = total_by_enum.to_frame()
        for col in empty_cols:
            summary[col] = 0
        return summary.sort_values("Records", ascending=False), pd.DataFrame()

    issues = issues.copy()
    issues["Collector Name"] = issues["row_index"].map(name_by_row)
    issues = issues.dropna(subset=["Collector Name"])

    err = issues[issues.severity == "error"]
    warn = issues[issues.severity == "warning"]

    summary = total_by_enum.to_frame()
    summary["Errors"] = err.groupby("Collector Name").size()
    summary["Warnings"] = warn.groupby("Collector Name").size()
    summary["Records with Error"] = err.groupby("Collector Name")["row_index"].nunique()
    summary["Records with Warning"] = warn.groupby("Collector Name")["row_index"].nunique()
    summary[empty_cols[:-2]] = summary[empty_cols[:-2]].fillna(0).astype(int)

    summary["Error Rate (%)"] = (summary["Records with Error"] / summary["Records"] * 100).round(1)
    summary["Warning Rate (%)"] = (summary["Records with Warning"] / summary["Records"] * 100).round(1)
    summary = summary.sort_values("Error Rate (%)", ascending=False)

    category_breakdown = (
        issues.groupby(["Collector Name", "category"]).size().unstack(fill_value=0)
    )
    category_breakdown = category_breakdown.loc[summary.index.intersection(category_breakdown.index)]

    return summary, category_breakdown


# ----------------------------------------------------------------------
# STAGE 4 — ADD QA COLUMNS TO RAW DATA
# ----------------------------------------------------------------------

def add_qa_columns(df, issues):
    """
    Add QA flag columns and a Message column to the dataframe.

    Flag columns: one per broad issue category, matching the categories
    validate() actually produces (issues['category']) — qa_gps,
    qa_species, qa_logic, qa_photo, qa_duplicate, qa_coverage_mismatch.
    Each is 'Yes' if that row has at least one issue in that category,
    '' otherwise.

    Message column: built directly from every issue's own message text
    for that row (grouped and joined with '; ', severity-tagged). This
    is pulled straight from the issues table rather than pattern-matched
    per category — so every issue type always shows up here, including
    any new validate_* rule added later, with no separate mapping to
    keep in sync.
    """
    df_qa = df.copy()

    qa_categories = ['gps', 'species', 'logic', 'photo', 'duplicate', 'coverage_mismatch']
    for cat in qa_categories:
        df_qa[f'qa_{cat}'] = ''

    df_qa['Message'] = ''

    if len(issues) == 0:
        return df_qa

    for cat in qa_categories:
        rows_with_cat = issues.loc[issues.category == cat, 'row_index'].unique()
        df_qa.loc[df_qa.index.isin(rows_with_cat), f'qa_{cat}'] = 'Yes'

    messages_by_row = (
        issues.sort_values(['row_index', 'severity'])
        .assign(formatted=lambda d: '[' + d['severity'] + '] ' + d['message'])
        .groupby('row_index')['formatted']
        .apply('; '.join)
    )
    for row_idx, msg in messages_by_row.items():
        if row_idx in df_qa.index:
            df_qa.at[row_idx, 'Message'] = msg

    return df_qa


# ----------------------------------------------------------------------
# STAGE 5 — CORRECT
# ----------------------------------------------------------------------

def _fuzzy_canonicalize(series: pd.Series, threshold: int) -> tuple[pd.Series, dict]:
    values = series.dropna().unique().tolist()
    norm = {v: re.sub(r"\s+", " ", v.replace("_", " ")).strip() for v in values}
    counts = series.value_counts()

    clusters: list[list[str]] = []
    assigned = set()
    for v in values:
        if v in assigned:
            continue
        cluster = [v]
        assigned.add(v)
        for other in values:
            if other in assigned:
                continue
            if fuzz.token_sort_ratio(norm[v].lower(), norm[other].lower()) >= threshold:
                cluster.append(other)
                assigned.add(other)
        clusters.append(cluster)

    def format_quality(v: str) -> int:
        score = 0
        if "_" not in v:
            score += 2
        if v != v.lower():
            score += 1
        return score

    mapping = {}
    for cluster in clusters:
        canonical = max(cluster, key=lambda v: (format_quality(v), counts[v]))
        for v in cluster:
            mapping[v] = canonical
    corrected = series.map(lambda v: mapping.get(v, v) if pd.notna(v) else v)
    return corrected, mapping


def correct(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()
    log_rows = []

    def log_change(mask, col, mapping, rule):
        for i in df.index[mask]:
            before = df.at[i, col]
            after_val = mapping.get(before, before) if isinstance(mapping, dict) else None
            log_rows.append(dict(row_index=i, uuid=df.at[i, "_uuid"], field=col,
                                  rule=rule, before=before, after=after_val,
                                  timestamp=datetime.now(timezone.utc).isoformat()))

    # Safe correction 1: enumerator name normalization
    corrected, mapping = _fuzzy_canonicalize(df["Collector Name"], ENUMERATOR_MATCH_THRESHOLD)
    changed_mask = (df["Collector Name"] != corrected) & df["Collector Name"].notna()
    log_change(changed_mask, "Collector Name", mapping, "enumerator_name_normalization")
    df["Collector Name"] = corrected

    # Reformat snake_case names
    before_fmt = df["Collector Name"].copy()

    def format_fix(v):
        if isinstance(v, str) and "_" in v:
            return v.replace("_", " ").title()
        return v

    df["Collector Name"] = df["Collector Name"].apply(format_fix)
    fmt_changed = before_fmt != df["Collector Name"]
    for i in df.index[fmt_changed]:
        log_rows.append(dict(row_index=i, uuid=df.at[i, "_uuid"], field="Collector Name",
                              rule="enumerator_name_formatting", before=before_fmt.at[i],
                              after=df.at[i, "Collector Name"],
                              timestamp=datetime.now(timezone.utc).isoformat()))

    # Safe correction 2: geography typo fix (only obvious typos).
    # Village can have several valid canonical names (multiple survey
    # villages); Administration Post has one. Fuzzy-match against
    # whichever set applies and snap to the closest canonical value.
    for field, canon_values in [("Administration Post", [CANONICAL_ADMIN_POST]),
                                 ("Village", CANONICAL_VILLAGES)]:
        before_col = df[field].copy()

        def fix(v, canon_values=canon_values):
            if pd.isna(v) or v in canon_values:
                return v
            best_canon, best_score = None, -1
            for canon in canon_values:
                score = fuzz.token_sort_ratio(str(v).lower(), canon.lower())
                if score > best_score:
                    best_canon, best_score = canon, score
            return best_canon if best_score >= GEO_MATCH_THRESHOLD else v

        df[field] = df[field].apply(fix)
        changed_mask = before_col != df[field]
        for i in df.index[changed_mask]:
            log_rows.append(dict(row_index=i, uuid=df.at[i, "_uuid"], field=field,
                                  rule="geography_typo_fix", before=before_col.at[i],
                                  after=df.at[i, field],
                                  timestamp=datetime.now(timezone.utc).isoformat()))

    correction_log = pd.DataFrame(log_rows)
    return df, correction_log


# ----------------------------------------------------------------------
# STAGE 6 — QA REPORT
# ----------------------------------------------------------------------

def generate_qa_report(df, issues, correction_log, out_path):
    total = len(df)
    if len(issues):
        err = issues[issues.severity == "error"]
        warn = issues[issues.severity == "warning"]
    else:
        err = warn = issues
    flagged_records = issues.row_index.nunique() if len(issues) else 0
    passed = total - flagged_records

    lines = []
    lines.append(f"# M4S Field Data QA Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("## Summary\n")
    lines.append(f"- Total records: **{total}**")
    lines.append(f"- Clean (no issues): **{passed}**")
    lines.append(f"- Records with at least one issue: **{flagged_records}**")
    lines.append(f"- Errors: **{len(err)}**  |  Warnings: **{len(warn)}**")
    lines.append(f"- Safe corrections applied: **{len(correction_log)}**\n")

    lines.append("## Issues by category\n")
    if len(issues):
        by_cat = issues.groupby(["category", "severity"]).size().unstack(fill_value=0)
        lines.append(by_cat.to_markdown())
    else:
        lines.append("No issues found.")
    lines.append("")

    lines.append("## Corrections applied (by rule)\n")
    if len(correction_log):
        lines.append(correction_log.groupby("rule").size().to_markdown())
    else:
        lines.append("No corrections were applied.")
    lines.append("")

    lines.append("## Enumerator quality\n")
    enum_summary, enum_breakdown = enumerator_summary(df, issues)
    if len(enum_summary):
        lines.append(enum_summary.to_markdown())
        lines.append("")
        if len(enum_breakdown):
            lines.append("**Issues by category, per enumerator:**\n")
            lines.append(enum_breakdown.to_markdown())
    else:
        lines.append("No 'Collector Name' column found — skipping enumerator breakdown.")
    lines.append("")

    lines.append("## Every flagged record\n")
    if len(issues):
        lines.append("| Row | UUID | Category | Field | Severity | Message |")
        lines.append("|---|---|---|---|---|---|")
        for _, r in issues.sort_values(["row_index", "severity"]).iterrows():
            lines.append(f"| {r.row_index} | {str(r.uuid)[:8]}… | {r.category} | {r.field} | "
                          f"{r.severity} | {r.message} |")
    else:
        lines.append("No flagged records — every row passed all QA checks.")

    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


# ----------------------------------------------------------------------
# STAGE 6b — QA HISTORY (persist run stats across survey rounds)
# ----------------------------------------------------------------------
# A "run" here means one execution of the pipeline against one raw
# export. We store only AGGREGATE counts — total records, error/warning
# counts by category, and the enumerator summary table — never raw
# survey rows. That's what makes trend tracking ("is enumerator B
# actually getting worse, or was that one bad day") possible without
# re-processing every old export.

def _init_history_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            run_timestamp TEXT NOT NULL,
            source_file TEXT,
            total_records INTEGER,
            flagged_records INTEGER,
            errors INTEGER,
            warnings INTEGER,
            corrections_applied INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_category_stats (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            count INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_enumerator_stats (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            collector_name TEXT NOT NULL,
            records INTEGER,
            errors INTEGER,
            warnings INTEGER,
            error_rate REAL,
            warning_rate REAL
        )
    """)
    conn.commit()


def save_run_history(clean_df, issues, correction_log, db_path="qa_history.db",
                      source_file: str | None = None) -> str:
    """
    Append this run's summary stats (total records, error/warning counts
    by category, enumerator summary) to a local SQLite history file.
    Creates the file and tables on first use. Returns the run_id.
    """
    conn = sqlite3.connect(db_path)
    _init_history_db(conn)

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    total = len(clean_df)
    flagged = issues.row_index.nunique() if len(issues) else 0
    n_errors = int((issues.severity == "error").sum()) if len(issues) else 0
    n_warnings = int((issues.severity == "warning").sum()) if len(issues) else 0

    conn.execute(
        "INSERT INTO runs (run_id, run_timestamp, source_file, total_records, "
        "flagged_records, errors, warnings, corrections_applied) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, datetime.now(timezone.utc).isoformat(), source_file, total,
         flagged, n_errors, n_warnings, len(correction_log)),
    )

    if len(issues):
        cat_counts = issues.groupby(["category", "severity"]).size()
        conn.executemany(
            "INSERT INTO run_category_stats (run_id, category, severity, count) VALUES (?,?,?,?)",
            [(run_id, cat, sev, int(cnt)) for (cat, sev), cnt in cat_counts.items()],
        )

    enum_summary, _ = enumerator_summary(clean_df, issues)
    if len(enum_summary):
        conn.executemany(
            "INSERT INTO run_enumerator_stats (run_id, collector_name, records, errors, "
            "warnings, error_rate, warning_rate) VALUES (?,?,?,?,?,?,?)",
            [(run_id, name, int(row["Records"]), int(row["Errors"]), int(row["Warnings"]),
              float(row["Error Rate (%)"]), float(row["Warning Rate (%)"]))
             for name, row in enum_summary.iterrows()],
        )

    conn.commit()
    conn.close()
    return run_id


def load_run_history(db_path="qa_history.db") -> dict[str, pd.DataFrame]:
    """
    Load the full QA run history as three DataFrames: runs,
    category_stats, enumerator_stats. Returns empty DataFrames (not an
    error) if the file doesn't exist yet — a project's first run.
    """
    empty = {
        "runs": pd.DataFrame(),
        "category_stats": pd.DataFrame(),
        "enumerator_stats": pd.DataFrame(),
    }
    if not Path(db_path).exists():
        return empty
    conn = sqlite3.connect(db_path)
    try:
        _init_history_db(conn)  # no-op if tables already exist
        runs = pd.read_sql_query("SELECT * FROM runs ORDER BY run_timestamp", conn)
        category_stats = pd.read_sql_query("SELECT * FROM run_category_stats", conn)
        enumerator_stats = pd.read_sql_query("SELECT * FROM run_enumerator_stats", conn)
    finally:
        conn.close()
    return {"runs": runs, "category_stats": category_stats, "enumerator_stats": enumerator_stats}


# ----------------------------------------------------------------------
# STAGE 7 — EXPORT
# ----------------------------------------------------------------------

def export_outputs(raw_df, clean_df, issues, correction_log, out_dir, source_file=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Add QA columns to the raw data before export
    raw_with_qa = add_qa_columns(raw_df, issues)

    # utf-8-sig (UTF-8 + BOM): plain 'utf-8' writes are byte-correct but Excel
    # doesn't auto-detect UTF-8 without a BOM and falls back to guessing the
    # system codepage — mangling em dashes ('—' -> 'â€”') and other non-ASCII
    # characters. The BOM makes Excel open these correctly with no extra steps.
    raw_with_qa.to_csv(out_dir / "raw_with_qa_flags.csv", index=False, encoding="utf-8-sig")
    clean_df.to_csv(out_dir / "clean_dataset.csv", index=False, encoding="utf-8-sig")
    issues.to_csv(out_dir / "qa_issues.csv", index=False, encoding="utf-8-sig")
    correction_log.to_csv(out_dir / "correction_log.csv", index=False, encoding="utf-8-sig")

    enum_summary, _ = enumerator_summary(clean_df, issues)
    enum_summary.to_csv(out_dir / "enumerator_summary.csv", encoding="utf-8-sig")

    save_run_history(clean_df, issues, correction_log,
                      db_path=out_dir / "qa_history.db", source_file=source_file)

    generate_qa_report(clean_df, issues, correction_log, out_dir / "qa_report.md")


# ----------------------------------------------------------------------
# ENTRY POINT
# ----------------------------------------------------------------------

def run(input_path: str, out_dir: str = "output", config_path: str | None = None):
    if config_path:
        configure(config_path)
    raw_df, df = load_data(input_path)
    df = standardize(df)
    issues = validate(df)
    df, correction_log = correct(df)
    export_outputs(raw_df, df, issues, correction_log, out_dir, source_file=input_path)
    print(f"Done. {len(df)} records processed, {len(issues)} issues found, "
          f"{len(correction_log)} safe corrections applied.")
    print(f"Outputs written to: {Path(out_dir).resolve()}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="M4S Field Data QA Checker")
    parser.add_argument("input_path", help="Raw Kobo export (.xlsx)")
    parser.add_argument("output_dir", nargs="?", default="output",
                         help="Output directory (default: ./output)")
    parser.add_argument("--config", default=None,
                         help="Path to a project config.yaml "
                              "(default: config.yaml next to this script)")
    args = parser.parse_args()
    run(args.input_path, args.output_dir, args.config)
