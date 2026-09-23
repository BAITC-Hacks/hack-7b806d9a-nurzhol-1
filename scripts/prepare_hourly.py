#!/usr/bin/env python3
"""Build a conservative hourly dataset from the two supplied turbine CSVs.

Timestamps retain their source wall-clock labels, confirmed by the user as
fixed UTC+5. Each timestamp is assumed to start its ten-minute interval:
00:00 through 00:50 form the hour labelled 00:00. The interval convention
remains unconfirmed.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


GRID_START = "2023-03-11 00:00:00"
GRID_END = "2026-01-31 23:00:00"
VALIDATION_START = "2026-01-01 00:00:00"
SOURCE_COLUMNS = [
    "ID",
    "Статистическое время",
    "Средняя скорость ветра(m/s)",
    "Нормализованная активная мощность",
    "Средняя температура окружающей среды(°C)",
]
VALUE_COLUMNS = {
    "wind": "wind_speed_ms",
    "power": "power_normalized",
    "temp": "temperature_c",
}
OUTPUT_COLUMNS = [
    "time_naive", "turbine_id", "wind_speed_ms", "power_normalized",
    "temperature_c", "observations", "is_complete", "split",
]


def aggregate_frame(frame, turbine_id, start=GRID_START, end=GRID_END):
    """Return one turbine's full hourly grid without modifying ``frame``.

    Input columns are ``time``, ``wind``, ``power`` and ``temp``. Extra columns
    (such as the original row ID) are ignored. A complete hour contains exactly
    six distinct readings on :00/:10/:20/:30/:40/:50, with all three measurements
    finite. Every incomplete hour retains its count but all three means are NaN.
    Duplicate timestamps or readings outside the requested grid raise errors.
    """
    work = frame[["time", *VALUE_COLUMNS]].copy()
    work["time"] = pd.to_datetime(work["time"], format="%Y-%m-%d %H:%M:%S", errors="raise")
    if work.time.isna().any():
        raise ValueError(f"Turbine {turbine_id}: missing timestamp")
    if work.time.dt.tz is not None:
        raise ValueError("Source timestamps must retain naive UTC+5 wall-clock labels")
    duplicate = work.time.duplicated(keep=False)
    if duplicate.any():
        examples = work.loc[duplicate, "time"].astype(str).unique()[:3]
        raise ValueError(f"Duplicate timestamps for turbine {turbine_id}: {', '.join(examples)}")

    grid = pd.date_range(start, end, freq="h", name="time_naive")
    if len(grid) == 0 or grid.tz is not None or grid[0] != grid[0].floor("h") or grid[-1] != pd.Timestamp(end):
        raise ValueError("Grid bounds must be ordered, naive, whole-hour timestamps")
    outside = (work.time < grid[0]) | (work.time >= grid[-1] + pd.Timedelta(hours=1))
    if outside.any():
        raise ValueError(f"Turbine {turbine_id}: {int(outside.sum())} readings outside requested hourly grid")

    work = work.sort_values("time")
    work[list(VALUE_COLUMNS)] = work[list(VALUE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    work["_finite"] = np.isfinite(work[list(VALUE_COLUMNS)]).all(axis=1)
    work["_aligned"] = work.time.eq(work.time.dt.floor("10min"))
    work["_hour"] = work.time.dt.floor("h")
    groups = work.groupby("_hour", sort=True)

    result = groups[list(VALUE_COLUMNS)].mean().rename(columns=VALUE_COLUMNS).reindex(grid)
    result["observations"] = groups.size().reindex(grid, fill_value=0).astype(int)
    valid = groups["_finite"].all() & groups["_aligned"].all()
    result["is_complete"] = result.observations.eq(6) & valid.reindex(grid, fill_value=False)
    result.loc[~result.is_complete, list(VALUE_COLUMNS.values())] = np.nan
    result["turbine_id"] = turbine_id
    result["split"] = np.where(result.index < pd.Timestamp(VALIDATION_START), "train", "validation")
    return result.reset_index()[OUTPUT_COLUMNS]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--turbine1", type=Path, required=True, help="Original turbine 1 CSV (read only)")
    parser.add_argument("--turbine2", type=Path, required=True, help="Original turbine 2 CSV (read only)")
    parser.add_argument("--output", type=Path, default=Path("data/processed/hourly.csv"))
    parser.add_argument("--summary", type=Path, default=Path("data/processed/hourly_summary.json"))
    args = parser.parse_args(argv)
    sources = [args.turbine1.resolve(), args.turbine2.resolve()]
    output, summary_path = args.output.resolve(), args.summary.resolve()
    if output in sources or summary_path in sources or output == summary_path:
        parser.error("Output and summary must be distinct files and cannot overwrite either raw input")
    if sources[0] == sources[1]:
        parser.error("Turbine 1 and turbine 2 must use different source files")

    hourly_frames, turbine_reports = [], []
    for turbine_id, source in enumerate(sources, start=1):
        raw = pd.read_csv(source)
        if list(raw.columns) != SOURCE_COLUMNS:
            raise ValueError(f"Unexpected source columns in {source}: {list(raw.columns)}")
        raw.columns = ["ID", "time", "wind", "power", "temp"]
        hourly = aggregate_frame(raw, turbine_id)
        hourly_frames.append(hourly)
        timestamps = pd.to_datetime(raw.time, format="%Y-%m-%d %H:%M:%S")
        split_reports = {}
        for split, subset in hourly.groupby("split"):
            split_reports[split] = {
                "hours": len(subset),
                "complete_hours": int(subset.is_complete.sum()),
                "incomplete_hours": int((~subset.is_complete).sum()),
                "missing_hours": int(subset.observations.eq(0).sum()),
            }
        turbine_reports.append({
            "turbine_id": turbine_id,
            "source_path": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "source_rows": len(raw),
            "source_start": str(timestamps.min()),
            "source_end": str(timestamps.max()),
            "source_was_sorted": bool(timestamps.is_monotonic_increasing),
            "hours": len(hourly),
            "complete_hours": int(hourly.is_complete.sum()),
            "incomplete_hours": int((~hourly.is_complete).sum()),
            "missing_hours": int(hourly.observations.eq(0).sum()),
            "partial_hours": int(hourly.observations.between(1, 5).sum()),
            "observation_count_histogram": {str(k): int(v) for k, v in hourly.observations.value_counts().sort_index().items()},
            "splits": split_reports,
        })

    combined = pd.concat(hourly_frames, ignore_index=True).sort_values(["time_naive", "turbine_id"])
    report = {
        "output_path": str(output),
        "output_rows": len(combined),
        "complete_hours": int(combined.is_complete.sum()),
        "incomplete_hours": int((~combined.is_complete).sum()),
        "grid_start": GRID_START,
        "grid_end": GRID_END,
        "validation_start": VALIDATION_START,
        "timezone": "UTC+05:00 fixed offset; source wall-clock labels preserved without conversion",
        "timezone_confirmed": True,
        "source_utc_offset_hours": 5,
        "timezone_confirmation_source": "user: время +5",
        "timestamp_interval_convention": "assumed start-of-interval; H:00 through H:50 belong to hour H:00; source convention unconfirmed",
        "aggregation": "arithmetic mean for wind, normalized power, and temperature; power is not summed",
        "complete_hour_rule": "exactly 6 unique timestamps on the 10-minute grid, with all 3 measurements finite at every timestamp",
        "incomplete_hour_policy": "retain hourly row and observation count; all 3 aggregate values are blank/NaN; no imputation",
        "split_rule": "train before 2026-01-01 00:00:00; validation is January 2026",
        "source_coverage_note": "Filename mentions February 2026; supplied observations end in January 2026. No February observations are generated.",
        "columns": OUTPUT_COLUMNS,
        "turbines": turbine_reports,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output, index=False, date_format="%Y-%m-%d %H:%M:%S")
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(combined):,} hourly rows; {report['complete_hours']:,} complete, {report['incomplete_hours']:,} incomplete.")
    print(f"Dataset: {output}\nSummary: {summary_path}")


if __name__ == "__main__":
    main()
