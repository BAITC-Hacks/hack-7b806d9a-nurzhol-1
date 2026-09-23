"""February service checks use synthetic weather, never fabricated evaluation labels."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from windops.forecast import ForecastService


def weather_rows(issue_date="2026-01-31"):
    origin = datetime.fromisoformat(issue_date).replace(hour=18, tzinfo=timezone.utc)
    return [dict(turbine_id=turbine, forecast_origin_utc=origin.isoformat(),
                 run_time_utc=(origin - timedelta(hours=6)).isoformat(),
                 valid_time_utc=(origin + timedelta(hours=lead)).isoformat(),
                 lead_hours=lead, wind_speed_100m_ms=0.75,
                 temperature_2m_c=-5.0, wind_direction_100m_deg=180.0,
                 available_at_utc=(origin - timedelta(hours=2)).isoformat(),
                 gfs_forecast_hour=lead + 6,
                 source_key=f"gfs.{issue_date.replace('-', '')}/12/atmos/gfs.t12z.pgrb2.0p25.f{lead+6:03d}",
                 source_etag='"fixture"', interpolation="bilinear_uv_temperature")
            for turbine in (1, 2) for lead in range(1, 49)]


class ForecastServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        model_dir = self.root / "outputs/baseline"
        model_dir.mkdir(parents=True)
        curves = {str(t): {"bin_width_ms": 0.5, "bin_centers_ms": [0.25, 1.25],
                           "bin_mean_power_normalized": [0.0, 0.8 / t],
                           "wind_min_ms": 0.1, "wind_max_ms": 1.5}
                  for t in (1, 2)}
        (model_dir / "empirical_curves.json").write_text(json.dumps(curves))
        self.weather_path = self.root / "data/weather/noaa_february.csv"
        self.weather_path.parent.mkdir(parents=True)
        self.rows = weather_rows()
        self.write_weather(self.rows)
        self.service = ForecastService(self.root)

    def write_weather(self, rows):
        pd.DataFrame(rows).to_csv(self.weather_path, index=False)

    def test_midnight_boundary_and_frozen_curve_interpolation(self):
        result = self.service.get_forecast("2026-01-31")
        self.assertEqual(len(result["rows"]), 96)
        row = result["rows"][0]
        self.assertEqual(row["valid_time_utc"], "2026-01-31T19:00:00+00:00")
        self.assertEqual(row["valid_time_local"], "2026-02-01T00:00:00+05:00")
        self.assertAlmostEqual(row["power_normalized"], 0.4)
        self.assertEqual(row["turbine_id"], 1)
        self.assertEqual(result["forecast_origin_local"], "2026-01-31T23:00:00+05:00")
        self.assertTrue(all(row["is_february_target"] for row in result["rows"]))
        json.dumps(result, allow_nan=False)

    def test_bad_dates_missing_hours_and_extra_turbines_are_rejected(self):
        for issue in ("2026-01-30", "2026-03-01", "../2026-02-01", "2026-2-1"):
            with self.subTest(issue=issue), self.assertRaises(ValueError):
                self.service.get_forecast(issue)
        for malformed in (self.rows[:-1], self.rows + [dict(self.rows[0], turbine_id=3)],
                          self.rows + [self.rows[0]]):
            self.write_weather(malformed)
            with self.assertRaises(ValueError):
                self.service.get_forecast("2026-01-31")

    def test_late_or_wrong_run_and_nonfinite_weather_are_rejected(self):
        for column, value in (("available_at_utc", "2026-01-31T18:00:01+00:00"),
                              ("run_time_utc", "2026-01-31T06:00:00+00:00"),
                              ("wind_speed_100m_ms", float("inf")),
                              ("gfs_forecast_hour", 55)):
            with self.subTest(column=column):
                modified = deepcopy(self.rows)
                modified[0][column] = value
                self.write_weather(modified)
                with self.assertRaises(ValueError):
                    self.service.get_forecast("2026-01-31")

    def test_issue_fingerprint_changes_only_when_relevant_source_changes(self):
        self.write_weather(self.rows + weather_rows("2026-02-01"))
        first = self.service.get_forecast("2026-01-31")
        self.assertFalse(first["cached"])
        self.assertTrue(self.service.get_forecast("2026-01-31")["cached"])
        other = weather_rows("2026-02-01")
        other[0]["wind_speed_100m_ms"] = 0.5
        self.write_weather(self.rows + other)
        self.assertTrue(self.service.get_forecast("2026-01-31")["cached"])
        self.rows[0]["wind_speed_100m_ms"] = 1.25
        self.write_weather(self.rows + other)
        changed = self.service.get_forecast("2026-01-31")
        self.assertFalse(changed["cached"])
        self.assertNotEqual(first["source"]["fingerprint"], changed["source"]["fingerprint"])
        self.assertAlmostEqual(changed["rows"][0]["power_normalized"], 0.8)

    def test_predict_revalidates_rows_and_rejects_stale_fingerprint(self):
        weather = self.service.get_weather("2026-01-31")
        tampered = deepcopy(weather)
        tampered["rows"][0]["wind_speed_100m_ms"] = 1.25
        with self.assertRaises(ValueError):
            self.service.predict("2026-01-31", tampered)
        tampered = deepcopy(weather)
        tampered["rows"][0]["available_at_utc"] = "2026-01-31T18:00:01+00:00"
        with self.assertRaises(ValueError):
            self.service.predict("2026-01-31", tampered)
        with self.assertRaises(ValueError):
            self.service.predict("2026-02-01", weather)

    def test_changed_weather_invalidates_cache_even_with_preserved_file_metadata(self):
        before = self.service.get_forecast("2026-01-31")
        stat = self.weather_path.stat()
        original = self.weather_path.read_bytes()
        self.weather_path.write_bytes(original.replace(b",0.75,", b",1.25,", 1))
        os.utime(self.weather_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        after = self.service.get_forecast("2026-01-31")
        self.assertNotEqual(before["source"]["fingerprint"], after["source"]["fingerprint"])
        self.assertAlmostEqual(after["rows"][0]["power_normalized"], 0.8)

    def test_partial_csv_does_not_fall_back_to_complete_stale_points(self):
        self.write_points()
        self.write_weather(self.rows[:-1])
        with self.assertRaises(ValueError):
            self.service.get_forecast("2026-01-31")

    def write_points(self):
        point_dir = self.root / "data/cache/noaa/points/20260131"
        point_dir.mkdir(parents=True, exist_ok=True)
        for lead in range(1, 49):
            rows = [r for r in self.rows if r["lead_hours"] == lead]
            obj = {"schema_version": 1, "rows": rows,
                   "available_at_utc": rows[0]["available_at_utc"],
                   "etag": rows[0]["source_etag"], "source_key": rows[0]["source_key"]}
            (point_dir / f"f{lead+6:03d}.json").write_text(json.dumps(obj))

    def test_complete_points_work_before_full_csv_exists(self):
        self.weather_path.unlink()
        self.write_points()
        self.assertEqual(self.service.list_issues(), ["2026-01-31"])
        self.assertEqual(len(self.service.get_forecast("2026-01-31")["rows"]), 96)
        (self.root / "data/cache/noaa/points/20260131/f054.json").unlink()
        self.assertEqual(self.service.list_issues(), [])
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.service.get_forecast("2026-01-31")

    def test_model_artifact_change_invalidates_previous_cache(self):
        first = self.service.get_forecast("2026-01-31")
        path = self.root / "outputs/baseline/empirical_curves.json"
        curves = json.loads(path.read_text())
        curves["1"]["bin_mean_power_normalized"][1] = 0.6
        path.write_text(json.dumps(curves))
        result = ForecastService(self.root).get_forecast("2026-01-31")
        self.assertFalse(result["cached"])
        self.assertNotEqual(first["model"]["version"], result["model"]["version"])
        self.assertAlmostEqual(result["rows"][0]["power_normalized"], 0.3)

    def test_explicit_activation_with_missing_model_fails_instead_of_silent_baseline(self):
        directory = self.root / "outputs/gfs_ml"
        directory.mkdir(parents=True)
        (directory / "active.json").write_text(json.dumps({"version": "missing-model"}))
        with self.assertRaises(FileNotFoundError):
            ForecastService(self.root)

    def save_affine_deployment(self):
        from windops.ml import ForecastModelBundle
        curves = json.loads((self.root / "outputs/baseline/empirical_curves.json").read_text())
        metadata = {"id": "affine_wind", "label": "Calibrated wind model",
                    "training_cutoff": "2025-11-29T00:00:00+05:00",
                    "training_cutoff_exclusive": True}
        bundle = ForecastModelBundle("affine_wind", metadata, curves=curves,
                                     calibration={str(t): {"slope": 1, "intercept": 0.25}
                                                  for t in (1, 2)})
        bundle.save(self.root / "outputs/gfs_ml/deployment")
        return bundle

    def test_verified_activation_changes_predictions_and_invalidates_baseline_cache(self):
        baseline = self.service.get_forecast("2026-01-31")
        bundle = self.save_affine_deployment()
        # Experiment artifacts alone never activate a candidate.
        self.assertAlmostEqual(ForecastService(self.root).get_forecast("2026-01-31")["rows"][0]["power_normalized"], 0.4)
        (self.root / "outputs/gfs_ml/active.json").write_text(json.dumps({"version": bundle.metadata["version"]}))
        service = ForecastService(self.root)
        result = service.get_forecast("2026-01-31")
        self.assertEqual(result["model"]["id"], "affine_wind")
        self.assertFalse(result["cached"])
        self.assertNotEqual(baseline["source"]["fingerprint"], result["source"]["fingerprint"])
        self.assertAlmostEqual(result["rows"][0]["power_normalized"], 0.6)
        self.assertAlmostEqual(result["rows"][48]["power_normalized"], 0.3)

    def test_activation_does_not_accept_an_unreviewed_artifact_version(self):
        self.save_affine_deployment()
        (self.root / "outputs/gfs_ml/active.json").write_text(json.dumps({"version": "different"}))
        with self.assertRaisesRegex(ValueError, "version"):
            ForecastService(self.root)

    def test_explicit_model_preview_keeps_default_model_and_cache_unchanged(self):
        self.service.get_forecast("2026-01-31")
        cache_path = self.root / "outputs/demo/cache/2026-01-31.json"
        original_cache = cache_path.read_bytes()
        self.save_affine_deployment()
        preview = ForecastService(self.root, model_manifest=self.root / "outputs/gfs_ml/deployment/manifest.json")
        self.assertAlmostEqual(preview.get_forecast("2026-01-31")["rows"][0]["power_normalized"], 0.6)
        self.assertEqual(cache_path.read_bytes(), original_cache)
        self.assertFalse((self.root / "outputs/gfs_ml/active.json").exists())
        self.assertAlmostEqual(ForecastService(self.root).get_forecast("2026-01-31")["rows"][0]["power_normalized"], 0.4)

    def test_cli_preview_refuses_to_overwrite_the_working_export(self):
        self.save_affine_deployment()
        directory = self.root / "outputs/february"
        directory.mkdir()
        csv = directory / "forecast_day_ahead_february.csv"
        csv.write_bytes(b"preserve this working export\n")
        script = Path(__file__).resolve().parents[1] / "scripts/replay_february.py"
        base = [sys.executable, str(script), "--root", str(self.root), "--model-manifest",
                str(self.root / "outputs/gfs_ml/deployment/manifest.json")]
        for extra in ([], ["--output-dir", str(directory)]):
            result = subprocess.run(base + extra, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("separate --output-dir", result.stderr)
            self.assertEqual(csv.read_bytes(), b"preserve this working export\n")

    def test_explicit_refresh_fetches_only_requested_run_and_recomputes(self):
        before = self.service.get_forecast("2026-01-31")
        script = self.root / "scripts/fetch_noaa.py"
        script.parent.mkdir()
        script.touch()
        fresh = deepcopy(self.rows)
        fresh[0]["wind_speed_100m_ms"] = 1.25

        # Replace only the external NOAA download boundary. All validation,
        # persistence, interpolation and fingerprinting still run for real.
        def download_one_run(command, **kwargs):
            self.assertIn("--refresh-listings", command)
            arguments = dict(zip(command[2::2], command[3::2]))
            self.assertEqual(arguments["--start-date"], "2026-01-31")
            self.assertEqual(arguments["--end-date"], "2026-01-31")
            self.assertEqual(arguments["--lead-start"], "7")
            self.assertEqual(arguments["--lead-end"], "54")
            pd.DataFrame(fresh).to_csv(arguments["--output"], index=False)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("windops.forecast.subprocess.run", side_effect=download_one_run):
            after = self.service.get_forecast("2026-01-31", refresh=True)
        self.assertFalse(after["cached"])
        self.assertNotEqual(before["source"]["fingerprint"], after["source"]["fingerprint"])
        self.assertAlmostEqual(after["rows"][0]["power_normalized"], 0.8)
        self.assertEqual(self.service.get_forecast("2026-01-31")["rows"], after["rows"])

    def test_failed_refresh_does_not_replace_previously_valid_weather(self):
        before = self.service.get_forecast("2026-01-31")
        script = self.root / "scripts/fetch_noaa.py"
        script.parent.mkdir()
        script.touch()
        failed = subprocess.CompletedProcess(["download"], 1, stdout="", stderr="archive unavailable")
        with patch("windops.forecast.subprocess.run", return_value=failed):
            with self.assertRaises(RuntimeError):
                self.service.get_forecast("2026-01-31", refresh=True)
        self.assertEqual(self.service.get_forecast("2026-01-31")["rows"], before["rows"])

    def test_cli_outputs_paths_to_complete_exports(self):
        rows = []
        for date in pd.date_range("2026-01-31", "2026-02-28"):
            rows.extend(weather_rows(date.date().isoformat()))
        self.write_weather(rows)
        script = Path(__file__).resolve().parents[1] / "scripts/replay_february.py"
        result = subprocess.run([sys.executable, str(script), "--root", str(self.root)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = json.loads(result.stdout)
        self.assertEqual(len(pd.read_csv(paths["day_ahead_csv"])), 1344)
        self.assertTrue(Path(paths["report_md"]).is_file())

    def test_full_february_export_preserves_march_spillover_and_hashes(self):
        rows = []
        for date in pd.date_range("2026-01-31", "2026-02-28"):
            rows.extend(weather_rows(date.date().isoformat()))
        self.write_weather(rows)
        exported = self.service.export_february()
        long = pd.read_csv(exported["long_csv"])
        daily = pd.read_csv(exported["day_ahead_csv"])
        self.assertEqual(len(long), 2784)
        self.assertEqual(len(daily), 1344)
        self.assertEqual(daily.groupby("turbine_id").size().tolist(), [672, 672])
        self.assertEqual(long.groupby("issue_date").size().tolist(), [96] * 29)
        self.assertTrue((daily.lead_hours <= 24).all())
        self.assertTrue(daily.is_february_target.all())
        self.assertTrue(long.valid_time_local.str.startswith("2026-03-").any())
        self.assertFalse(daily.duplicated(["turbine_id", "valid_time_utc"]).any())
        manifest = json.loads(Path(exported["manifest_json"]).read_text())
        self.assertEqual(manifest["rows"], 2784)
        self.assertEqual(manifest["day_ahead_rows"], 1344)
        for key in ("long_csv", "day_ahead_csv"):
            expected = hashlib.sha256(Path(exported[key]).read_bytes()).hexdigest()
            self.assertEqual(manifest["files"][key]["sha256"], expected)


if __name__ == "__main__":
    unittest.main()
