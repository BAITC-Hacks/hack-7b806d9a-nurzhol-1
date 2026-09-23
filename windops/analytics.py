"""Read-only forecast revisions, forecast events, and January model validation."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path


LOCAL_TIMEZONE = timezone(timedelta(hours=5))
HORIZONS = {"1-24": (1, 24), "25-48": (25, 48)}


def _utc(value):
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compare_forecasts(service, issue_date, forecast=None):
    """Compare the selected issue with yesterday, joining the same target hours."""
    previous_date = (date.fromisoformat(issue_date) - timedelta(days=1)).isoformat()
    if previous_date not in service.list_issues():
        raise ValueError(f"Нет предыдущего выпуска за {previous_date} для сравнения прогнозов.")
    newer = service.get_forecast(issue_date) if forecast is None else forecast
    if newer["issue_date"] != issue_date:
        raise ValueError("Дата переданного прогноза не совпадает с выбранным выпуском.")
    older = service.get_forecast(previous_date)
    old_rows = {(r["turbine_id"], _utc(r["valid_time_utc"])): r for r in older["rows"]}
    rows = []
    for new in sorted(newer["rows"], key=lambda r: (r["turbine_id"], _utc(r["valid_time_utc"]))):
        valid = _utc(new["valid_time_utc"])
        old = old_rows.get((new["turbine_id"], valid))
        if old is None:
            continue
        rows.append({
            "turbine_id": new["turbine_id"], "valid_time_utc": valid.isoformat(),
            "valid_time_local": valid.astimezone(LOCAL_TIMEZONE).isoformat(),
            "old_power": old["power_normalized"], "new_power": new["power_normalized"],
            "delta_power": new["power_normalized"] - old["power_normalized"],
            "old_wind": old["wind_speed_ms"], "new_wind": new["wind_speed_ms"],
            "delta_wind": new["wind_speed_ms"] - old["wind_speed_ms"],
            "old_lead_hours": old["lead_hours"], "new_lead_hours": new["lead_hours"],
        })
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["turbine_id"]].append(row)
    summaries = []
    for turbine, group in grouped.items():
        largest = max(group, key=lambda row: abs(row["delta_power"]))
        summaries.append({"turbine_id": turbine, "count": len(group),
            "mean_delta_power": math.fsum(row["delta_power"] for row in group) / len(group),
            "max_abs_delta_power": abs(largest["delta_power"]),
            "max_change_time_local": largest["valid_time_local"]})
    return {
        "newer": {key: deepcopy(newer[key]) for key in ("issue_date", "model", "source")},
        "older": {key: deepcopy(older[key]) for key in ("issue_date", "model", "source")},
        "rows": rows, "summary": summaries,
        "caveat": "Это пересмотр прогноза, не улучшение точности: фактическая выработка за февраль отсутствует.",
    }


def forecast_events(forecast):
    """Describe up to three events per turbine from actual forecast rows.

    Hour indices are zero based within each turbine's chronologically sorted rows.
    Low intervals include both endpoint rows; gaps break hourly sequences.
    """
    grouped = defaultdict(list)
    for row in forecast["rows"]:
        grouped[row["turbine_id"]].append(row)
    events = []
    for turbine in sorted(grouped):
        rows = sorted(grouped[turbine], key=lambda row: _utc(row["valid_time_utc"]))
        times = [_utc(row["valid_time_utc"]) for row in rows]
        powers = [row["power_normalized"] for row in rows]

        def event(kind, start, end, title, detail, **values):
            return {"id": f"turbine-{turbine}-{kind}-{times[start].isoformat()}",
                    "kind": kind, "turbine_id": turbine, "start_hour": start, "end_hour": end,
                    "start_time_utc": times[start].isoformat(), "end_time_utc": times[end].isoformat(),
                    "title": title, "detail": detail, **values}

        drops = [(powers[i] - powers[i - 1], i) for i in range(1, len(rows))
                 if times[i] - times[i - 1] == timedelta(hours=1)]
        if drops:
            delta, end = min(drops)
            if delta <= -.1 or math.isclose(delta, -.1, rel_tol=0, abs_tol=1e-12):
                events.append(event("drop", end - 1, end, "Снижение выработки",
                    f"За час: {powers[end-1]:.3f} → {powers[end]:.3f}; изменение {delta:+.3f} по шкале 0–1.",
                    start_power=powers[end-1], end_power=powers[end], delta_power=delta))

        longest = []
        current = []
        for i, power in enumerate(powers):
            if power < .1:
                if current and times[i] - times[current[-1]] != timedelta(hours=1):
                    current = []
                current.append(i)
                if len(current) > len(longest):
                    longest = current.copy()
            else:
                current = []
        if len(longest) >= 3:
            low_powers = [powers[i] for i in longest]
            events.append(event("low", longest[0], longest[-1], "Низкая выработка",
                f"Ниже 0.1 по шкале 0–1 в течение {len(longest)} последовательных часов.",
                threshold=.1, duration_hours=len(longest), min_power=min(low_powers), max_power=max(low_powers)))
        if rows:
            peak = max(range(len(rows)), key=lambda i: powers[i])
            events.append(event("peak", peak, peak, "Пик выработки",
                f"Максимум прогноза: {powers[peak]:.3f} по шкале 0–1.", power_normalized=powers[peak]))
    return events


def _metrics(rows):
    count = len(rows)
    return {"count": count, "unique_hours": len({row["valid_time_utc"] for row in rows}),
            "mae": math.fsum(row["abs_error"] for row in rows) / count if count else None,
            "rmse": math.sqrt(math.fsum(row["error"] ** 2 for row in rows) / count) if count else None}


def january_validation(root, turbine_id=1, horizon="1-24"):
    """Read eligible empirical-curve January cases without retraining the model."""
    if isinstance(turbine_id, bool) or turbine_id not in (1, 2):
        raise ValueError("Выберите турбину 1 или 2.")
    if horizon not in HORIZONS:
        raise ValueError("Горизонт должен быть 1-24 или 25-48 часов.")
    cases = {}
    with (Path(root) / "outputs/baseline/predictions.csv").open(newline="", encoding="utf-8-sig") as stream:
        for source in csv.DictReader(stream):
            if (source.get("model") != "empirical_curve"
                    or str(source.get("score_eligible", "")).strip().lower() != "true"
                    or str(source.get("target_hour_complete", "")).strip().lower() != "true"):
                continue
            try:
                if float(source["utc_offset_hours"]) != 5 or float(source["turbine_id"]) != turbine_id:
                    continue
                actual = float(source["actual_power_normalized"])
                predicted = float(source["predicted_power_normalized"])
                lead = float(source["lead_hours"])
                if not (math.isfinite(actual) and math.isfinite(predicted)
                        and lead.is_integer() and 1 <= lead <= 48):
                    continue
                valid = _utc(source["valid_time_utc"])
                origin = _utc(source["forecast_origin_utc"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            local = valid.astimezone(LOCAL_TIMEZONE)
            if (local.year, local.month) != (2026, 1):
                continue
            error = predicted - actual
            row = {"turbine_id": turbine_id, "forecast_origin_utc": origin.isoformat(),
                   "valid_time_utc": valid.isoformat(), "valid_time_local": local.isoformat(),
                   "lead_hours": int(lead), "actual": actual, "predicted": predicted,
                   "error": error, "abs_error": abs(error)}
            key = (origin, turbine_id, valid)
            if key in cases and cases[key] != row:
                raise ValueError("Дубликат январского прогноза содержит противоречивые значения.")
            cases[key] = row
    all_rows = sorted(cases.values(), key=lambda row: (row["valid_time_utc"], row["forecast_origin_utc"]))
    by_horizon = {name: [row for row in all_rows if start <= row["lead_hours"] <= end]
                  for name, (start, end) in HORIZONS.items()}
    selected = by_horizon[horizon]
    return {"model": {"id": "empirical_curve", "label": "Эмпирическая кривая мощности"},
            "caveat": "Январь использован для выбора модели; это не независимый тест качества.",
            "turbine_id": turbine_id, "horizon": horizon, "rows": selected,
            "metrics": _metrics(selected), "days": sorted({row["valid_time_local"][:10] for row in selected}),
            "metrics_by_horizon": [{"horizon": name, **_metrics(rows)} for name, rows in by_horizon.items()]}
