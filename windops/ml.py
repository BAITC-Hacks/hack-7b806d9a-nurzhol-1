"""Frozen, auditable GFS-to-power models and the locked December selection gate."""
from __future__ import annotations

from datetime import timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from scripts.evaluate_baseline import EmpiricalPowerCurve, canonical_hourly, validate_weather


LOCAL = timezone(timedelta(hours=5))
SCHEMA_VERSION = 1
MODEL_VERSION = "gfs-ml-v1"
CANDIDATES = ("raw_empirical", "affine_wind", "catboost_gfs")
LABELS = {"raw_empirical": "Эмпирическая кривая без коррекции GFS",
          "affine_wind": "Коррекция ветра GFS + эмпирическая кривая",
          "catboost_gfs": "CatBoost: прогноз GFS → мощность"}
FEATURES = ("wind_speed_100m_ms", "temperature_2m_c", "wind_direction_sin",
            "wind_direction_cos", "lead_hours", "local_hour_sin", "local_hour_cos")
CATBOOST_PARAMETERS = dict(iterations=400, depth=4, learning_rate=0.03, l2_leaf_reg=10,
                          loss_function="RMSE", random_seed=42, thread_count=2,
                          verbose=False, allow_writing_files=False)
CASE_KEYS = ["turbine_id", "forecast_origin_utc", "valid_time_utc", "lead_hours"]


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode()


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name,
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def local_timestamp(value):
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize(LOCAL) if timestamp.tzinfo is None else timestamp.tz_convert(LOCAL)


def utc_timestamp(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError("The label availability bound must have an explicit timezone")
    return timestamp.tz_convert("UTC")


def feature_frame(weather):
    """Only as-issued forecast fields enter features; never station observations."""
    frame = pd.DataFrame(weather)
    required = {"wind_speed_100m_ms", "temperature_2m_c", "wind_direction_100m_deg",
                "lead_hours", "valid_time_utc"}
    if not required.issubset(frame):
        raise ValueError(f"Missing forecast features: {sorted(required.difference(frame))}")
    numeric = frame[["wind_speed_100m_ms", "temperature_2m_c", "wind_direction_100m_deg", "lead_hours"]].apply(pd.to_numeric, errors="raise")
    if (not np.isfinite(numeric.to_numpy()).all() or (numeric.wind_speed_100m_ms < 0).any()
            or not numeric.lead_hours.between(1, 48).all() or (numeric.lead_hours % 1 != 0).any()):
        raise ValueError("Forecast features must be finite; speed nonnegative and horizon 1..48")
    valid = pd.to_datetime(frame.valid_time_utc, utc=True, format="mixed", errors="raise")
    if valid.isna().any():
        raise ValueError("Missing forecast valid time")
    radians = np.deg2rad(numeric.wind_direction_100m_deg.to_numpy())
    hours = valid.dt.tz_convert(LOCAL).dt.hour.to_numpy()
    angle = hours * (2 * np.pi / 24)
    return pd.DataFrame({
        "wind_speed_100m_ms": numeric.wind_speed_100m_ms.to_numpy(),
        "temperature_2m_c": numeric.temperature_2m_c.to_numpy(),
        "wind_direction_sin": np.sin(radians), "wind_direction_cos": np.cos(radians),
        "lead_hours": numeric.lead_hours.to_numpy(), "local_hour_sin": np.sin(angle),
        "local_hour_cos": np.cos(angle),
    }, index=frame.index, columns=list(FEATURES))


def pair_weather(hourly, weather):
    """Keep all forecast cases; label quality determines a common evaluation mask."""
    station = canonical_hourly(hourly)
    forecast = validate_weather(weather)
    if set(forecast.turbine_id).difference({"1", "2"}):
        raise ValueError("Only turbines 1 and 2 are supported")
    forecast["valid_time_local"] = forecast.valid_time_utc.dt.tz_convert(LOCAL)
    forecast["time_naive"] = forecast.valid_time_local.dt.tz_localize(None)
    station = station[["turbine_id", "time_naive", "wind_speed_ms", "power_normalized",
                       "temperature_c", "is_complete"]].rename(columns={
        "wind_speed_ms": "actual_wind_speed_ms", "power_normalized": "actual_power_normalized",
        "temperature_c": "actual_temperature_c", "is_complete": "target_hour_complete"})
    paired = forecast.merge(station, on=["turbine_id", "time_naive"], how="left", validate="many_to_one")
    paired["label_end_utc"] = paired.valid_time_utc + pd.Timedelta(hours=1)
    measured = paired[["actual_wind_speed_ms", "actual_power_normalized", "actual_temperature_c"]]
    paired["score_eligible"] = (paired.target_hour_complete.eq(True)
                                & np.isfinite(measured).all(axis=1)
                                & paired.actual_power_normalized.between(0, 1)
                                & paired.actual_wind_speed_ms.ge(0))
    return paired.drop(columns="time_naive")


def select_targets(pairs, start, end, eligible_only=True):
    mask = (pairs.valid_time_local.ge(local_timestamp(start))
            & pairs.valid_time_local.lt(local_timestamp(end)))
    if eligible_only:
        mask &= pairs.score_eligible
    return pairs.loc[mask].copy().reset_index(drop=True)


def _curve_from_dict(data):
    centers = np.asarray(data["bin_centers_ms"], dtype=float)
    means = np.asarray(data["bin_mean_power_normalized"], dtype=float)
    if (centers.ndim != 1 or means.ndim != 1 or len(centers) == 0 or len(centers) != len(means)
            or not np.isfinite(centers).all() or not np.isfinite(means).all()
            or not (np.diff(centers) > 0).all() or (centers < 0).any()
            or ((means < 0) | (means > 1)).any()):
        raise ValueError("Invalid saved empirical curve")
    curve = EmpiricalPowerCurve(data["bin_width_ms"])
    curve.centers, curve.means = centers, means
    return curve


class ForecastModelBundle:
    """Portable model bundle: a manifest and hashed files in the same directory."""
    def __init__(self, candidate, metadata, curves=None, calibration=None, models=None):
        if candidate not in CANDIDATES:
            raise ValueError("Unknown GFS model candidate")
        self.candidate = candidate
        self.metadata = dict(metadata)
        self.curve_data = curves
        self.curves = {int(t): _curve_from_dict(value) for t, value in (curves or {}).items()}
        self.calibration = calibration
        self.models = models

    def predict(self, weather_dataframe):
        source = pd.DataFrame(weather_dataframe).copy()
        source["_gfs_ml_position"] = np.arange(len(source))
        weather = validate_weather(source)
        if set(weather.turbine_id).difference({"1", "2"}):
            raise ValueError("Unknown turbine in forecast inputs")
        features = feature_frame(weather)
        values = np.empty(len(weather), dtype=float)
        for turbine in (1, 2):
            mask = weather.turbine_id.eq(str(turbine)).to_numpy()
            if not mask.any():
                continue
            if self.candidate == "catboost_gfs":
                predicted = self.models[turbine].predict(features.iloc[np.flatnonzero(mask)])
            else:
                wind = features.wind_speed_100m_ms.to_numpy()[mask]
                if self.candidate == "affine_wind":
                    params = self.calibration[str(turbine)]
                    wind = np.maximum(0, params["slope"] * wind + params["intercept"])
                predicted = self.curves[turbine].predict(wind)
            if not np.isfinite(predicted).all():
                raise ValueError("Model produced nonfinite predictions; cases must not be dropped")
            values[weather.loc[mask, "_gfs_ml_position"].to_numpy(dtype=int)] = np.clip(predicted, 0, 1)
        return values

    def save(self, directory):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        if self.candidate in ("raw_empirical", "affine_wind"):
            atomic_write(directory / "curves.json", json_bytes(self.curve_data))
            artifacts["curves"] = "curves.json"
        if self.candidate == "affine_wind":
            atomic_write(directory / "calibration.json", json_bytes(self.calibration))
            artifacts["calibration"] = "calibration.json"
        if self.candidate == "catboost_gfs":
            for turbine in (1, 2):
                name = f"turbine_{turbine}.cbm"
                with tempfile.NamedTemporaryFile(dir=directory, suffix=".cbm", delete=False) as stream:
                    temporary = Path(stream.name)
                try:
                    self.models[turbine].save_model(str(temporary))
                    temporary.replace(directory / name)
                finally:
                    temporary.unlink(missing_ok=True)
                artifacts[f"turbine_{turbine}"] = name
        metadata = {key: value for key, value in self.metadata.items() if key not in ("version", "artifact_sha256")}
        manifest = {"schema_version": SCHEMA_VERSION, "implementation_version": MODEL_VERSION,
                    "candidate": self.candidate, "feature_names": list(FEATURES), "metadata": metadata,
                    "artifacts": {role: {"file": name, "sha256": sha256((directory / name).read_bytes())}
                                  for role, name in artifacts.items()}}
        payload = json_bytes(manifest)
        path = directory / "manifest.json"
        atomic_write(path, payload)
        self.metadata.update(version=MODEL_VERSION + ":" + sha256(payload), artifact_sha256=sha256(payload))
        return path

    @classmethod
    def load(cls, manifest_path):
        path = Path(manifest_path).resolve()
        payload = path.read_bytes()
        manifest = json.loads(payload)
        candidate = manifest.get("candidate")
        if (manifest.get("schema_version") != SCHEMA_VERSION
                or manifest.get("implementation_version") != MODEL_VERSION
                or candidate not in CANDIDATES or manifest.get("feature_names") != list(FEATURES)):
            raise ValueError("Unsupported model manifest/schema/features")
        roles = {"raw_empirical": {"curves"}, "affine_wind": {"curves", "calibration"},
                 "catboost_gfs": {"turbine_1", "turbine_2"}}[candidate]
        if set(manifest.get("artifacts", {})) != roles:
            raise ValueError("Model manifest has missing or unexpected artifacts")
        files = {}
        for role, artifact in manifest["artifacts"].items():
            name = artifact.get("file")
            if (not isinstance(name, str) or not name or name in (".", "..")
                    or Path(name).name != name or "\\" in name or Path(name).is_absolute()):
                raise ValueError("Artifact path must be a filename in the model directory")
            target = (path.parent / name).resolve()
            if target.parent != path.parent:
                raise ValueError("Artifact path escapes the model directory")
            content = target.read_bytes()
            if sha256(content) != artifact.get("sha256"):
                raise ValueError(f"Artifact SHA256 hash mismatch: {name}")
            files[role] = (target, content)
        metadata = manifest.get("metadata", {})
        if (metadata.get("id") != candidate or not metadata.get("label")
                or not metadata.get("training_cutoff_exclusive") or not metadata.get("training_cutoff")):
            raise ValueError("Model manifest lacks training provenance")
        local_timestamp(metadata["training_cutoff"])
        metadata = dict(metadata, version=MODEL_VERSION + ":" + sha256(payload), artifact_sha256=sha256(payload))
        curves = json.loads(files["curves"][1]) if "curves" in files else None
        if curves is not None and set(curves) != {"1", "2"}:
            raise ValueError("Saved curves must contain both turbines")
        calibration = json.loads(files["calibration"][1]) if "calibration" in files else None
        if calibration is not None:
            if set(calibration) != {"1", "2"}:
                raise ValueError("Saved calibration must contain both turbines")
            for params in calibration.values():
                if not np.isfinite([params["slope"], params["intercept"]]).all():
                    raise ValueError("Nonfinite affine calibration")
        models = None
        if candidate == "catboost_gfs":
            from catboost import CatBoostRegressor
            models = {}
            for turbine in (1, 2):
                model = CatBoostRegressor()
                model.load_model(str(files[f"turbine_{turbine}"][0]))
                if model.feature_names_ != list(FEATURES):
                    raise ValueError("Saved CatBoost feature schema does not match manifest")
                models[turbine] = model
        return cls(candidate, metadata, curves, calibration, models)


def fit_candidates(hourly, training_pairs, target_cutoff_local, before_issue_utc, candidates=CANDIDATES):
    """Fit only supplied historical pairs; availability is an independent hard guard."""
    if not candidates or set(candidates).difference(CANDIDATES):
        raise ValueError("Only the three preregistered candidate algorithms are allowed")
    cutoff, issue = local_timestamp(target_cutoff_local), utc_timestamp(before_issue_utc)
    pairs = training_pairs.copy()
    if (pairs.empty or not pairs.score_eligible.all()
            or not pairs.valid_time_local.lt(cutoff).all()
            or not pairs.label_end_utc.lt(issue).all()):
        raise ValueError("Training labels must end strictly before the first evaluation issue and target cutoff")
    station = canonical_hourly(hourly)
    station["valid_time_local"] = station.time_naive.dt.tz_localize(LOCAL)
    station["label_end_utc"] = station.valid_time_local.dt.tz_convert("UTC") + pd.Timedelta(hours=1)
    usable = (station.is_complete & np.isfinite(station[["wind_speed_ms", "power_normalized", "temperature_c"]]).all(axis=1)
              & station.wind_speed_ms.ge(0) & station.power_normalized.between(0, 1)
              & station.valid_time_local.lt(cutoff))
    station = station.loc[usable].copy()
    if not station.label_end_utc.lt(issue).all():
        raise ValueError("Station training label end is unavailable at the first evaluation issue")
    curves, calibration, models, provenance = {}, {}, {}, {}
    for turbine in (1, 2):
        labels = station.loc[station.turbine_id.eq(str(turbine))]
        paired = pairs.loc[pairs.turbine_id.eq(str(turbine))]
        if labels.empty or paired.empty:
            raise ValueError(f"No historical training rows for turbine {turbine}")
        curve = EmpiricalPowerCurve().fit(labels.wind_speed_ms, labels.power_normalized)
        curves[str(turbine)] = curve.to_dict()
        x = paired.wind_speed_100m_ms.to_numpy(dtype=float)
        y = paired.actual_wind_speed_ms.to_numpy(dtype=float)
        centered = x - x.mean()
        denominator = float(np.dot(centered, centered))
        slope = float(np.dot(centered, y - y.mean()) / denominator) if denominator > 0 else 0.0
        calibration[str(turbine)] = {"slope": slope, "intercept": float(y.mean() - slope * x.mean()),
                                     "method": "ordinary least squares; corrected speed clipped at zero"}
        if "catboost_gfs" in candidates:
            from catboost import CatBoostRegressor
            model = CatBoostRegressor(**CATBOOST_PARAMETERS)
            model.fit(feature_frame(paired), paired.actual_power_normalized.to_numpy(dtype=float))
            models[turbine] = model
        provenance[str(turbine)] = {
            "station_curve_rows": len(labels), "forecast_training_cases": len(paired),
            "unique_target_hours": int(paired.valid_time_utc.nunique()),
            "first_target_local": paired.valid_time_local.min().isoformat(),
            "last_target_local": paired.valid_time_local.max().isoformat(),
            "last_pair_label_end_utc": paired.label_end_utc.max().isoformat(),
            "last_station_label_end_utc": labels.label_end_utc.max().isoformat(),
        }
    result = {}
    for candidate in candidates:
        metadata = {"id": candidate, "label": LABELS[candidate], "training_cutoff": cutoff.isoformat(),
                    "training_cutoff_exclusive": True, "training_cutoff_semantics": "target interval start",
                    "labels_end_strictly_before_utc": issue.isoformat(), "timezone": "UTC+5",
                    "training": provenance, "catboost_parameters": CATBOOST_PARAMETERS if candidate == "catboost_gfs" else None}
        result[candidate] = ForecastModelBundle(candidate, metadata, curves if candidate != "catboost_gfs" else None,
                                               calibration if candidate == "affine_wind" else None,
                                               models if candidate == "catboost_gfs" else None)
    return result


def predict_candidates(bundles, pairs):
    if pairs.empty or not pairs.score_eligible.all():
        raise ValueError("Prediction comparison requires the common complete-case mask")
    frames = []
    for candidate in CANDIDATES:
        frame = pairs.copy()
        frame["candidate"] = candidate
        frame["predicted_power_normalized"] = bundles[candidate].predict(pairs)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _error_metrics(rows):
    if rows.empty:
        raise ValueError("Cannot score an empty evaluation group")
    values = rows[["actual_power_normalized", "predicted_power_normalized"]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite scored values; do not silently remove forecast cases")
    error = values[:, 1] - values[:, 0]
    return {"n": len(rows), "rmse": float(np.sqrt(np.mean(error ** 2))),
            "mae": float(np.mean(np.abs(error))), "bias": float(np.mean(error))}


def metrics_table(predictions):
    records = []
    for (candidate, turbine), group in predictions.groupby(["candidate", "turbine_id"], sort=True):
        for name, low, high in (("1-24", 1, 24), ("25-48", 25, 48), ("1-48", 1, 48)):
            records.append({"candidate": candidate, "turbine_id": int(turbine), "horizon": name,
                            **_error_metrics(group.loc[group.lead_hours.between(low, high)])})
    return pd.DataFrame(records)


def promotion_decision(predictions):
    """Selection is exclusively December, on identical complete target cases."""
    if set(predictions.candidate) != set(CANDIDATES):
        raise ValueError("Exactly three preregistered candidates are required")
    frame = predictions.copy()
    frame["turbine_id"] = frame.turbine_id.astype(str)
    frame["valid_time_local"] = pd.to_datetime(frame.valid_time_local, utc=True, format="mixed").dt.tz_convert(LOCAL)
    if (not frame.valid_time_local.ge(local_timestamp("2025-12-01")).all()
            or not frame.valid_time_local.lt(local_timestamp("2026-01-01")).all()
            or set(frame.turbine_id) != {"1", "2"}):
        raise ValueError("Promotion may use only December cases for both turbines")
    groups = {}
    reference = None
    for candidate in CANDIDATES:
        group = frame.loc[frame.candidate.eq(candidate)].sort_values(CASE_KEYS).reset_index(drop=True)
        if group.duplicated(CASE_KEYS).any():
            raise ValueError("Duplicate evaluation case within a candidate")
        case_labels = group[CASE_KEYS + ["actual_power_normalized"]]
        if reference is not None and not reference.equals(case_labels):
            raise ValueError("Candidates must score identical forecast cases and labels")
        reference = case_labels
        _error_metrics(group)
        groups[candidate] = group
    baseline = groups["raw_empirical"]
    result = {"selection_period": "2025-12", "baseline": "raw_empirical", "selected_candidate": None,
              "gate_passed": False, "independent_test": False,
              "rule": "Each turbine RMSE improves >=5%; each turbine/horizon does not worsen; both pooled month halves improve",
              "candidates": {}}
    for candidate in CANDIDATES[1:]:
        group = groups[candidate]
        checks = []
        for turbine in ("1", "2"):
            base = baseline.loc[baseline.turbine_id.eq(turbine)]
            rows = group.loc[group.turbine_id.eq(turbine)]
            base_rmse, rmse = _error_metrics(base)["rmse"], _error_metrics(rows)["rmse"]
            checks.append({"check": "total_rmse_improvement", "turbine_id": int(turbine),
                           "baseline_rmse": base_rmse, "candidate_rmse": rmse,
                           "relative_improvement": 1 - rmse / base_rmse if base_rmse > 0 else None,
                           "passed": bool(base_rmse > 0 and rmse <= base_rmse * 0.95)})
            for horizon, low, high in (("1-24", 1, 24), ("25-48", 25, 48)):
                base_h = _error_metrics(base.loc[base.lead_hours.between(low, high)])["rmse"]
                score_h = _error_metrics(rows.loc[rows.lead_hours.between(low, high)])["rmse"]
                checks.append({"check": "horizon_nonworsening", "turbine_id": int(turbine),
                               "horizon": horizon, "baseline_rmse": base_h, "candidate_rmse": score_h,
                               "passed": bool(score_h <= base_h)})
        for name, low, high in (("December 1–15", 1, 15), ("December 16–31", 16, 31)):
            base_rmse = _error_metrics(baseline.loc[baseline.valid_time_local.dt.day.between(low, high)])["rmse"]
            rmse = _error_metrics(group.loc[group.valid_time_local.dt.day.between(low, high)])["rmse"]
            checks.append({"check": "half_month_improvement", "half": name, "baseline_rmse": base_rmse,
                           "candidate_rmse": rmse, "passed": bool(rmse < base_rmse)})
        result["candidates"][candidate] = {"passed": all(check["passed"] for check in checks),
                                           "pooled_metrics": _error_metrics(group), "checks": checks}
    passing = [name for name, value in result["candidates"].items() if value["passed"]]
    if passing:
        result["selected_candidate"] = min(passing, key=lambda name: (result["candidates"][name]["pooled_metrics"]["rmse"], name))
        result["gate_passed"] = True
    result["baseline_pooled_metrics"] = _error_metrics(baseline)
    return result


def require_archive(weather, first_date, last_date):
    """Reject partial downloads before fitting; every expected run needs both turbines."""
    frame = validate_weather(weather)
    origins = pd.date_range(first_date + "T18:00:00Z", last_date + "T18:00:00Z", freq="D")
    expected = pd.MultiIndex.from_product([("1", "2"), origins, range(1, 49)],
                                         names=["turbine_id", "forecast_origin_utc", "lead_hours"])
    received = pd.MultiIndex.from_frame(frame[["turbine_id", "forecast_origin_utc", "lead_hours"]])
    if len(expected.difference(received)) or len(received.difference(expected)):
        raise ValueError(f"Incomplete or unexpected archive coverage for {first_date}..{last_date}; "
                         f"expected {len(expected)} weather rows, received {len(frame)}")
    if not frame.run_time_utc.eq(frame.forecast_origin_utc - pd.Timedelta(hours=6)).all():
        raise ValueError("Archive must contain the issue date's 12 UTC GFS run")
    if "gfs_forecast_hour" in frame:
        if not pd.to_numeric(frame.gfs_forecast_hour, errors="raise").eq(frame.lead_hours + 6).all():
            raise ValueError("Archive GFS hours do not match f007..f054")
    return frame


def _write_stage(directory, bundles, pairs, train_pairs, predictions):
    directory = Path(directory)
    files = {}
    for name, frame in (("pairs", pairs), ("training_pairs", train_pairs),
                        ("predictions", predictions), ("metrics", metrics_table(predictions))):
        path = directory / f"{name}.csv"
        payload = frame.to_csv(index=False).encode()
        atomic_write(path, payload)
        files[name] = {"file": path.name, "sha256": sha256(payload), "rows": len(frame)}
    for candidate, bundle in bundles.items():
        manifest = bundle.save(directory / candidate)
        # Verify the actual saved format reproduces predictions before exporting it.
        reloaded = ForecastModelBundle.load(manifest)
        reference = predictions.loc[predictions.candidate.eq(candidate), "predicted_power_normalized"].to_numpy()
        reproduced = reloaded.predict(pairs.loc[pairs.score_eligible])
        if not np.allclose(reference, reproduced, rtol=0, atol=1e-12):
            raise ValueError(f"Saved model failed reproduction: {candidate}")
        files[candidate] = {"file": f"{candidate}/manifest.json", "sha256": sha256(manifest.read_bytes())}
    atomic_write(directory / "artifacts.json", json_bytes(files))


def run_experiment(hourly, november_december_weather, january_weather, output_dir, source_hashes=None):
    """Execute the locked stages; this function never activates or alters the demo."""
    output_dir = Path(output_dir).resolve()
    if (output_dir / "active.json").exists():
        raise ValueError("An active deployment exists in this output directory; "
                         "choose a new --output-dir to preserve the reviewed model")
    if any((output_dir / name).exists() for name in ("decision.json", "deployment", "experiment.json")):
        raise ValueError("Experiment results already exist in this output directory; "
                         "choose a new --output-dir to preserve the decision and model")
    # Validate both entire archives before writing any selection result.
    pre_weather = require_archive(november_december_weather, "2025-10-30", "2025-12-30")
    jan_weather = require_archive(january_weather, "2025-12-31", "2026-01-31")
    pre_pairs = pair_weather(hourly, pre_weather)
    december_all = select_targets(pre_pairs, "2025-12-01", "2026-01-01", eligible_only=False)
    december = december_all.loc[december_all.score_eligible].reset_index(drop=True)
    train = select_targets(pre_pairs, "2025-11-01", "2025-11-29")
    first_december_issue = december_all.forecast_origin_utc.min()
    december_models = fit_candidates(hourly, train, "2025-11-29", first_december_issue)
    december_predictions = predict_candidates(december_models, december)
    decision = promotion_decision(december_predictions)
    decision["source_hashes"] = source_hashes or {}
    decision["january_used_for_selection"] = False
    _write_stage(output_dir / "december", december_models, december_all, train, december_predictions)
    # This decision is finalized before evaluating any January forecasts.
    atomic_write(output_dir / "decision.json", json_bytes(decision))

    # Use only the old December31..January31 weather file for January scoring.
    # Adding December30 forecasts of January1 would invalidate the Dec31 cutoff.
    jan_pairs = pair_weather(hourly, jan_weather)
    january_all = select_targets(jan_pairs, "2026-01-01", "2026-02-01", eligible_only=False)
    january = january_all.loc[january_all.score_eligible].reset_index(drop=True)
    january_train = select_targets(pre_pairs, "2025-11-01", "2025-12-31")
    first_january_issue = january_all.forecast_origin_utc.min()
    january_models = fit_candidates(hourly, january_train, "2025-12-31", first_january_issue)
    january_predictions = predict_candidates(january_models, january)
    _write_stage(output_dir / "january_descriptive", january_models, january_all, january_train, january_predictions)

    deployment_manifest = None
    if decision["gate_passed"]:
        selected = decision["selected_candidate"]
        combined = pd.concat([pre_pairs, jan_pairs], ignore_index=True)
        first_february_issue = utc_timestamp("2026-01-31T18:00:00Z")
        # Strictly earlier hour-end implies start < issue minus one hour.
        deployment_cutoff = first_february_issue.tz_convert(LOCAL) - pd.Timedelta(hours=1)
        deployment_pairs = select_targets(combined, "2025-11-01", deployment_cutoff)
        if not deployment_pairs.label_end_utc.lt(first_february_issue).all():
            raise ValueError("Deployment includes station labels unavailable at the first February issue")
        deployment = fit_candidates(hourly, deployment_pairs, deployment_cutoff, first_february_issue,
                                    candidates=(selected,))[selected]
        deployment.metadata.update(selection_period="2025-12", selected_candidate=selected,
                                   decision_sha256=sha256((output_dir / "decision.json").read_bytes()),
                                   frozen_for_all_february_issues=True)
        deployment_manifest = deployment.save(output_dir / "deployment")
        loaded = ForecastModelBundle.load(deployment_manifest)
        probe = jan_weather.loc[jan_weather.forecast_origin_utc.eq(first_february_issue)]
        if not np.allclose(loaded.predict(probe), deployment.predict(probe), rtol=0, atol=1e-12):
            raise ValueError("Deployment model failed saved-artifact reproduction")
        atomic_write(output_dir / "deployment/training_pairs.csv", deployment_pairs.to_csv(index=False).encode())

    summary = {
        "protocol": "2026-09-23-gfs-ml-experiment", "source_hashes": source_hashes or {},
        "candidate_parameters": CATBOOST_PARAMETERS,
        "december": {"candidate_count": 3, "training_cases": len(train),
                     "all_evaluation_cases": len(december_all), "scored_cases": len(december),
                     "earliest_issue_utc": first_december_issue.isoformat(),
                     "latest_training_label_end_utc": train.label_end_utc.max().isoformat()},
        "january_descriptive": {"independent_test": False, "used_for_selection": False,
                                "all_evaluation_cases": len(january_all), "scored_cases": len(january),
                                "earliest_issue_utc": first_january_issue.isoformat(),
                                "latest_training_label_end_utc": january_train.label_end_utc.max().isoformat()},
        "gate_passed": decision["gate_passed"], "selected_candidate": decision["selected_candidate"],
        "deployment_manifest": str(deployment_manifest) if deployment_manifest else None,
        "activation_performed": False,
        "limitations": ["December is a model-selection period, not an independent test.",
                        "January already informed earlier model comparison; it is descriptive only.",
                        "November–January training does not establish year-round generalization.",
                        "Repeated forecast cases for one target hour are correlated.",
                        "No February actual power is available; February accuracy cannot be measured.",
                        "Fixed UTC+5; source interval-start convention remains an assumption."],
    }
    atomic_write(output_dir / "experiment.json", json_bytes(summary))
    report = ["# GFS → power: locked experiment", "",
              "Selection used only December. January was evaluated after the decision and did not alter it.", "",
              f"Promotion gate passed: **{decision['gate_passed']}**.",
              f"Selected algorithm: **{decision['selected_candidate'] or 'none; keep current baseline'}**.", "",
              "## December", "",
              "| Candidate | Turbine | Horizon | Cases | RMSE | MAE | Bias |",
              "|---|---:|---|---:|---:|---:|---:|"]
    for row in metrics_table(december_predictions).itertuples():
        report.append(f"| {row.candidate} | {row.turbine_id} | {row.horizon} | {row.n} | {row.rmse:.6f} | {row.mae:.6f} | {row.bias:.6f} |")
    report += ["", "The exact gate checks are recorded in decision.json. Saved bundles verify artifact hashes on load.",
               "Deployment, when present, is frozen before January 31 18 UTC and requires separate explicit activation.",
               "", "## Limitations", ""] + ["- " + value for value in summary["limitations"]]
    atomic_write(output_dir / "REPORT.md", ("\n".join(report) + "\n").encode())
    return summary
