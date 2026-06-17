#!/usr/bin/env python3
"""Classify hot leads from Kajabi deliveries CSV exports."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_COLUMNS = {"contact_email", "delivered_at", "opened_at", "clicked_at"}
RECOMMENDED_COLUMNS = {
    "contact_subscribed",
    "bounced_at",
    "dropped_at",
    "complained_at",
    "temporary_failure_at",
}


DEFAULT_CONFIG: dict[str, Any] = {
    "scoring": {
        "open_points": 5,
        "click_points": 15,
        "hot_lead_threshold": 35,
    },
    "recency_multiplier": {
        "days_0_14": 2.0,
        "days_15_30": 1.5,
        "days_31_90": 1.0,
        "over_90_days": 0,
    },
    "deduplication": {
        "max_open_points_per_email_per_contact": True,
        "max_click_points_per_email_per_contact": True,
    },
    "lookback": {"days": 90},
    "tag": {"name": "hot-lead", "apply_to_kajabi": False},
    "output": {"include_audit_columns": True},
    "archive": {
        "processed_exports": True,
        "directory": "archive",
    },
}


@dataclass
class LeadStats:
    email: str
    score: float = 0.0
    delivered_sources: set[str] = field(default_factory=set)
    opened_sources: set[str] = field(default_factory=set)
    clicked_sources: set[str] = field(default_factory=set)
    last_engagement_at: datetime | None = None
    warnings: set[str] = field(default_factory=set)

    def add_engagement(self, action: str, source_key: str, points: float, occurred_at: datetime) -> None:
        self.score += points
        if action == "open":
            self.opened_sources.add(source_key)
        elif action == "click":
            self.clicked_sources.add(source_key)
        if self.last_engagement_at is None or occurred_at > self.last_engagement_at:
            self.last_engagement_at = occurred_at


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return DEFAULT_CONFIG
    with path.open("r", encoding="utf-8") as f:
        user_config = json.load(f)
    return deep_merge(DEFAULT_CONFIG, user_config)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_run_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError(f"Invalid --run-date value: {value}")
    return parsed


def discover_csv_files(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.csv")))
        elif path.is_file() and path.suffix.lower() == ".csv":
            files.append(path)
    return sorted(set(files))


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def has_value(row: dict[str, str], column: str) -> bool:
    return bool(str(row.get(column, "")).strip())


def should_exclude(row: dict[str, str], available_columns: set[str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if "contact_subscribed" in available_columns and not truthy(row.get("contact_subscribed")):
        reasons.append("not_subscribed")
    for column, reason in [
        ("bounced_at", "bounced"),
        ("dropped_at", "dropped"),
        ("complained_at", "complained"),
        ("temporary_failure_at", "temporary_failure"),
    ]:
        if column in available_columns and has_value(row, column):
            reasons.append(reason)
    return bool(reasons), reasons


def multiplier_for(occurred_at: datetime, run_date: datetime, config: dict[str, Any]) -> float:
    age_days = (run_date - occurred_at).total_seconds() / 86400
    recency = config["recency_multiplier"]
    if age_days < 0:
        return float(recency["days_0_14"])
    if age_days <= 14:
        return float(recency["days_0_14"])
    if age_days <= 30:
        return float(recency["days_15_30"])
    if age_days <= int(config["lookback"]["days"]):
        return float(recency["days_31_90"])
    return float(recency["over_90_days"])


def action_points(action: str, occurred_at: datetime, run_date: datetime, config: dict[str, Any]) -> float:
    scoring = config["scoring"]
    base = float(scoring["open_points"] if action == "open" else scoring["click_points"])
    return base * multiplier_for(occurred_at, run_date, config)


def iso_or_blank(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def score_files(csv_files: list[Path], config: dict[str, Any], run_date: datetime) -> tuple[list[LeadStats], dict[str, Any]]:
    leads: dict[str, LeadStats] = {}
    warnings: list[str] = []
    excluded_contacts: set[str] = set()
    unique_delivered: set[str] = set()
    rows_read = 0
    rows_ignored_outside_lookback = 0
    rows_excluded = 0
    open_seen: set[tuple[str, str]] = set()
    click_seen: set[tuple[str, str]] = set()

    lookback_days = int(config["lookback"]["days"])
    min_date = run_date.timestamp() - (lookback_days * 86400)

    for csv_file in csv_files:
        with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            columns = set(reader.fieldnames or [])
            missing_required = REQUIRED_COLUMNS - columns
            if missing_required:
                warnings.append(f"{csv_file}: missing required columns {sorted(missing_required)}; file skipped")
                continue
            missing_recommended = RECOMMENDED_COLUMNS - columns
            if missing_recommended:
                warnings.append(f"{csv_file}: missing recommended exclusion columns {sorted(missing_recommended)}")

            for row_number, row in enumerate(reader, start=2):
                rows_read += 1
                email = str(row.get("contact_email", "")).strip().lower()
                if not email:
                    warnings.append(f"{csv_file}:{row_number}: missing contact_email; row skipped")
                    continue

                delivered_at = parse_datetime(row.get("delivered_at"))
                if delivered_at is None:
                    warnings.append(f"{csv_file}:{row_number}: invalid delivered_at; row skipped")
                    continue
                if delivered_at.timestamp() < min_date:
                    rows_ignored_outside_lookback += 1
                    continue

                source_key = f"{csv_file.name}"
                lead = leads.setdefault(email, LeadStats(email=email))
                lead.delivered_sources.add(source_key)
                unique_delivered.add(email)

                exclude, reasons = should_exclude(row, columns)
                if exclude:
                    rows_excluded += 1
                    excluded_contacts.add(email)
                    lead.warnings.update(reasons)
                    continue

                opened_at = parse_datetime(row.get("opened_at"))
                clicked_at = parse_datetime(row.get("clicked_at"))

                if opened_at is not None:
                    key = (email, source_key)
                    if not config["deduplication"]["max_open_points_per_email_per_contact"] or key not in open_seen:
                        points = action_points("open", opened_at, run_date, config)
                        if points > 0:
                            lead.add_engagement("open", source_key, points, opened_at)
                        open_seen.add(key)

                if clicked_at is not None:
                    key = (email, source_key)
                    if not config["deduplication"]["max_click_points_per_email_per_contact"] or key not in click_seen:
                        points = action_points("click", clicked_at, run_date, config)
                        if points > 0:
                            lead.add_engagement("click", source_key, points, clicked_at)
                        click_seen.add(key)

    threshold = float(config["scoring"]["hot_lead_threshold"])
    hot_leads = [
        lead
        for email, lead in leads.items()
        if email not in excluded_contacts and lead.score >= threshold
    ]
    hot_leads.sort(key=lambda lead: (lead.score, lead.last_engagement_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)

    summary = {
        "csv_files": len(csv_files),
        "rows_read": rows_read,
        "unique_delivered_contacts": len(unique_delivered),
        "excluded_contacts": len(excluded_contacts),
        "rows_excluded": rows_excluded,
        "rows_ignored_outside_lookback": rows_ignored_outside_lookback,
        "hot_leads": len(hot_leads),
        "threshold": threshold,
        "warnings": warnings,
    }
    return hot_leads, summary


def write_outputs(
    hot_leads: list[LeadStats],
    summary: dict[str, Any],
    config: dict[str, Any],
    output_dir: Path,
    run_date: datetime,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    csv_path = output_dir / f"hot-leads-{stamp}.csv"
    report_path = output_dir / f"hot-leads-report-{stamp}.md"

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        tag_name = str(config["tag"]["name"])
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "email",
                "tag",
            ],
        )
        writer.writeheader()
        for lead in hot_leads:
            writer.writerow(
                {
                    "email": lead.email,
                    "tag": tag_name,
                }
            )

    warning_lines = "\n".join(f"- {warning}" for warning in summary["warnings"]) or "- No warnings."
    config_json = json.dumps(config, indent=2, ensure_ascii=False)
    report = f"""# Hot Leads Report - {run_date.date().isoformat()}

## Summary

- CSV files analyzed: {summary["csv_files"]}
- Rows read: {summary["rows_read"]}
- Unique delivered contacts: {summary["unique_delivered_contacts"]}
- Excluded contacts: {summary["excluded_contacts"]}
- Excluded rows: {summary["rows_excluded"]}
- Rows ignored outside lookback: {summary["rows_ignored_outside_lookback"]}
- Hot leads found: {summary["hot_leads"]}
- Threshold used: {summary["threshold"]}
- Operational tag: {config["tag"]["name"]}

## Output

- Hot leads CSV: `{csv_path}`

## Hot Lead Details

| Email | Score | Delivered | Opened | Clicked | Last engagement | Warnings |
| --- | ---: | ---: | ---: | ---: | --- | --- |
{chr(10).join(
    f"| {lead.email} | {round(lead.score, 2)} | {len(lead.delivered_sources)} | {len(lead.opened_sources)} | {len(lead.clicked_sources)} | {iso_or_blank(lead.last_engagement_at)} | {';'.join(sorted(lead.warnings)) or '-'} |"
    for lead in hot_leads
) or "| - | - | - | - | - | - | - |"}

## Warnings

{warning_lines}

## Configuration Used

```json
{config_json}
```
"""
    report_path.write_text(report, encoding="utf-8")
    return csv_path, report_path


def archive_processed_files(csv_files: list[Path], config: dict[str, Any]) -> list[tuple[Path, Path]]:
    archive_config = config.get("archive", {})
    if not archive_config.get("processed_exports", False):
        return []

    archive_dir = Path(str(archive_config["directory"]))
    if not archive_dir.is_absolute():
        archive_dir = csv_files[0].resolve().parent / archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    moved: list[tuple[Path, Path]] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    for source in csv_files:
        resolved_source = source.resolve()
        resolved_archive = archive_dir.resolve()
        if resolved_archive in resolved_source.parents:
            continue

        target = archive_dir / source.name
        if target.exists():
            target = archive_dir / f"{source.stem}-{stamp}{source.suffix}"
        shutil.move(str(source), str(target))
        moved.append((source, target))
    return moved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify hot leads from Kajabi deliveries CSV exports.")
    parser.add_argument("--input", nargs="+", required=True, help="CSV file(s) or folder(s) containing CSV exports.")
    parser.add_argument("--output-dir", required=True, help="Directory where CSV and report will be written.")
    parser.add_argument("--config", help="Path to config.json. Defaults are used if omitted.")
    parser.add_argument("--run-date", help="ISO date/datetime used as scoring reference. Defaults to now UTC.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = load_config(Path(args.config) if args.config else None)
    run_date = parse_run_date(args.run_date)
    csv_files = discover_csv_files(args.input)
    if not csv_files:
        parser.error("No CSV files found in --input")

    hot_leads, summary = score_files(csv_files, config, run_date)
    csv_path, report_path = write_outputs(hot_leads, summary, config, Path(args.output_dir), run_date)
    archived_files = archive_processed_files(csv_files, config)

    print(f"CSV files analyzed: {summary['csv_files']}")
    print(f"Unique delivered contacts: {summary['unique_delivered_contacts']}")
    print(f"Hot leads found: {summary['hot_leads']}")
    print(f"Hot leads CSV: {csv_path}")
    print(f"Report: {report_path}")
    print(f"Archived CSV files: {len(archived_files)}")
    if summary["warnings"]:
        print(f"Warnings: {len(summary['warnings'])} (see report)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
