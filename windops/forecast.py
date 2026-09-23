"""Bounded February replay using archived weather and a frozen selected model.

No training or network request occurs on forecast reads. Timestamps identify the
start of hourly intervals, with the user-confirmed fixed UTC+5 local convention.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading

import numpy as np
import pandas as pd

from scripts.evaluate_baseline import EmpiricalPowerCurve, validate_weather


FIRST_ISSUE = date(2026, 1, 31)
LAST_ISSUE = date(2026, 2, 28)
ISSUE_DATES = tuple((FIRST_ISSUE + timedelta(days=n)).isoformat() for n in range(29))
LOCAL_TIMEZONE = timezone(timedelta(hours=5))
PROVIDER = "NOAA GFS 0.25° archive"
SERVICE_VERSION = "february-empirical-v1"


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path, payload):
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
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _issue_origin(issue_date):
    if not isinstance(issue_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date):
        raise ValueError("issue_date must be YYYY-MM-DD, between 2026-01-31 and 2026-02-28")
    try:
        parsed = date.fromisoformat(issue_date)
    except ValueError as exc:
        raise ValueError("Invalid issue_date") from exc
    if not FIRST_ISSUE <= parsed <= LAST_ISSUE:
        raise ValueError("issue_date must be between 2026-01-31 and 2026-02-28")
    return datetime.combine(parsed, datetime.min.time(), tzinfo=timezone.utc).replace(hour=18)


class ForecastService:
    def __init__(self, root, model_manifest=None):
        self.root = Path(root).resolve()
        self.cache_dir = self.root / "outputs/demo/cache"
        self.weather_path = self.root / "data/weather/noaa_february.csv"
        self.model_path = self.root / "outputs/baseline/empirical_curves.json"
        model_bytes = self.model_path.read_bytes()
        saved = json.loads(model_bytes)
        if set(saved) != {"1", "2"}:
            raise ValueError("Frozen model must contain turbine 1 and turbine 2")
        self.curves = {}
        for turbine, data in saved.items():
            centers = np.asarray(data["bin_centers_ms"], dtype=float)
            means = np.asarray(data["bin_mean_power_normalized"], dtype=float)
            if (centers.ndim != 1 or means.ndim != 1 or len(centers) == 0
                    or len(centers) != len(means) or not np.isfinite(centers).all()
                    or not np.isfinite(means).all() or not (np.diff(centers) > 0).all()
                    or (centers < 0).any() or ((means < 0) | (means > 1)).any()):
                raise ValueError(f"Invalid frozen curve for turbine {turbine}")
            curve = EmpiricalPowerCurve(data["bin_width_ms"])
            curve.centers, curve.means = centers, means
            self.curves[int(turbine)] = curve
        self.model = {
            "id": "empirical_curve", "label": "Frozen empirical power curve",
            "training_cutoff": "2025-12-31T00:00:00+05:00",
            "training_cutoff_exclusive": True,
            "version": SERVICE_VERSION + ":" + _sha256(model_bytes),
            "artifact_sha256": _sha256(model_bytes),
        }
        self.bundle = None
        activation_path = self.root / "outputs/gfs_ml/active.json"
        if model_manifest is not None or activation_path.exists():
            activation = json.loads(activation_path.read_text()) if model_manifest is None else None
            manifest = Path(model_manifest) if model_manifest is not None else self.root / "outputs/gfs_ml/deployment/manifest.json"
            if not manifest.is_file():
                raise FileNotFoundError("Activated GFS model is missing its deployment manifest")
            from windops.ml import ForecastModelBundle
            self.bundle = ForecastModelBundle.load(manifest)
            if activation is not None and activation.get("version") != self.bundle.metadata["version"]:
                raise ValueError("Activated GFS model version differs from the verified deployment")
            self.model = dict(self.bundle.metadata)
            if model_manifest is not None:
                self.cache_dir = self.cache_dir / "experimental" / self.model["artifact_sha256"]
        self._lock = threading.RLock()
        self._csv_cache = {}

    def _read_csv(self, path):
        # Content checks also detect a replacement that preserves size and mtime.
        # Parse once per content hash, but never use file metadata as provenance.
        payload = path.read_bytes()
        stamp = _sha256(payload)
        with self._lock:
            entry = self._csv_cache.get(path)
            if entry is None or entry[0] != stamp:
                frame = pd.read_csv(io.BytesIO(payload), float_precision="round_trip")
                if "forecast_origin_utc" not in frame:
                    raise ValueError(f"Weather file has no forecast_origin_utc: {path.name}")
                origins = pd.to_datetime(frame.forecast_origin_utc, utc=True, errors="coerce", format="mixed")
                entry = (stamp, frame, origins)
                self._csv_cache[path] = entry
            return entry[1], entry[2]

    def _load_rows(self, issue_date):
        origin = _issue_origin(issue_date)
        refreshed = self.root / "outputs/demo/weather" / f"{issue_date}.csv"
        sources = [path for path in (self.weather_path, refreshed) if path.exists()]
        sources.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for path in sources:
            frame, origins = self._read_csv(path)
            selected = frame.loc[origins.eq(origin)]
            if not selected.empty:
                # A present but invalid issue is an error, never silently replaced
                # with an older complete copy from the point cache.
                return selected.copy()
        points = self.root / "data/cache/noaa/points" / issue_date.replace("-", "")
        rows = []
        for hour in range(7, 55):
            path = points / f"f{hour:03d}.json"
            if not path.exists():
                raise FileNotFoundError(
                    f"Weather for {issue_date} is incomplete (missing f{hour:03d}). "
                    "Run scripts/replay_february.py after the archive download, "
                    "or explicitly request a weather refresh for this issue.")
            point = json.loads(path.read_text())
            if point.get("schema_version") != 1 or len(point.get("rows", [])) != 2:
                raise ValueError(f"Invalid weather point cache: {path.name}")
            for row in point["rows"]:
                if (row.get("gfs_forecast_hour") != hour
                        or row.get("available_at_utc") != point.get("available_at_utc")
                        or row.get("source_etag") != point.get("etag")
                        or row.get("source_key") != point.get("source_key")):
                    raise ValueError(f"Weather point provenance mismatch: {path.name}")
            rows.extend(point["rows"])
        return pd.DataFrame(rows)

    def _validated_rows(self, issue_date, rows):
        origin = _issue_origin(issue_date)
        frame = validate_weather(pd.DataFrame(rows))
        if (len(frame) != 96 or set(frame.turbine_id) != {"1", "2"}
                or not frame.forecast_origin_utc.eq(origin).all()
                or not frame.run_time_utc.eq(origin - timedelta(hours=6)).all()):
            raise ValueError("Weather must have 48 hours for each turbine, from the issue date's 12 UTC run")
        for turbine in ("1", "2"):
            if frame.loc[frame.turbine_id.eq(turbine), "lead_hours"].tolist() != list(range(1, 49)):
                raise ValueError(f"Incomplete 48-hour weather for turbine {turbine}")
        required_provenance = {"gfs_forecast_hour", "source_key", "source_etag"}
        if not required_provenance.issubset(frame.columns):
            raise ValueError("Weather lacks GFS forecast-hour, source-key or ETag provenance")
        gfs_hours = pd.to_numeric(frame.gfs_forecast_hour, errors="raise")
        if not np.array_equal(gfs_hours.to_numpy(), (frame.lead_hours + 6).to_numpy()):
            raise ValueError("GFS forecast hours must be f007 through f054")
        keys = [f"gfs.{issue_date.replace('-', '')}/12/atmos/gfs.t12z.pgrb2.0p25.f{h:03d}"
                for h in gfs_hours.astype(int)]
        if frame.source_key.tolist() != keys or frame.source_etag.isna().any() or frame.source_etag.eq("").any():
            raise ValueError("Weather source-key/ETag provenance is invalid")
        frame["turbine_id"] = frame.turbine_id.astype(int)
        frame["gfs_forecast_hour"] = gfs_hours.astype(int)
        for column in ("forecast_origin_utc", "run_time_utc", "valid_time_utc", "available_at_utc"):
            frame[column] = frame[column].map(lambda value: value.isoformat())
        result = frame.to_dict(orient="records")
        _json_bytes(result)  # Reject NaN in optional provenance fields as well.
        return result

    def _fingerprint(self, issue_date, rows):
        return _sha256(_json_bytes({"issue_date": issue_date, "model_version": self.model["version"],
                                  "weather_rows": rows}))

    def list_issues(self):
        available = []
        for issue_date in ISSUE_DATES:
            try:
                self._validated_rows(issue_date, self._load_rows(issue_date))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            available.append(issue_date)
        return available

    def _refresh_weather(self, issue_date):
        """Fetch one run using a fresh archive listing and isolated point cache.

        --refresh-listings bypasses both cached listings and saved research XML;
        the isolated cache prevents reuse of old extracted points. Replace the
        issue's weather only after all 96 rows pass chronology and provenance.
        """
        script = self.root / "scripts/fetch_noaa.py"
        if not script.exists():
            raise FileNotFoundError("Weather refresh requires scripts/fetch_noaa.py")
        directory = self.root / "outputs/demo/weather"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"refresh-{issue_date}-", dir=directory) as temporary:
            output = Path(temporary) / "weather.csv"
            command = [sys.executable, str(script), "--start-date", issue_date,
                       "--end-date", issue_date, "--lead-start", "7", "--lead-end", "54",
                       "--workers", "8", "--cache", str(Path(temporary) / "cache"),
                       "--output", str(output), "--refresh-listings"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
            if result.returncode:
                raise RuntimeError("NOAA weather refresh failed: " + (result.stderr or result.stdout)[-1200:])
            self._validated_rows(issue_date, pd.read_csv(output, float_precision="round_trip"))
            _atomic_write(directory / f"{issue_date}.csv", output.read_bytes())

    def get_weather(self, issue_date, refresh=False):
        origin = _issue_origin(issue_date)
        if not isinstance(refresh, bool):
            raise ValueError("refresh must be a boolean")
        if refresh:
            self._refresh_weather(issue_date)
        rows = self._validated_rows(issue_date, self._load_rows(issue_date))
        return {
            "issue_date": issue_date, "forecast_origin_utc": origin.isoformat(),
            "forecast_origin_local": origin.astimezone(LOCAL_TIMEZONE).isoformat(),
            "run_time_utc": (origin - timedelta(hours=6)).isoformat(),
            "available_at_utc": max(row["available_at_utc"] for row in rows),
            "source_fingerprint": self._fingerprint(issue_date, rows),
            "source": {"provider": PROVIDER, "url": "https://noaa-gfs-bdp-pds.s3.amazonaws.com",
                       "interpolation": "bilinear U/V and temperature; wind magnitude after interpolation"},
            "rows": rows,
        }

    def predict(self, issue_date, weather):
        origin = _issue_origin(issue_date)
        if not isinstance(weather, dict) or weather.get("issue_date") != issue_date:
            raise ValueError("Weather issue does not match requested issue_date")
        rows = self._validated_rows(issue_date, weather.get("rows", []))
        fingerprint = self._fingerprint(issue_date, rows)
        available_at = max(row["available_at_utc"] for row in rows)
        if (weather.get("source_fingerprint") != fingerprint
                or weather.get("forecast_origin_utc") != origin.isoformat()
                or weather.get("forecast_origin_local") != origin.astimezone(LOCAL_TIMEZONE).isoformat()
                or weather.get("run_time_utc") != (origin - timedelta(hours=6)).isoformat()
                or weather.get("available_at_utc") != available_at):
            raise ValueError("Weather fingerprint or issue-time provenance changed; obtain weather again")
        predicted = []
        summaries = []
        for turbine in (1, 2):
            turbine_weather = [row for row in rows if row["turbine_id"] == turbine]
            wind = np.asarray([row["wind_speed_100m_ms"] for row in turbine_weather], dtype=float)
            temperatures = np.asarray([row["temperature_2m_c"] for row in turbine_weather], dtype=float)
            power = (self.bundle.predict(pd.DataFrame(turbine_weather)) if self.bundle is not None
                     else self.curves[turbine].predict(wind))
            for row, value in zip(turbine_weather, power):
                valid = datetime.fromisoformat(row["valid_time_utc"])
                local = valid.astimezone(LOCAL_TIMEZONE)
                predicted.append({
                    "turbine_id": turbine, "valid_time_utc": valid.isoformat(),
                    "valid_time_local": local.isoformat(), "lead_hours": row["lead_hours"],
                    "power_normalized": float(value), "wind_speed_ms": row["wind_speed_100m_ms"],
                    "temperature_c": row["temperature_2m_c"],
                    "is_february_target": local.year == 2026 and local.month == 2,
                })
            summaries.append({
                "turbine_id": turbine, "hours": 48,
                "mean_power_normalized": float(power.mean()), "min_power_normalized": float(power.min()),
                "max_power_normalized": float(power.max()), "mean_power_24h": float(power[:24].mean()),
                "mean_power_48h": float(power.mean()), "mean_power_second_24h": float(power[24:].mean()),
                "mean_wind_speed_ms": float(wind.mean()), "max_wind_speed_ms": float(wind.max()),
                "min_temperature_c": float(temperatures.min()), "max_temperature_c": float(temperatures.max()),
                "peak_valid_time_local": predicted[(turbine - 1) * 48 + int(power.argmax())]["valid_time_local"],
            })
        return {
            "issue_date": issue_date, "forecast_origin_utc": origin.isoformat(),
            "forecast_origin_local": origin.astimezone(LOCAL_TIMEZONE).isoformat(),
            "model": dict(self.model),
            "source": {"provider": PROVIDER, "run_time_utc": weather["run_time_utc"],
                       "available_at_utc": available_at, "fingerprint": fingerprint},
            "rows": predicted,
            "summary": {"turbines": summaries, "rows": len(predicted),
                        "february_target_rows": sum(row["is_february_target"] for row in predicted)},
            "cached": False,
        }

    def get_forecast(self, issue_date, refresh=False):
        weather = self.get_weather(issue_date, refresh=refresh)
        path = self.cache_dir / f"{issue_date}.json"
        with self._lock:
            if path.exists() and not refresh:
                try:
                    cached = json.loads(path.read_text())
                    if (cached["source"]["fingerprint"] == weather["source_fingerprint"]
                            and cached["model"]["version"] == self.model["version"]
                            and cached["issue_date"] == issue_date and len(cached["rows"]) == 96):
                        cached["cached"] = True
                        return cached
                except (OSError, ValueError, TypeError, KeyError):
                    pass  # An interrupted/corrupt cache is safely recomputed.
            result = self.predict(issue_date, weather)
            _atomic_write(path, _json_bytes(result))
            return result

    def export_february(self, output_dir=None):
        directory = Path(output_dir).resolve() if output_dir is not None else self.root / "outputs/february"
        forecasts = [self.get_forecast(issue) for issue in ISSUE_DATES]
        all_rows = []
        for forecast in forecasts:
            for row in forecast["rows"]:
                all_rows.append({"issue_date": forecast["issue_date"],
                                 "forecast_origin_utc": forecast["forecast_origin_utc"],
                                 "forecast_origin_local": forecast["forecast_origin_local"],
                                 "run_time_utc": forecast["source"]["run_time_utc"],
                                 "available_at_utc": forecast["source"]["available_at_utc"],
                                 "source_fingerprint": forecast["source"]["fingerprint"],
                                 "model_version": forecast["model"]["version"], **row})
        long = pd.DataFrame(all_rows)
        day_ahead = long.loc[(long.lead_hours <= 24) & long.is_february_target].copy()
        if (len(long) != 2784 or len(day_ahead) != 1344
                or day_ahead.duplicated(["turbine_id", "valid_time_utc"]).any()
                or day_ahead.groupby("turbine_id").size().tolist() != [672, 672]):
            raise ValueError("February export coverage checks failed")
        paths = {"long_csv": directory / "forecast_48h_long.csv",
                 "day_ahead_csv": directory / "forecast_day_ahead_february.csv",
                 "manifest_json": directory / "manifest.json", "report_md": directory / "REPORT.md"}
        payloads = {"long_csv": long.to_csv(index=False).encode(),
                    "day_ahead_csv": day_ahead.to_csv(index=False).encode()}
        manifest = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(), "timezone": "UTC+5",
            "timezone_confirmed_by_user": True, "interval_convention": "interval_start (assumed)",
            "issues": list(ISSUE_DATES), "rows": len(long), "day_ahead_rows": len(day_ahead),
            "march_spillover_rows": int((~long.is_february_target).sum()), "model": self.model,
            "source": PROVIDER,
            "issue_fingerprints": {f["issue_date"]: f["source"]["fingerprint"] for f in forecasts},
            "files": {key: {"name": paths[key].name, "sha256": _sha256(value)}
                      for key, value in payloads.items()},
            "limitations": ["No February actuals are available; no February accuracy is claimed.",
                            "Power is normalized to 0..1, not MW or MWh.",
                            "Hourly source timestamps are assumed to label interval starts.",
                            ("Selected GFS model is frozen for February; January metrics describe an earlier training version."
                             if self.bundle is not None else
                             "Frozen empirical curves use linear interpolation and constant endpoint extrapolation.")],
        }
        report = ("# February 2026 forecast replay\n\n"
                  "29 daily issues, January 31–February 28 at 23:00 UTC+5 (18:00 UTC). "
                  "Each issue uses the same-day NOAA GFS 12 UTC run and f007–f054, "
                  "with publication no later than the issue.\n\n"
                  "- Full 48-hour trajectories: **2,784 rows**, 48 hours per turbine per issue.\n"
                  "- February day-ahead series: **1,344 rows**, 672 unique hours per turbine.\n"
                  f"- March spillover retained in full trajectories: **{manifest['march_spillover_rows']} rows**.\n"
                  f"- Model: {self.model['label']}; training cutoff {self.model['training_cutoff']}. "
                  "No retraining during replay.\n\n"
                  "`manifest.json` records model hash, issue fingerprints and export SHA-256 hashes.\n\n"
                  + "\n".join("- " + item for item in manifest["limitations"]) + "\n")
        for key, payload in payloads.items():
            _atomic_write(paths[key], payload)
        _atomic_write(paths["report_md"], report.encode())
        _atomic_write(paths["manifest_json"], _json_bytes(manifest))
        return {key: str(path) for key, path in paths.items()}
