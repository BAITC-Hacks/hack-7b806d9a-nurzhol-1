"""Behavioral tests for chronology, matching, and baseline error calculations."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

from scripts.evaluate_baseline import (
    EmpiricalPowerCurve,
    evaluate,
    fit_models,
    metric_table,
    validate_weather,
    write_outputs,
)


def hourly_fixture():
    records = []
    for turbine, scale in (("T1", 1.0), ("T2", 0.5)):
        for time, wind, power in [
            ("2025-12-29 00:00", 0.25, 0.0),
            ("2025-12-29 01:00", 1.25, 0.8),
            ("2025-12-31 12:00", 1.25, 1.0),
            ("2026-01-01 00:00", 9.0, 0.2),
            ("2026-01-01 05:00", 9.0, 0.5),
            ("2026-01-01 06:00", 9.0, 0.6),
        ]:
            records.append(dict(time_naive=time, turbine_id=turbine,
                                wind_speed_ms=wind, power_normalized=power * scale,
                                temperature_c=5.0, observations=6,
                                is_complete=True,
                                split="train" if time < "2026" else "validation"))
    return pd.DataFrame(records)


def weather_fixture(origin="2025-12-31 18:00:00+00:00", leads=(6,)):
    origin = pd.Timestamp(origin)
    records = []
    for turbine in ("T1", "T2"):
        for lead in leads:
            records.append(dict(turbine_id=turbine, forecast_origin_utc=origin,
                                run_time_utc=origin - pd.Timedelta(hours=6),
                                valid_time_utc=origin + pd.Timedelta(hours=lead),
                                lead_hours=lead, wind_speed_100m_ms=0.75,
                                temperature_2m_c=5.0, wind_direction_100m_deg=180.0,
                                available_at_utc=origin))
    return pd.DataFrame(records)


class CurveTests(unittest.TestCase):
    def test_empty_bins_interpolate_between_training_bin_means(self):
        # Using nearest bins or medians changes the hand-derived 0.45 midpoint.
        curve = EmpiricalPowerCurve().fit([0.1, 0.2, 0.3, 1.25], [0.0, 0.0, 0.3, 0.8])
        np.testing.assert_allclose(curve.predict([0.25, 0.75, 1.25]), [0.1, 0.45, 0.8])

    def test_extrapolation_is_bounded_and_flagged(self):
        curve = EmpiricalPowerCurve().fit([0.25, 1.25], [0.0, 0.8])
        np.testing.assert_allclose(curve.predict([-1.0, 100.0]), [0.0, 0.8])
        self.assertEqual(curve.outside_training_range([0.1, 0.75, 2.0]).tolist(), [True, False, True])

    def test_incomplete_and_late_december_rows_cannot_train_models(self):
        hourly = hourly_fixture()
        extra = hourly.iloc[[0]].copy()
        extra["time_naive"] = "2025-12-28 00:00"
        extra["is_complete"] = False
        extra["power_normalized"] = 1.0
        hourly = pd.concat([hourly, extra], ignore_index=True)
        models, summary = fit_models(hourly, include_catboost=False)
        self.assertAlmostEqual(models["T1"]["training_mean"].predict([0.75], [5.0])[0], 0.4)
        self.assertEqual(summary["T1"]["training_rows"], 2)
        self.assertEqual(summary["T2"]["training_rows"], 2)
        self.assertAlmostEqual(models["T2"]["empirical_curve"].predict([0.75], [5.0])[0], 0.2)


class ChronologyTests(unittest.TestCase):
    def test_default_utc5_matches_local_midnight_to_previous_day_19utc(self):
        predictions, metrics, metadata, _ = evaluate(hourly_fixture(), weather_fixture(leads=(1,)), include_catboost=False)
        self.assertEqual(predictions.utc_offset_hours.unique().tolist(), [5])
        self.assertEqual(metrics.utc_offset_hours.unique().tolist(), [5])
        rows = predictions.query("turbine_id == 'T1'")
        self.assertTrue(rows.valid_time_utc.eq(pd.Timestamp("2025-12-31 19:00:00+00:00")).all())
        self.assertTrue(rows.valid_time_naive.eq(pd.Timestamp("2026-01-01 00:00:00")).all())
        np.testing.assert_allclose(rows.actual_power_normalized, 0.2)
        self.assertTrue(metadata["timezone_confirmed"])
        self.assertEqual(metadata["source_utc_offset_hours"], 5)
        self.assertEqual(metadata["timezone_confirmation_source"], "user")
        self.assertEqual(metadata["sensitivity_utc_offsets"], [])

    def test_future_weather_and_inconsistent_horizons_are_rejected(self):
        cases = [
            ("available_at_utc", "2025-12-31 18:00:01+00:00"),
            ("lead_hours", 7),
            ("run_time_utc", "2025-12-31 19:00:00+00:00"),
            ("lead_hours", 0),
        ]
        for column, value in cases:
            with self.subTest(column=column, value=value):
                weather = weather_fixture()
                weather[column] = weather[column].astype(object)
                weather.loc[0, column] = value
                with self.assertRaises(ValueError):
                    validate_weather(weather)

    def test_duplicate_forecast_cases_are_rejected(self):
        weather = weather_fixture()
        with self.assertRaises(ValueError):
            validate_weather(pd.concat([weather, weather.iloc[[0]]], ignore_index=True))

    def test_timezone_hypotheses_join_distinct_local_targets(self):
        predictions, metrics, metadata, _ = evaluate(hourly_fixture(), weather_fixture(), utc_offsets=(0, 5, 6), include_catboost=False)
        rows = predictions.query("turbine_id == 'T1' and model == 'empirical_curve'").set_index("utc_offset_hours")
        self.assertAlmostEqual(rows.loc[0, "actual_power_normalized"], 0.2)
        self.assertAlmostEqual(rows.loc[5, "actual_power_normalized"], 0.5)
        self.assertAlmostEqual(rows.loc[6, "actual_power_normalized"], 0.6)
        np.testing.assert_allclose(rows["predicted_power_normalized"], [0.4, 0.4, 0.4])
        self.assertTrue(metadata["timezone_confirmed"])
        self.assertEqual(metadata["source_utc_offset_hours"], 5)
        self.assertEqual(metadata["sensitivity_utc_offsets"], [0, 6])
        self.assertEqual(rows.timezone_is_confirmed.tolist(), [False, True, False])
        self.assertTrue((metrics.query("horizon == '1-24'")["n"] == 1).all())

    def test_january_observed_wind_cannot_affect_predictions(self):
        hourly = hourly_fixture()
        before = evaluate(hourly, weather_fixture(), include_catboost=False)[0]
        january = hourly["time_naive"].str.startswith("2026")
        hourly.loc[january, ["wind_speed_ms", "temperature_c"]] = [-999.0, 999.0]
        after = evaluate(hourly, weather_fixture(), include_catboost=False)[0]
        np.testing.assert_allclose(before["predicted_power_normalized"], after["predicted_power_normalized"])

    def test_incomplete_targets_and_outside_january_do_not_enter_scores(self):
        hourly = hourly_fixture()
        hourly.loc[(hourly.turbine_id == "T1") & (hourly.time_naive == "2026-01-01 05:00"), "is_complete"] = False
        weather = pd.concat([weather_fixture(), weather_fixture("2026-01-31 18:00:00+00:00", (6,))])
        predictions, metrics, _, _ = evaluate(hourly, weather, include_catboost=False)
        incomplete = predictions.query("turbine_id == 'T1' and utc_offset_hours == 5 and is_validation_window")
        self.assertTrue(incomplete.actual_power_normalized.isna().all())
        self.assertTrue((metrics.query("turbine_id == 'T1' and utc_offset_hours == 5")["n"] == 0).all())
        self.assertTrue((~predictions.query("is_demo").is_validation_window).all())


class MetricsAndArtifactsTests(unittest.TestCase):
    def test_cli_defaults_to_user_confirmed_utc5(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            hourly_fixture().to_csv(directory / "hourly.csv", index=False)
            weather_fixture(leads=(1,)).to_csv(directory / "weather.csv", index=False)
            completed = subprocess.run([
                sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/evaluate_baseline.py"),
                "--hourly", str(directory / "hourly.csv"), "--weather", str(directory / "weather.csv"),
                "--output-dir", str(directory / "results"), "--no-catboost", "--no-plots",
            ], check=True, capture_output=True, text=True)
            status = json.loads(completed.stdout)
            self.assertTrue(status["timezone_confirmed"])
            self.assertEqual(status["source_utc_offset_hours"], 5)
            predictions = pd.read_csv(directory / "results/predictions.csv")
            self.assertEqual(predictions.utc_offset_hours.unique().tolist(), [5])

    def test_demo_plot_uses_available_48_hour_forecast_without_january_targets(self):
        # Demo-only data must produce a useful figure, not an empty January chart.
        result = evaluate(hourly_fixture(), weather_fixture("2026-01-31 18:00:00+00:00", range(1, 49)), include_catboost=False)
        with tempfile.TemporaryDirectory() as directory:
            write_outputs(*result, output_dir=directory, make_plots=True)
            self.assertTrue((Path(directory) / "forecast_demo_48h.png").is_file())
            self.assertFalse((Path(directory) / "metrics.png").exists())

    @unittest.skipUnless(importlib.util.find_spec("catboost"), "CatBoost is not installed")
    def test_saved_catboost_models_reproduce_forecast_predictions(self):
        # Removing model serialization or changing the feature order breaks reruns.
        from catboost import CatBoostRegressor
        result = evaluate(hourly_fixture(), weather_fixture(), include_catboost=True)
        with tempfile.TemporaryDirectory() as directory:
            write_outputs(*result, output_dir=directory, make_plots=False)
            saved = CatBoostRegressor()
            saved.load_model(str(Path(directory) / "catboost_T1.cbm"))
            prediction = np.clip(saved.predict([[0.75, 5.0]])[0], 0, 1)
            original = result[0].query("turbine_id == 'T1' and model == 'catboost'")
            np.testing.assert_allclose(original.predicted_power_normalized, prediction)

    def test_incomplete_weather_coverage_is_reported_without_hiding_missing_horizons(self):
        # A seemingly complete metric from just one downloaded lead must expose coverage.
        _, _, metadata, _ = evaluate(hourly_fixture(), weather_fixture(), include_catboost=False)
        coverage = metadata["forecast_coverage"]
        self.assertEqual(coverage["expected_cases_per_turbine"], 32 * 48)
        self.assertEqual(coverage["turbines"]["T1"]["received_cases"], 1)
        self.assertEqual(coverage["turbines"]["T1"]["missing_cases"], 1535)
        self.assertFalse(coverage["complete"])

    def test_errors_are_hand_calculated_with_horizon_24_in_first_day(self):
        frame = pd.DataFrame({
            "turbine_id": ["T1"] * 3, "utc_offset_hours": [0] * 3,
            "model": ["empirical_curve"] * 3, "lead_hours": [1, 24, 25],
            "actual_power_normalized": [0.0, 1.0, 0.5],
            "predicted_power_normalized": [0.2, 0.6, 0.6],
            "score_eligible": [True] * 3,
        })
        metrics = metric_table(frame).set_index("horizon")
        self.assertEqual(metrics.loc["1-24", "n"], 2)
        self.assertAlmostEqual(metrics.loc["1-24", "mae"], 0.3)
        self.assertAlmostEqual(metrics.loc["1-24", "rmse"], np.sqrt(0.1))
        self.assertAlmostEqual(metrics.loc["1-24", "bias"], -0.1)
        self.assertEqual(metrics.loc["25-48", "n"], 1)

    def test_demo_has_48_hours_and_files_keep_missing_metrics_as_json_null(self):
        weather = pd.concat([weather_fixture(), weather_fixture("2026-01-31 18:00:00+00:00", range(1, 49))])
        result = evaluate(hourly_fixture(), weather, utc_offsets=(0, 5, 6), include_catboost=True)
        with tempfile.TemporaryDirectory() as directory:
            write_outputs(*result, output_dir=directory, make_plots=False)
            demo = pd.read_csv(Path(directory) / "forecast_demo_wide.csv")
            self.assertEqual(len(demo), 48 * 3 * 3)
            self.assertIn("T1", demo.columns)
            self.assertIn("T2", demo.columns)
            compact = pd.read_csv(Path(directory) / "forecast_48h_utc.csv")
            self.assertEqual(len(compact), 48)
            self.assertEqual(compact.lead_hours.tolist(), list(range(1, 49)))
            self.assertEqual(set(compact.columns), {
                "forecast_origin_utc", "valid_time_utc", "lead_hours",
                "power_T1_catboost", "power_T1_empirical_curve", "power_T1_training_mean",
                "power_T2_catboost", "power_T2_empirical_curve", "power_T2_training_mean",
            })
            self.assertNotIn("valid_time_naive", compact.columns)
            self.assertNotIn("utc_offset_hours", compact.columns)
            self.assertNotIn("timezone_hypothesis", compact.columns)
            self.assertEqual(pd.to_datetime(compact.valid_time_utc, utc=True).iloc[0], pd.Timestamp("2026-01-31 19:00:00+00:00"))
            self.assertEqual(pd.to_datetime(compact.valid_time_utc, utc=True).iloc[-1], pd.Timestamp("2026-02-02 18:00:00+00:00"))
            local = pd.read_csv(Path(directory) / "forecast_48h_local.csv")
            self.assertEqual(len(local), 48)
            self.assertEqual(local.forecast_origin_local.iloc[0], "2026-01-31 23:00:00+05:00")
            self.assertEqual(local.valid_time_local.iloc[0], "2026-02-01 00:00:00+05:00")
            self.assertEqual(local.valid_time_local.iloc[-1], "2026-02-02 23:00:00+05:00")
            np.testing.assert_array_equal(pd.to_datetime(local.valid_time_local, utc=True),
                                          pd.to_datetime(compact.valid_time_utc, utc=True))
            for (turbine, model, offset), rows in result[0].loc[result[0].is_demo].groupby(["turbine_id", "model", "utc_offset_hours"]):
                np.testing.assert_allclose(compact[f"power_{turbine}_{model}"],
                                           rows.sort_values("lead_hours").predicted_power_normalized)
            payload = json.loads((Path(directory) / "metrics.json").read_text())
            self.assertIsNone(next(row for row in payload["metrics"] if row["n"] == 0)["rmse"])
            self.assertTrue((Path(directory) / "predictions.csv").exists())


if __name__ == "__main__":
    unittest.main()
