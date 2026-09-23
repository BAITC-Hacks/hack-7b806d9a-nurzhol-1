#!/usr/bin/env python3
"""Leakage-safe per-turbine baselines using archived, as-issued NOAA forecasts.

Hourly labels use UTC+5, confirmed by the user. Other UTC offsets are optional
sensitivity checks. The frozen training cutoff predates the earliest validation
issue under every supported offset. January target weather never enters a model.
"""
from __future__ import annotations

import argparse
from datetime import timedelta, timezone
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd


TRAINING_CUTOFF = pd.Timestamp("2025-12-31 00:00:00")
VALIDATION_START = pd.Timestamp("2026-01-01")
VALIDATION_END = pd.Timestamp("2026-02-01")
FIRST_ORIGIN = pd.Timestamp("2025-12-31 18:00:00", tz="UTC")
DEMO_ORIGIN = pd.Timestamp("2026-01-31 18:00:00", tz="UTC")
SOURCE_UTC_OFFSET_HOURS = 5
SOURCE_TIMEZONE = timezone(timedelta(hours=SOURCE_UTC_OFFSET_HOURS))
WEATHER_COLUMNS = [
    "turbine_id", "forecast_origin_utc", "run_time_utc", "valid_time_utc",
    "lead_hours", "wind_speed_100m_ms", "temperature_2m_c",
    "wind_direction_100m_deg", "available_at_utc",
]


class EmpiricalPowerCurve:
    """Mean normalized power in 0.5 m/s bins, linearly interpolated at centers.

    Outside occupied bin centers the nearest endpoint mean is held constant;
    this is deliberately not a claimed physical cut-in/cut-out power curve.
    """

    def __init__(self, bin_width=0.5):
        if not np.isfinite(bin_width) or bin_width <= 0:
            raise ValueError("bin_width must be positive")
        self.bin_width = float(bin_width)

    def fit(self, wind, power):
        wind, power = np.asarray(wind, dtype=float), np.asarray(power, dtype=float)
        if (len(wind) == 0 or len(wind) != len(power)
                or not np.isfinite(wind).all() or not np.isfinite(power).all()
                or (wind < 0).any() or ((power < 0) | (power > 1)).any()):
            raise ValueError("Training wind/power must be finite, nonnegative, and power in [0,1]")
        bins = np.floor(wind / self.bin_width).astype(int)
        self.bins = pd.DataFrame({"bin_index": bins, "power": power}).groupby("bin_index").power.agg(["mean", "count"])
        self.centers = (self.bins.index.to_numpy() + 0.5) * self.bin_width
        self.means = self.bins["mean"].to_numpy()
        self.wind_min, self.wind_max = float(wind.min()), float(wind.max())
        self.power_min, self.power_max = float(power.min()), float(power.max())
        self.rare_low, self.rare_high = np.quantile(wind, [0.01, 0.99]).tolist()
        return self

    def predict(self, wind, temperature=None):
        return np.clip(np.interp(np.asarray(wind, dtype=float), self.centers, self.means), 0, 1)

    def outside_training_range(self, wind):
        wind = np.asarray(wind, dtype=float)
        return (wind < self.wind_min) | (wind > self.wind_max)

    def sparse_bin(self, wind):
        bins = np.floor(np.asarray(wind, dtype=float) / self.bin_width).astype(int)
        return self.bins["count"].reindex(bins, fill_value=0).to_numpy() < 20

    def to_dict(self):
        return {"bin_width_ms": self.bin_width, "bin_centers_ms": self.centers.tolist(),
                "bin_mean_power_normalized": self.means.tolist(),
                "bin_counts": self.bins["count"].tolist(),
                "wind_min_ms": self.wind_min, "wind_max_ms": self.wind_max,
                "power_min": self.power_min, "power_max": self.power_max,
                "wind_percentile_1_ms": self.rare_low, "wind_percentile_99_ms": self.rare_high,
                "interpolation": "linear; constant endpoint extrapolation; clip [0,1]"}


class TrainingMean:
    def __init__(self, mean):
        self.mean = float(mean)

    def predict(self, wind, temperature=None):
        return np.full(len(wind), self.mean)


class CatBoostPower:
    def __init__(self, model):
        self.model = model

    def predict(self, wind, temperature):
        features = np.column_stack([wind, temperature])
        return np.clip(self.model.predict(features), 0, 1)


def canonical_hourly(hourly):
    required = {"time_naive", "turbine_id", "wind_speed_ms", "power_normalized",
                "temperature_c", "observations", "is_complete", "split"}
    missing = required.difference(hourly.columns)
    if missing:
        raise ValueError(f"Missing hourly columns: {sorted(missing)}")
    hourly = hourly.copy()
    hourly["time_naive"] = pd.to_datetime(hourly["time_naive"], errors="raise", format="mixed")
    if hourly.time_naive.dt.tz is not None:
        raise ValueError("Hourly time_naive must have no timezone")
    if hourly.time_naive.isna().any() or (hourly.time_naive.dt.minute != 0).any() or (hourly.time_naive.dt.second != 0).any():
        raise ValueError("Hourly timestamps must be finite and aligned to whole hours")
    hourly["turbine_id"] = hourly.turbine_id.astype(str)
    values = hourly.is_complete.astype(str).str.lower()
    if not values.isin(["true", "false", "1", "0"]).all():
        raise ValueError("is_complete must contain booleans")
    hourly["is_complete"] = values.isin(["true", "1"])
    if hourly.duplicated(["turbine_id", "time_naive"]).any():
        raise ValueError("Duplicate hourly turbine/time labels")
    for column in ("wind_speed_ms", "power_normalized", "temperature_c"):
        hourly[column] = pd.to_numeric(hourly[column], errors="raise")
    return hourly


def fit_models(hourly, include_catboost=True):
    hourly = canonical_hourly(hourly)
    models, summaries = {}, {}
    for turbine, rows in hourly.groupby("turbine_id", sort=True):
        candidates = rows.loc[rows.time_naive < TRAINING_CUTOFF]
        train = candidates.loc[candidates.is_complete].copy()
        feature_columns = ["wind_speed_ms", "temperature_c", "power_normalized"]
        valid = np.isfinite(train[feature_columns]).all(axis=1)
        valid &= (train.wind_speed_ms >= 0) & train.power_normalized.between(0, 1)
        excluded_invalid = int((~valid).sum())
        train = train.loc[valid].sort_values("time_naive")
        if train.empty:
            raise ValueError(f"No complete valid training rows for turbine {turbine}")
        curve = EmpiricalPowerCurve().fit(train.wind_speed_ms, train.power_normalized)
        models[turbine] = {"empirical_curve": curve,
                           "training_mean": TrainingMean(train.power_normalized.mean())}
        if include_catboost:
            from catboost import CatBoostRegressor
            model = CatBoostRegressor(iterations=300, depth=4, learning_rate=0.05,
                                      loss_function="RMSE", random_seed=42, thread_count=2,
                                      verbose=False, allow_writing_files=False)
            model.fit(train[["wind_speed_ms", "temperature_c"]].to_numpy(), train.power_normalized.to_numpy())
            models[turbine]["catboost"] = CatBoostPower(model)
        summaries[turbine] = {
            "training_rows": len(train), "candidate_rows_before_cutoff": len(candidates),
            "incomplete_rows_excluded": int((~candidates.is_complete).sum()),
            "invalid_complete_rows_excluded": excluded_invalid,
            "first_training_time_naive": train.time_naive.min().isoformat(),
            "last_training_time_naive": train.time_naive.max().isoformat(),
            "cutoff_exclusive_naive": TRAINING_CUTOFF.isoformat(),
            "wind_min_ms": float(train.wind_speed_ms.min()), "wind_max_ms": float(train.wind_speed_ms.max()),
            "temperature_min_c": float(train.temperature_c.min()), "temperature_max_c": float(train.temperature_c.max()),
            "power_min": float(train.power_normalized.min()), "power_max": float(train.power_normalized.max()),
            "training_mean_power": float(train.power_normalized.mean()),
        }
    return models, summaries


def validate_weather(weather):
    missing = set(WEATHER_COLUMNS).difference(weather.columns)
    if missing:
        raise ValueError(f"Missing weather columns: {sorted(missing)}")
    weather = weather.copy()
    if weather.empty:
        raise ValueError("Weather input is empty")
    weather["turbine_id"] = weather.turbine_id.astype(str)
    for column in ("forecast_origin_utc", "run_time_utc", "valid_time_utc", "available_at_utc"):
        weather[column] = pd.to_datetime(weather[column], utc=True, errors="raise", format="mixed")
        if weather[column].isna().any():
            raise ValueError(f"Missing weather timestamp: {column}")
    for column in ("lead_hours", "wind_speed_100m_ms", "temperature_2m_c", "wind_direction_100m_deg"):
        weather[column] = pd.to_numeric(weather[column], errors="raise")
        if not np.isfinite(weather[column]).all():
            raise ValueError(f"Weather contains missing/nonfinite {column}")
    actual_lead = (weather.valid_time_utc - weather.forecast_origin_utc).dt.total_seconds() / 3600
    if not np.allclose(actual_lead, weather.lead_hours, atol=1e-9, rtol=0):
        raise ValueError("lead_hours differs from valid_time_utc - forecast_origin_utc")
    if not (weather.lead_hours.between(1, 48) & (weather.lead_hours % 1 == 0)).all():
        raise ValueError("Forecast leads must be integers 1 through 48")
    if (weather.available_at_utc > weather.forecast_origin_utc).any():
        raise ValueError("Forecast weather was unavailable at forecast issue time")
    if (weather.run_time_utc > weather.forecast_origin_utc).any() or (weather.run_time_utc > weather.valid_time_utc).any():
        raise ValueError("Forecast run must precede issue and valid times")
    if (weather.wind_speed_100m_ms < 0).any():
        raise ValueError("Negative forecast wind speed")
    if weather.duplicated(["turbine_id", "forecast_origin_utc", "valid_time_utc"]).any():
        raise ValueError("Duplicate turbine/origin/valid weather cases")
    weather["lead_hours"] = weather.lead_hours.astype(int)
    return weather.sort_values(["forecast_origin_utc", "turbine_id", "lead_hours"]).reset_index(drop=True)


def metric_table(predictions):
    records = []
    for (offset, turbine, model), group in predictions.groupby(["utc_offset_hours", "turbine_id", "model"], sort=True):
        if "is_validation_window" in group:
            group = group.loc[group.is_validation_window]
        for label, lo, hi in (("1-24", 1, 24), ("25-48", 25, 48), ("1-48", 1, 48)):
            horizon = group.loc[group.lead_hours.between(lo, hi)]
            usable = horizon.loc[horizon.score_eligible]
            errors = usable.predicted_power_normalized - usable.actual_power_normalized
            records.append({"utc_offset_hours": int(offset), "turbine_id": turbine,
                            "model": model, "horizon": label, "n": len(errors),
                            "forecast_cases": len(horizon),
                            "missing_target_cases": int(horizon.actual_power_normalized.isna().sum()),
                            "mae": float(errors.abs().mean()) if len(errors) else None,
                            "rmse": float(np.sqrt(np.mean(errors ** 2))) if len(errors) else None,
                            "bias": float(errors.mean()) if len(errors) else None})
    return pd.DataFrame(records)


def evaluate(hourly, weather, utc_offsets=(5,), include_catboost=True,
             raw_interval_label="interval_start"):
    if raw_interval_label not in {"interval_start", "interval_end"}:
        raise ValueError("raw_interval_label must be interval_start or interval_end")
    if not utc_offsets or any(offset not in (0, 5, 6) for offset in utc_offsets):
        raise ValueError("Use UTC+5 for the confirmed source timezone; UTC+0 and UTC+6 are supported sensitivity checks")
    hourly = canonical_hourly(hourly)
    weather = validate_weather(weather)
    models, training = fit_models(hourly, include_catboost=include_catboost)
    unknown = set(weather.turbine_id).difference(models)
    if unknown:
        raise ValueError(f"Weather turbines lack training models: {sorted(unknown)}")
    if weather.forecast_origin_utc.min() < FIRST_ORIGIN:
        raise ValueError("Forecast issue predates the safe frozen training protocol")
    # Complete labels only; observed weather is never joined into feature inputs.
    targets = hourly[["turbine_id", "time_naive", "power_normalized", "is_complete"]].copy()
    valid_target = targets.is_complete & np.isfinite(targets.power_normalized) & targets.power_normalized.between(0, 1)
    targets.loc[~valid_target, "power_normalized"] = np.nan
    targets = targets.rename(columns={"power_normalized": "actual_power_normalized", "is_complete": "target_hour_complete"})
    frames = []
    for offset in utc_offsets:
        cases = weather.copy()
        cases["utc_offset_hours"] = int(offset)
        cases["timezone_hypothesis"] = f"UTC+{offset}"
        cases["timezone_is_confirmed"] = offset == SOURCE_UTC_OFFSET_HOURS
        cases["valid_time_naive"] = cases.valid_time_utc.dt.tz_localize(None) + pd.Timedelta(hours=offset)
        cases = cases.merge(targets, left_on=["turbine_id", "valid_time_naive"],
                            right_on=["turbine_id", "time_naive"], how="left", validate="many_to_one").drop(columns="time_naive")
        cases["target_hour_complete"] = cases.target_hour_complete.eq(True)
        cases["is_validation_window"] = (
            cases.valid_time_naive.ge(VALIDATION_START) & cases.valid_time_naive.lt(VALIDATION_END)
            & cases.forecast_origin_utc.ge(FIRST_ORIGIN) & cases.forecast_origin_utc.lt(DEMO_ORIGIN))
        cases["is_demo"] = cases.forecast_origin_utc.eq(DEMO_ORIGIN)
        for turbine, group in cases.groupby("turbine_id", sort=True):
            wind, temp = group.wind_speed_100m_ms.to_numpy(), group.temperature_2m_c.to_numpy()
            curve = models[turbine]["empirical_curve"]
            group = group.copy()
            group["wind_outside_training_range"] = curve.outside_training_range(wind)
            group["wind_rare_extreme"] = (wind < curve.rare_low) | (wind > curve.rare_high)
            group["wind_bin_sparse"] = curve.sparse_bin(wind)
            group["temperature_outside_training_range"] = (temp < training[turbine]["temperature_min_c"]) | (temp > training[turbine]["temperature_max_c"])
            for name, model in models[turbine].items():
                predicted = model.predict(wind, temp)
                if not np.isfinite(predicted).all():
                    raise ValueError(f"Nonfinite predictions from {turbine}/{name}")
                result = group.copy()
                result["model"] = name
                result["predicted_power_normalized"] = predicted
                result["score_eligible"] = result.is_validation_window & result.actual_power_normalized.notna()
                frames.append(result)
    predictions = pd.concat(frames, ignore_index=True).sort_values(
        ["utc_offset_hours", "forecast_origin_utc", "model", "turbine_id", "lead_hours"])
    metrics = metric_table(predictions)
    ranking = []
    comparable = metrics.loc[(metrics.horizon == "1-48") & (metrics.n > 0)]
    for (offset, turbine), group in comparable.groupby(["utc_offset_hours", "turbine_id"]):
        row = group.sort_values(["rmse", "mae", "model"]).iloc[0]
        ranking.append({"utc_offset_hours": int(offset), "turbine_id": turbine,
                        "lowest_january_rmse_model": row.model, "rmse": float(row.rmse), "n": int(row.n)})
    expected = pd.MultiIndex.from_product(
        [pd.date_range(FIRST_ORIGIN, DEMO_ORIGIN, freq="D"), range(1, 49)],
        names=["forecast_origin_utc", "lead_hours"])
    coverage = {"expected_cases_per_turbine": len(expected), "turbines": {}, "complete": True}
    for turbine in models:
        turbine_weather = weather.loc[weather.turbine_id == turbine]
        received = pd.MultiIndex.from_frame(turbine_weather[["forecast_origin_utc", "lead_hours"]])
        missing = expected.difference(received)
        unexpected = received.difference(expected)
        coverage["turbines"][turbine] = {
            "received_cases": len(received), "missing_cases": len(missing),
            "unexpected_cases": len(unexpected),
            "missing_examples": [{"forecast_origin_utc": origin.isoformat(), "lead_hours": int(lead)}
                                 for origin, lead in missing[:5]],
        }
        coverage["complete"] &= len(missing) == 0 and len(unexpected) == 0
    metadata = {
        "timezone_confirmed": True, "source_utc_offset_hours": SOURCE_UTC_OFFSET_HOURS,
        "timezone_confirmation_source": "user", "utc_offsets_evaluated": list(utc_offsets),
        "sensitivity_utc_offsets": [offset for offset in utc_offsets if offset != SOURCE_UTC_OFFSET_HOURS],
        "timezone_policy": "The user confirmed CSV timezone UTC+5. Other explicitly requested offsets are sensitivity checks, not confirmed source timezones.",
        "raw_interval_label_assumption": raw_interval_label,
        "hourly_label_semantics": "Input hourly time_naive denotes interval start. If original 10-minute rows label interval ends, subtract 10 minutes before hourly aggregation; evaluator never shifts already aggregated hours.",
        "training_cutoff_exclusive_naive": TRAINING_CUTOFF.isoformat(),
        "training_freeze_reason": "One day early to avoid using December 31 labels unavailable at the first issue under any timezone hypothesis.",
        "forecast_coverage": coverage,
        "validation_local_start_inclusive": VALIDATION_START.isoformat(),
        "validation_local_end_exclusive": VALIDATION_END.isoformat(),
        "first_validation_origin_utc": FIRST_ORIGIN.isoformat(),
        "last_validation_origin_utc": (DEMO_ORIGIN - pd.Timedelta(days=1)).isoformat(),
        "demo_origin_utc": DEMO_ORIGIN.isoformat(), "demo_horizons": "1-48 hours after origin; confirmed UTC+5 covers February 1 00:00 through February 2 23:00 local time",
        "primary_metric": "RMSE on normalized power; 1-48h combined, with UTC+5 as the primary evaluation",
        "bias_definition": "mean(prediction - actual)",
        "model_features": {"empirical_curve": ["wind_speed_ms"], "catboost": ["wind_speed_ms", "temperature_c"],
                           "forecast_mapping": {"wind_speed_ms": "wind_speed_100m_ms", "temperature_c": "temperature_2m_c"},
                           "output_bounds": [0, 1]},
        "comparison_protocol": "All models use identical complete January target cases per turbine, timezone and horizon. Overlapping forecast origins are distinct cases; no random split or February tuning.",
        "hyperparameters": {"empirical_curve": {"bin_width_ms": 0.5, "bin_statistic": "mean"},
                            "catboost": {"enabled": include_catboost, "iterations": 300, "depth": 4, "learning_rate": 0.05, "loss_function": "RMSE", "random_seed": 42, "thread_count": 2}},
        "selection_note": "January ranks are descriptive holdout results, not unbiased post-selection performance. UTC+5 was confirmed by the user, not selected by scores; no February actuals are used.",
        "january_model_comparisons": ranking,
        "training": training,
        "limitations": [
            "Original 10-minute start/end labeling still requires confirmation; the source timezone is confirmed UTC+5.",
            "NOAA 100m model wind and turbine observed wind have different height/exposure/error distributions; turbine hub height is unknown.",
            "Empirical curves learn observed operating behavior, including curtailment/outages, rather than manufacturer physical power curves.",
            "Endpoint extrapolation does not model an unobserved high-wind cut-out; inspect rare/extreme and sparse-bin flags.",
            "Weather availability must be supplied by the archive provenance pipeline; timestamp checks alone do not establish publication history.",
        ],
    }
    return predictions, metrics, metadata, models


def write_outputs(predictions, metrics, metadata, models, output_dir, make_plots=True):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(output_dir / "predictions.csv", index=False)
    metrics.to_csv(output_dir / "metrics.csv", index=False)
    # pandas converts NaN to JSON null before the strict JSON serializer.
    payload = {"metadata": metadata, "metrics": json.loads(metrics.to_json(orient="records"))}
    (output_dir / "metrics.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    (output_dir / "training_summary.json").write_text(json.dumps(metadata["training"], indent=2, allow_nan=False) + "\n")
    curves = {turbine: model_set["empirical_curve"].to_dict() for turbine, model_set in models.items()}
    (output_dir / "empirical_curves.json").write_text(json.dumps(curves, indent=2, allow_nan=False) + "\n")
    for turbine, model_set in models.items():
        if "catboost" in model_set:
            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", turbine)
            model_set["catboost"].model.save_model(str(output_dir / f"catboost_{safe_name}.cbm"))
    demo = predictions.loc[predictions.is_demo]
    demo.to_csv(output_dir / "forecast_demo.csv", index=False)
    keys = ["forecast_origin_utc", "valid_time_utc", "lead_hours", "utc_offset_hours", "timezone_hypothesis", "valid_time_naive", "model"]
    wide = demo.pivot(index=keys, columns="turbine_id", values="predicted_power_normalized").reset_index()
    wide.to_csv(output_dir / "forecast_demo_wide.csv", index=False)
    utc_keys = ["forecast_origin_utc", "valid_time_utc", "lead_hours"]
    # Timezone hypotheses change target alignment, not the numerical forecasts.
    canonical_demo = demo.drop_duplicates(utc_keys + ["turbine_id", "model"])
    compact = canonical_demo.pivot(index=utc_keys, columns=["turbine_id", "model"],
                                   values="predicted_power_normalized")
    compact.columns = [f"power_{turbine}_{model}" for turbine, model in compact.columns]
    compact = compact.reset_index().sort_values("lead_hours")
    compact.to_csv(output_dir / "forecast_48h_utc.csv", index=False)
    local = compact.rename(columns={"forecast_origin_utc": "forecast_origin_local", "valid_time_utc": "valid_time_local"}).copy()
    for column in ("forecast_origin_local", "valid_time_local"):
        local[column] = pd.to_datetime(local[column], utc=True).dt.tz_convert(SOURCE_TIMEZONE)
    local.to_csv(output_dir / "forecast_48h_local.csv", index=False)
    if make_plots:
        if not demo.empty:
            write_demo_plot(demo, output_dir)
        if predictions.is_validation_window.any():
            write_plots(predictions, metrics, output_dir)


def write_demo_plot(demo, output_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    # Numerical forecasts are identical across sensitivity offsets; keep the axis UTC.
    demo = demo.loc[demo.utc_offset_hours == demo.utc_offset_hours.min()]
    turbines = sorted(demo.turbine_id.unique())
    colors = {"empirical_curve": "#236A95", "training_mean": "#929A9D", "catboost": "#C46A30"}
    figure, axes = plt.subplots(len(turbines), 1, figsize=(12, 3.4 * len(turbines)),
                               squeeze=False, sharex=True, constrained_layout=True)
    for turbine, ax in zip(turbines, axes[:, 0]):
        for model, rows in demo.loc[demo.turbine_id == turbine].groupby("model"):
            rows = rows.sort_values("lead_hours")
            ax.plot(rows.valid_time_utc, rows.predicted_power_normalized,
                    color=colors.get(model), label=model.replace("_", " "),
                    linewidth=1.8, linestyle="--" if model == "training_mean" else "-")
        ax.set_title(f"Turbine {turbine}", loc="left")
        ax.set_ylabel("Normalized power")
        ax.set_ylim(0, 1)
        ax.grid(alpha=.2)
        ax.legend(loc="upper left", fontsize=9, ncols=3)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=7))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H:%M UTC"))
    figure.suptitle("48-hour forecast · issued 2026-01-31 at 18:00 UTC\nUTC timestamps; source timezone UTC+5 confirmed by user", fontsize=14)
    figure.savefig(Path(output_dir) / "forecast_demo_48h.png", dpi=150)
    plt.close(figure)


def write_plots(predictions, metrics, output_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    offsets = sorted(predictions.utc_offset_hours.unique())
    turbines = sorted(predictions.turbine_id.unique())
    colors = {"empirical_curve": "#236A95", "training_mean": "#929A9D", "catboost": "#C46A30"}
    figure, axes = plt.subplots(len(offsets), len(turbines), figsize=(6 * len(turbines), 3.3 * len(offsets)), squeeze=False, constrained_layout=True)
    for i, offset in enumerate(offsets):
        for j, turbine in enumerate(turbines):
            ax = axes[i, j]
            group = metrics.loc[(metrics.utc_offset_hours == offset) & (metrics.turbine_id == turbine) & (metrics.horizon != "1-48")]
            names = sorted(group.model.unique())
            for k, model in enumerate(names):
                rows = group.loc[group.model == model].set_index("horizon")
                vals = [rows.loc[horizon, "rmse"] if pd.notna(rows.loc[horizon, "rmse"]) else np.nan
                        for horizon in ("1-24", "25-48")]
                ax.bar(np.arange(2) + (k - (len(names) - 1) / 2) * .23, vals, width=.23, label=model, color=colors.get(model))
            ax.set_xticks([0, 1], ["Hours 1–24", "Hours 25–48"])
            ax.set_title(f"{turbine} · UTC+{offset} {'confirmed' if offset == SOURCE_UTC_OFFSET_HOURS else 'sensitivity'}")
            ax.set_ylabel("Normalized power RMSE")
            ax.set_ylim(bottom=0)
            ax.grid(axis="y", alpha=.2)
            if i == 0 and j == 0:
                ax.legend(fontsize=8)
    figure.suptitle("January 2026 holdout · source timezone UTC+5 confirmed by user", fontsize=15)
    figure.savefig(output_dir / "metrics.png", dpi=150)
    plt.close(figure)
    representative = pd.Timestamp("2026-01-15 18:00:00", tz="UTC")
    representative_cases = predictions.loc[predictions.forecast_origin_utc.eq(representative)]
    if representative_cases.empty:
        return
    figure, axes = plt.subplots(len(offsets), len(turbines), figsize=(6 * len(turbines), 3.3 * len(offsets)), squeeze=False, constrained_layout=True)
    for i, offset in enumerate(offsets):
        for j, turbine in enumerate(turbines):
            ax = axes[i, j]
            group = representative_cases.loc[(representative_cases.utc_offset_hours == offset) & (representative_cases.turbine_id == turbine)]
            for name, rows in group.groupby("model"):
                rows = rows.sort_values("lead_hours")
                ax.plot(rows.lead_hours, rows.predicted_power_normalized, label=name, color=colors.get(name), linewidth=1.4)
            actual = group.drop_duplicates("lead_hours").sort_values("lead_hours")
            ax.plot(actual.lead_hours, actual.actual_power_normalized, label="actual", color="#182329", linewidth=1.8)
            ax.set_title(f"{turbine} · UTC+{offset} {'confirmed' if offset == SOURCE_UTC_OFFSET_HOURS else 'sensitivity'}")
            ax.set_xlabel("Hours after issue (UTC)")
            ax.set_ylabel("Normalized power")
            ax.set_ylim(-.03, 1.03)
            ax.grid(alpha=.2)
            if i == 0 and j == 0:
                ax.legend(fontsize=8)
    figure.suptitle("Issued 2026-01-15 18:00 UTC · 48-hour archived forecast", fontsize=15)
    figure.savefig(output_dir / "actual_vs_forecast_jan15.png", dpi=150)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hourly", type=Path, default=Path("data/processed/hourly.csv"))
    parser.add_argument("--weather", type=Path, default=Path("data/weather/noaa_forecasts.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/baseline"))
    parser.add_argument("--utc-offsets", type=int, nargs="+", default=[SOURCE_UTC_OFFSET_HOURS],
                        help="Default: user-confirmed UTC+5. Explicit UTC+0/+6 values enable sensitivity checks")
    parser.add_argument("--no-catboost", action="store_true")
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--raw-interval-label", choices=["interval_start", "interval_end"], default="interval_start",
                        help="Provenance of upstream aggregation; interval_end requires upstream -10 minute adjustment, never shifts hourly data here")
    args = parser.parse_args()
    result = evaluate(pd.read_csv(args.hourly), pd.read_csv(args.weather),
                      utc_offsets=tuple(args.utc_offsets), include_catboost=not args.no_catboost,
                      raw_interval_label=args.raw_interval_label)
    write_outputs(*result, output_dir=args.output_dir, make_plots=not args.no_plots)
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "prediction_rows": len(result[0]),
                      "metric_rows": len(result[1]), "timezone_confirmed": True,
                      "source_utc_offset_hours": SOURCE_UTC_OFFSET_HOURS,
                      "timezone_confirmation_source": "user"}, indent=2))


if __name__ == "__main__":
    main()
