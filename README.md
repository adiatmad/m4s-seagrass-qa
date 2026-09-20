# M4S Seagrass QA

A QA pipeline and Streamlit dashboard for cleaning and validating [KoboToolbox](https://www.kobotoolbox.org/) field survey exports — built for the **M4S Seagrass Monitoring** project in Metinaro (Sabuli village), Timor-Leste, and reusable for other Kobo-based ecological surveys via a config file.

It turns a raw Kobo `.xlsx` export into:
- A clean, corrected dataset
- A list of every QA issue found, with severity and a plain-language message
- A markdown QA report
- A per-enumerator quality breakdown
- A run history so quality trends across survey rounds are visible, not just a single snapshot

## Features

- **Validation rules**: missing/invalid GPS, GPS precision thresholds, points outside the survey boundary, duplicate submissions, species-presence/percent-cover logic errors, percent-cover range checks, missing mandatory fields, implausible survey durations, missing photos, and species-cover-vs-epicover mismatches.
- **Safe auto-correction**: enumerator name and geography typos are fuzzy-matched and normalized, with every change logged. Scientific and GPS values are only ever flagged, never auto-corrected.
- **Enumerator quality scoring**: per-enumerator record counts, error/warning rates, and issue breakdown by category — so retraining is targeted instead of blanket.
- **Configurable rules** (`config.yaml`): species list, site geography, GPS boundary, and all QA thresholds live in one YAML file. Point the same pipeline at a different site or survey by swapping the config, not the code.
- **QA history**: aggregate stats from each run (never raw survey rows) are logged to a local SQLite file, so error rates — overall and per enumerator — can be tracked across survey rounds over time.
- **Streamlit dashboard**: upload an export, filter by QA flag, browse every tab (report, issues, corrections, clean data, enumerator quality, history), and download every output as CSV/Markdown/SQLite.

## Project structure

```
m4s-seagrass-qa/
├── m4s_seagrass_qa.py   # Pipeline: load → standardize → validate → correct → report → export
├── m4s_app.py           # Streamlit front end
├── config.yaml          # Species list, site geography, QA thresholds
├── requirements.txt
└── README.md
```

## Quickstart

```bash
git clone https://github.com/<your-username>/m4s-seagrass-qa.git
cd m4s-seagrass-qa
pip install -r requirements.txt
```

### Run the dashboard

```bash
streamlit run m4s_app.py
```

Upload a raw Kobo `.xlsx` export in the browser. Nothing is saved server-side — outputs are yours to download each session. The one exception is QA history: since there's no server-side storage, you carry that log yourself by uploading last session's `qa_history.db` in the sidebar and downloading the updated one at the end. Skip that on your very first run.

### Run from the command line

```bash
python3 m4s_seagrass_qa.py raw_export.xlsx [output_dir]
```

Outputs land in `./output/` by default: `clean_dataset.csv`, `qa_issues.csv`, `correction_log.csv`, `enumerator_summary.csv`, `qa_report.md`, `raw_with_qa_flags.csv`, and `qa_history.db` (which accumulates automatically across repeated runs into the same `output_dir`, since that's your own machine).

## Configuration

All project-specific values live in `config.yaml`:

```yaml
species_list: [...]
geography:
  admin_post: Metinaro
  village: Sabuli
site:
  centroid: [-8.51967, 125.7174723]
  radius_m: 500
thresholds:
  enumerator_match: 85
  gps_precision_warn_m: 10
  gps_precision_error_m: 30
  # ...
```

To run this same pipeline against a different site or survey, copy `config.yaml`, edit the values, and either:
- pass it on the command line: `python3 m4s_seagrass_qa.py export.xlsx --config my_config.yaml`
- or upload it in the Streamlit sidebar

If no config file is found, the pipeline falls back to the built-in M4S Seagrass defaults, so it still runs out of the box.

## Deploying to Streamlit Community Cloud

1. Push this repo to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. Click **New app**, select this repo/branch, and set the main file path to `m4s_app.py`.
4. Deploy. `requirements.txt` is picked up automatically.

Since the app doesn't persist anything server-side by design (see above), no additional storage or secrets setup is needed for a basic deployment.

## Data notes

- Species list, form field names, and column references are matched to the M4S Seagrass Kobo form. If the form changes, `load_data()` will raise a clear error naming the missing columns rather than silently guessing.
- QA history stores aggregate counts only (record totals, error/warning counts by category, enumerator summary rows) — never raw survey data or GPS coordinates.

## License

Add a license before making this repo public if you intend others to reuse it — none is set yet.

## AI-Assisted Development

This project was developed and/or maintained with AI assistance. AI was used to support parts of the design, implementation, documentation, and/or maintenance workflow. The human maintainer remains responsible for reviewing, validating, and approving the project's code and outputs.
