"""Behavioral checks for conservative ten-minute to hourly aggregation."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

from scripts.prepare_hourly import aggregate_frame as aggregate


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_hourly.py"


def six_readings(hour="2025-12-31 23:00:00"):
    return pd.DataFrame({
        "time": pd.date_range(hour, periods=6, freq="10min").strftime("%Y-%m-%d %H:%M:%S"),
        "wind": [1, 2, 3, 4, 5, 6],
        "power": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        "temp": [-3, -2, -1, 0, 1, 2],
    })


class HourlyAggregationTests(unittest.TestCase):
    def test_averages_power_instead_of_summing_it(self):
        result = aggregate(six_readings(), 1, start="2025-12-31 23:00", end="2025-12-31 23:00")
        row = result.iloc[0]
        self.assertAlmostEqual(row.power_normalized, 0.35)
        self.assertAlmostEqual(row.wind_speed_ms, 3.5)
        self.assertAlmostEqual(row.temperature_c, -0.5)
        self.assertEqual(row.observations, 6)
        self.assertTrue(row.is_complete)

    def test_partial_and_missing_hours_remain_visible_without_imputation(self):
        result = aggregate(six_readings().iloc[:5], 2, start="2025-12-31 23:00", end="2026-01-01 00:00")
        self.assertEqual(result.observations.tolist(), [5, 0])
        self.assertEqual(result.is_complete.tolist(), [False, False])
        self.assertTrue(result[["wind_speed_ms", "power_normalized", "temperature_c"]].isna().all().all())
        self.assertEqual(result.turbine_id.tolist(), [2, 2])

    def test_hour_boundary_and_validation_boundary_do_not_leak(self):
        frame = pd.concat([six_readings(), six_readings("2026-01-01 00:00:00")], ignore_index=True)
        frame.loc[6:, "power"] = 0.9
        result = aggregate(frame, 1, start="2025-12-31 23:00", end="2026-01-01 00:00")
        self.assertEqual(result.split.tolist(), ["train", "validation"])
        self.assertEqual(result.observations.tolist(), [6, 6])
        self.assertAlmostEqual(result.iloc[0].power_normalized, 0.35)
        self.assertAlmostEqual(result.iloc[1].power_normalized, 0.9)
        self.assertIsNone(result.time_naive.dt.tz)

    def test_duplicate_timestamp_is_rejected_even_if_values_match(self):
        frame = six_readings()
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "[Dd]uplicate"):
            aggregate(frame, 1, start="2025-12-31 23:00", end="2025-12-31 23:00")

    def test_out_of_order_readings_produce_same_means_without_mutating_input(self):
        frame = six_readings().iloc[::-1].copy()
        original = frame.copy(deep=True)
        result = aggregate(frame, 1, start="2025-12-31 23:00", end="2025-12-31 23:00")
        self.assertTrue(result.iloc[0].is_complete)
        self.assertAlmostEqual(result.iloc[0].power_normalized, 0.35)
        pd.testing.assert_frame_equal(frame, original)

    def test_six_readings_off_ten_minute_grid_are_incomplete(self):
        for replacement in ["2025-12-31 23:51:00", "2025-12-31 23:50:01"]:
            with self.subTest(replacement=replacement):
                frame = six_readings()
                frame.loc[5, "time"] = replacement
                result = aggregate(frame, 1, start="2025-12-31 23:00", end="2025-12-31 23:00")
                self.assertEqual(result.iloc[0].observations, 6)
                self.assertFalse(result.iloc[0].is_complete)
                self.assertTrue(pd.isna(result.iloc[0].power_normalized))

    def test_nonfinite_measurement_invalidates_all_hourly_averages(self):
        for column, value in [("wind", np.nan), ("power", np.inf), ("temp", -np.inf)]:
            with self.subTest(column=column):
                frame = six_readings()
                frame[column] = frame[column].astype(float)
                frame.loc[2, column] = value
                result = aggregate(frame, 1, start="2025-12-31 23:00", end="2025-12-31 23:00")
                self.assertEqual(result.iloc[0].observations, 6)
                self.assertFalse(result.iloc[0].is_complete)
                self.assertTrue(result[["wind_speed_ms", "power_normalized", "temperature_c"]].isna().all().all())


class HourlyCommandTests(unittest.TestCase):
    def test_command_writes_both_turbine_grids_and_summary_without_changing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = pd.concat([
                six_readings("2023-03-11 00:00:00"),
                six_readings("2026-01-31 23:00:00"),
            ], ignore_index=True)
            raw.insert(0, "ID", range(1, 13))
            raw.columns = [
                "ID", "Статистическое время", "Средняя скорость ветра(m/s)",
                "Нормализованная активная мощность", "Средняя температура окружающей среды(°C)",
            ]
            first, second = root / "first.csv", root / "second.csv"
            raw.to_csv(first, index=False)
            raw.to_csv(second, index=False)
            original = first.read_bytes()
            output, summary = root / "processed/hourly.csv", root / "processed/summary.json"
            command = [sys.executable, str(SCRIPT), "--turbine1", str(first), "--turbine2", str(second),
                       "--output", str(output), "--summary", str(summary)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            hourly = pd.read_csv(output)
            self.assertEqual(list(hourly.columns), ["time_naive", "turbine_id", "wind_speed_ms", "power_normalized",
                                                    "temperature_c", "observations", "is_complete", "split"])
            self.assertEqual(len(hourly), 50784)
            self.assertEqual(hourly.groupby("turbine_id").size().to_dict(), {1: 25392, 2: 25392})
            self.assertEqual(hourly.groupby("split").size().to_dict(), {"train": 49296, "validation": 1488})
            self.assertEqual(hourly.is_complete.sum(), 4)
            self.assertEqual(hourly.time_naive.min(), "2023-03-11 00:00:00")
            self.assertEqual(hourly.time_naive.max(), "2026-01-31 23:00:00")
            report = json.loads(summary.read_text())
            self.assertEqual(report["output_rows"], 50784)
            self.assertEqual(report["complete_hours"], 4)
            self.assertEqual(first.read_bytes(), original)
            self.assertEqual(second.read_bytes(), original)

            # A mistaken destination must not overwrite either raw file.
            unsafe = command.copy()
            unsafe[unsafe.index("--output") + 1] = str(first)
            rejected = subprocess.run(unsafe, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(first.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
