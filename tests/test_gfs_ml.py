"""Protocol tests for the preregistered GFS-to-power experiment."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

from windops.ml import (ForecastModelBundle, feature_frame, fit_candidates,
                       pair_weather, promotion_decision, run_experiment, select_targets)


def hourly_fixture():
    rows = []
    for turbine in (1, 2):
        for stamp in pd.date_range("2025-10-30", "2026-02-01", freq="h"):
            wind = float(1 + stamp.hour / 4)
            rows.append(dict(time_naive=stamp, turbine_id=turbine, wind_speed_ms=wind,
                             power_normalized=min(wind / 10, 1) / turbine,
                             temperature_c=0.0, observations=6, is_complete=True, split="train"))
    return pd.DataFrame(rows)


def weather_fixture(dates=("2025-10-31", "2025-11-01", "2025-11-28", "2025-11-29", "2025-11-30", "2025-12-01")):
    rows = []
    for date in dates:
        origin = pd.Timestamp(date + "T18:00:00Z")
        for turbine in (1, 2):
            for lead in range(1, 49):
                valid = origin + pd.Timedelta(hours=lead)
                local_hour = (valid + pd.Timedelta(hours=5)).hour
                rows.append(dict(turbine_id=turbine, forecast_origin_utc=origin,
                                 run_time_utc=origin - pd.Timedelta(hours=6), valid_time_utc=valid,
                                 lead_hours=lead, wind_speed_100m_ms=float(local_hour / 8),
                                 temperature_2m_c=-4.0, wind_direction_100m_deg=180.0,
                                 available_at_utc=origin - pd.Timedelta(hours=2)))
    return pd.DataFrame(rows)


class FeatureAndChronologyTests(unittest.TestCase):
    def test_features_ignore_future_measurements_and_keep_original_order(self):
        weather = weather_fixture().iloc[[20, 0, 49]].copy()
        before = feature_frame(weather)
        poisoned = weather.assign(actual_power_normalized=[999, -999, np.nan],
                                  actual_wind_speed_ms=[999, -999, np.nan],
                                  wind_speed_ms=[999, -999, np.nan], temperature_c=999)
        pd.testing.assert_frame_equal(before, feature_frame(poisoned))
        np.testing.assert_allclose(before.wind_speed_100m_ms, [2.5, 0.0, 0.125])
        self.assertAlmostEqual(before.iloc[1].local_hour_sin, 0.0)
        self.assertAlmostEqual(before.iloc[1].local_hour_cos, 1.0)
        self.assertNotIn("actual_power_normalized", before.columns)

    def test_pairing_uses_local_hour_and_retains_duplicate_targets_within_split(self):
        pairs = pair_weather(hourly_fixture(), weather_fixture())
        row = pairs.loc[(pairs.turbine_id == "1") & (pairs.lead_hours == 1)].iloc[0]
        self.assertEqual(row.valid_time_local.isoformat(), "2025-11-01T00:00:00+05:00")
        self.assertEqual(row.label_end_utc.isoformat(), "2025-10-31T20:00:00+00:00")
        self.assertAlmostEqual(row.actual_power_normalized, 0.1)
        train = select_targets(pairs, "2025-11-01", "2025-11-29")
        valid = select_targets(pairs, "2025-12-01", "2026-01-01")
        self.assertTrue(train.duplicated(["turbine_id", "valid_time_utc"]).any())
        train_targets = set(zip(train.turbine_id, train.valid_time_utc))
        self.assertFalse(train_targets.intersection(zip(valid.turbine_id, valid.valid_time_utc)))
        self.assertTrue((train.label_end_utc < valid.forecast_origin_utc.min()).all())

    def test_incomplete_labels_and_late_weather_cannot_enter_fit(self):
        hourly = hourly_fixture()
        hourly.loc[(hourly.turbine_id == 1) & (hourly.time_naive == "2025-11-01"), "is_complete"] = False
        pairs = pair_weather(hourly, weather_fixture())
        selected = select_targets(pairs, "2025-11-01", "2025-11-29")
        self.assertFalse(((selected.turbine_id == "1") & (selected.valid_time_local == pd.Timestamp("2025-11-01T00:00:00+05:00"))).any())
        bad_weather = weather_fixture()
        bad_weather.loc[0, "available_at_utc"] = bad_weather.loc[0, "forecast_origin_utc"] + pd.Timedelta(seconds=1)
        with self.assertRaises(ValueError):
            pair_weather(hourly, bad_weather)

    def test_fit_rejects_labels_ending_at_first_evaluation_issue(self):
        hourly = hourly_fixture()
        pairs = select_targets(pair_weather(hourly, weather_fixture()), "2025-11-01", "2025-11-29")
        with self.assertRaisesRegex(ValueError, "label.*end|available"):
            fit_candidates(hourly, pairs, "2025-11-29", pairs.label_end_utc.max())


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hourly = hourly_fixture()
        cls.weather = weather_fixture()
        pairs = select_targets(pair_weather(cls.hourly, cls.weather), "2025-11-01", "2025-11-29")
        cls.bundles = fit_candidates(cls.hourly, pairs, "2025-11-29", "2025-11-29T18:00:00Z")

    def test_affine_fit_learns_station_wind_without_using_future_station_labels(self):
        # Fixture relation is station wind = 2 * GFS speed + 1.
        affine = self.bundles["affine_wind"]
        raw = self.bundles["raw_empirical"]
        first = self.weather.iloc[[8]]  # GFS=1, corrected wind=3; empirical bin interpolation=0.2875.
        self.assertAlmostEqual(affine.predict(first)[0], 0.2875)
        self.assertLess(raw.predict(first)[0], affine.predict(first)[0])
        modified = self.hourly.copy()
        modified.loc[modified.time_naive >= "2025-11-29", ["power_normalized", "wind_speed_ms"]] = [0.99, 50]
        pairs = select_targets(pair_weather(modified, self.weather), "2025-11-01", "2025-11-29")
        refitted = fit_candidates(modified, pairs, "2025-11-29", "2025-11-29T18:00:00Z")
        for name in self.bundles:
            np.testing.assert_allclose(self.bundles[name].predict(self.weather), refitted[name].predict(self.weather))

    def test_each_saved_model_round_trips_with_row_order_and_hash_validation(self):
        weather = self.weather.iloc[[80, 1, 48, 12]].copy()
        for name, bundle in self.bundles.items():
            with self.subTest(model=name), tempfile.TemporaryDirectory() as directory:
                path = bundle.save(Path(directory))
                loaded = ForecastModelBundle.load(path)
                np.testing.assert_allclose(bundle.predict(weather), loaded.predict(weather), atol=1e-12)
                rowwise = [loaded.predict(weather.iloc[[i]])[0] for i in range(len(weather))]
                np.testing.assert_allclose(loaded.predict(weather), rowwise, atol=1e-12)
                self.assertTrue(np.all((loaded.predict(weather) >= 0) & (loaded.predict(weather) <= 1)))
                self.assertEqual(loaded.metadata["id"], name)
                manifest = json.loads(path.read_text())
                artifact = next(iter(manifest["artifacts"].values()))
                target = path.parent / artifact["file"]
                target.write_bytes(target.read_bytes() + b"changed")
                with self.assertRaisesRegex(ValueError, "hash|SHA"):
                    ForecastModelBundle.load(path)

    def test_manifest_rejects_paths_outside_its_own_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.bundles["raw_empirical"].save(Path(directory) / "model")
            manifest = json.loads(path.read_text())
            artifact = next(iter(manifest["artifacts"].values()))
            outside = Path(directory) / "outside.json"
            outside.write_bytes((path.parent / artifact["file"]).read_bytes())
            artifact["file"] = "../outside.json"
            artifact["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "path|directory|filename"):
                ForecastModelBundle.load(path)


def gate_fixture():
    rows = []
    for turbine in (1, 2):
        for day in ("2025-12-05", "2025-12-20"):
            for lead in (1, 25):
                for candidate, error in (("raw_empirical", 0.2), ("affine_wind", 0.18), ("catboost_gfs", 0.1)):
                    rows.append(dict(turbine_id=str(turbine), valid_time_local=pd.Timestamp(day, tz="Etc/GMT-5"),
                                     valid_time_utc=pd.Timestamp(day, tz="UTC"),
                                     forecast_origin_utc=pd.Timestamp(day, tz="UTC") - pd.Timedelta(hours=lead),
                                     lead_hours=lead, candidate=candidate, actual_power_normalized=0.3,
                                     predicted_power_normalized=0.3 + error))
    return pd.DataFrame(rows)


class PromotionGateTests(unittest.TestCase):
    def test_gate_selects_best_passing_candidate_and_requires_each_turbine(self):
        frame = gate_fixture()
        self.assertEqual(promotion_decision(frame)["selected_candidate"], "catboost_gfs")
        frame.loc[(frame.candidate == "catboost_gfs") & (frame.turbine_id == "2"), "predicted_power_normalized"] = 0.51
        self.assertEqual(promotion_decision(frame)["selected_candidate"], "affine_wind")

    def test_better_pooled_rmse_cannot_hide_worse_horizon_or_month_half(self):
        frame = gate_fixture()
        frame.loc[(frame.candidate == "catboost_gfs") & (frame.lead_hours == 25), "predicted_power_normalized"] = 0.501
        decision = promotion_decision(frame)
        self.assertFalse(decision["candidates"]["catboost_gfs"]["passed"])
        frame = gate_fixture()
        frame.loc[(frame.candidate == "catboost_gfs") & (frame.valid_time_local.dt.day >= 16), "predicted_power_normalized"] = 0.501
        self.assertFalse(promotion_decision(frame)["candidates"]["catboost_gfs"]["passed"])

    def test_missing_or_mismatched_candidate_cases_cannot_pass(self):
        frame = gate_fixture().iloc[1:]
        with self.assertRaises(ValueError):
            promotion_decision(frame)


class ExperimentCommandTests(unittest.TestCase):
    def test_failed_rerun_cannot_replace_the_decision_for_an_existing_deployment(self):
        hourly = hourly_fixture()
        pre = weather_fixture(pd.date_range("2025-10-30", "2025-12-30").strftime("%Y-%m-%d"))
        january = weather_fixture(pd.date_range("2025-12-31", "2026-01-31").strftime("%Y-%m-%d"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "initial"
            summary = run_experiment(hourly, pre, january, output)
            self.assertTrue(summary["gate_passed"])
            manifest = output / "deployment/manifest.json"
            original = ForecastModelBundle.load(manifest)
            before = {path.relative_to(output): path.read_bytes()
                      for path in output.rglob("*") if path.is_file()}

            # Exact weather and bin-center labels give the raw baseline zero error,
            # so no candidate can pass the promotion gate on a new experiment.
            exact_hourly = hourly.copy()
            exact_hourly["wind_speed_ms"] = .25 + exact_hourly.time_naive.dt.hour / 2
            exact_hourly["power_normalized"] = exact_hourly.wind_speed_ms / 16 / exact_hourly.turbine_id
            exact_pre = pre.assign(wind_speed_100m_ms=pre.wind_speed_100m_ms * 4 + .25)
            exact_january = january.assign(wind_speed_100m_ms=january.wind_speed_100m_ms * 4 + .25)
            with self.assertRaisesRegex(ValueError, "--output-dir"):
                run_experiment(exact_hourly, exact_pre, exact_january, output)

            self.assertEqual({path.relative_to(output): path.read_bytes()
                              for path in output.rglob("*") if path.is_file()}, before)
            preserved = ForecastModelBundle.load(manifest)
            np.testing.assert_array_equal(preserved.predict(january), original.predict(january))
            self.assertEqual(preserved.metadata["decision_sha256"],
                             hashlib.sha256((output / "decision.json").read_bytes()).hexdigest())
            new_output = Path(directory) / "rerun"
            rerun = run_experiment(exact_hourly, exact_pre, exact_january, new_output)
            self.assertFalse(rerun["gate_passed"])
            self.assertFalse((new_output / "deployment").exists())

    def test_reexperiment_cannot_overwrite_an_active_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "active.json").write_text('{"version":"existing-reviewed-version"}')
            (output / "deployment").mkdir()
            manifest = output / "deployment/manifest.json"
            manifest.write_text("existing deployment must remain unchanged")
            pre = weather_fixture(pd.date_range("2025-10-30", "2025-12-30").strftime("%Y-%m-%d"))
            january = weather_fixture(pd.date_range("2025-12-31", "2026-01-31").strftime("%Y-%m-%d"))
            with self.assertRaisesRegex(ValueError, "active|Active"):
                run_experiment(hourly_fixture(), pre, january, output)
            self.assertEqual(manifest.read_text(), "existing deployment must remain unchanged")
            self.assertFalse((output / "decision.json").exists())

    def test_complete_experiment_keeps_january_boundary_and_freezes_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/weather").mkdir(parents=True)
            (root / "data/processed").mkdir(parents=True)
            hourly_fixture().to_csv(root / "data/processed/hourly.csv", index=False)
            dates = pd.date_range("2025-10-30", "2025-12-30").strftime("%Y-%m-%d")
            weather_fixture(dates).to_csv(root / "data/weather/noaa_nov_dec.csv", index=False)
            january = pd.date_range("2025-12-31", "2026-01-31").strftime("%Y-%m-%d")
            weather_fixture(january).to_csv(root / "data/weather/noaa_forecasts.csv", index=False)
            script = Path(__file__).resolve().parents[1] / "scripts/evaluate_gfs_ml.py"
            result = subprocess.run([sys.executable, str(script), "--root", str(root)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = root / "outputs/gfs_ml"
            decision = json.loads((output / "decision.json").read_text())
            self.assertTrue(decision["gate_passed"])
            self.assertFalse((output / "active.json").exists())
            metrics = pd.read_csv(output / "january_descriptive/metrics.csv")
            self.assertEqual(set(metrics.loc[metrics.horizon == "1-48", "n"]), {1464})
            december = pd.read_csv(output / "december/metrics.csv")
            self.assertEqual(set(december.loc[december.horizon == "1-48", "n"]), {1488})
            bundle = ForecastModelBundle.load(output / "deployment/manifest.json")
            self.assertEqual(bundle.metadata["id"], decision["selected_candidate"])
            self.assertEqual(bundle.metadata["labels_end_strictly_before_utc"], "2026-01-31T18:00:00+00:00")
            for turbine in bundle.metadata["training"].values():
                self.assertEqual(turbine["last_pair_label_end_utc"], "2026-01-31T17:00:00+00:00")
            january_bundle = ForecastModelBundle.load(output / "january_descriptive/raw_empirical/manifest.json")
            self.assertEqual(january_bundle.metadata["labels_end_strictly_before_utc"], "2025-12-31T18:00:00+00:00")

    def test_partial_archive_is_rejected_before_any_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/weather").mkdir(parents=True)
            (root / "data/processed").mkdir(parents=True)
            hourly_fixture().to_csv(root / "data/processed/hourly.csv", index=False)
            weather_fixture().to_csv(root / "data/weather/noaa_nov_dec.csv", index=False)
            weather_fixture(["2025-12-31"]).to_csv(root / "data/weather/noaa_forecasts.csv", index=False)
            script = Path(__file__).resolve().parents[1] / "scripts/evaluate_gfs_ml.py"
            result = subprocess.run([sys.executable, str(script), "--root", str(root)],
                                    capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("archive", result.stderr.lower())
            self.assertFalse((root / "outputs/gfs_ml/decision.json").exists())


if __name__ == "__main__":
    unittest.main()
