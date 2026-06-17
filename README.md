# Hot Leads Classifier

A lightweight AI agent with Python command-line tool that classifies Kajabi contacts based on their engagement with email broadcasts.

It reads Kajabi email delivery CSV exports, analyzes each contact’s opens, clicks, and recent activity, then scores them to identify warm and hot leads. Contacts who pass the configured threshold are exported into a Kajabi-friendly CSV, ready to be tagged or used in follow-up workflows.

## Features

- Reads a single CSV file or a folder of CSV exports.
- Uses `contact_email` as the unique contact identifier.
- Excludes non-contactable records when exclusion fields are available.
- Scores opens and clicks with configurable point values.
- Weights recent engagement more heavily.
- Ignores engagement outside the configured lookback window.
- Writes a hot-leads CSV and a Markdown report.
- Optionally archives processed exports after a successful run.

## Input Format

Expected Kajabi CSV columns:

```csv
contact_id,contact_email,contact_subscribed,delivered_at,opened_at,clicked_at,bounced_at,dropped_at,complained_at,temporary_failure_at
```

Required columns:

- `contact_email`
- `delivered_at`
- `opened_at`
- `clicked_at`

Recommended columns for exclusions:

- `contact_subscribed`
- `bounced_at`
- `dropped_at`
- `complained_at`
- `temporary_failure_at`

If a recommended column is missing, the script still runs and records a warning in the report.

## Configuration

Edit `config.json`.

Default scoring:

- open: `5` points
- click: `15` points
- hot-lead threshold: `35`
- last 14 days: `2.0x` multiplier
- days 15-30: `1.5x` multiplier
- days 31-90: `1.0x` multiplier
- older than 90 days: ignored

By default, processed CSV files are moved into an `archive` folder next to the input CSV files.

## Quick Start

1. Place Kajabi delivery CSV exports in `exports-kajabi/`.
2. Run the classifier:

```powershell
py .\hot_leads_classifier.py `
  --input .\exports-kajabi `
  --output-dir .\output `
  --config .\config.json
```

On macOS or Linux:

```bash
python3 hot_leads_classifier.py \
  --input exports-kajabi \
  --output-dir output \
  --config config.json
```

The script creates:

- `output/hot-leads-YYYYMMDD-HHMMSS.csv`
- `output/hot-leads-report-YYYYMMDD-HHMMSS.md`

Processed CSV exports are archived in:

- `exports-kajabi/archive/`

## Output CSV

```csv
email,tag
```

The CSV is intentionally kept import-friendly for Kajabi. The `tag` value comes from `tag.name` in `config.json`. The default is `hot-lead`.

Scoring details, engagement counts, timestamps, and warnings are included in the Markdown report instead of the import CSV.

## CLI Options

```text
--input       One or more CSV files or folders containing CSV exports.
--output-dir  Directory where the CSV and Markdown report will be written.
--config      Optional path to config.json. Built-in defaults are used if omitted.
--run-date    Optional ISO date/datetime used as the scoring reference. Defaults to now UTC.
```

Example with multiple inputs:

```bash
python3 hot_leads_classifier.py \
  --input exports-kajabi/june.csv exports-kajabi/july.csv \
  --output-dir output \
  --config config.json
```

## Kajabi Tagging

The operational tag name is:

```text
hot-lead
```

This script does not modify Kajabi and does not apply tags automatically. It only produces a ready-to-use list of contacts.

If automatic Kajabi tagging is added later, the workflow should:

1. verify or create the `hot-lead` tag;
2. find each contact by email;
3. apply the tag;
4. report tagged contacts, missing contacts, and errors.

Any workflow that modifies Kajabi should require explicit confirmation before running.

## Future Optimization

To tune scoring against real sales outcomes, add an outcome export such as:

```csv
contact_email,entered_hot_sequence_at,purchased_at,purchase_amount,closed
email@example.com,2026-06-01T09:00:00Z,2026-06-05T18:10:00Z,247,true
```

Then compare:

- conversion rate by score band;
- revenue by score band;
- average time from tag to purchase;
- false positives;
- false negatives;
- the best threshold for the hot-lead sequence.
