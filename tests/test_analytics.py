"""Analytics contract tests use small synthetic forecast and evaluation fixtures."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import tempfile
import unittest

from windops.analytics import compare_forecasts, forecast_events, january_validation


UTC = timezone.utc
LOCAL = timezone(timedelta(hours=5))


def forecast_row(hour, power, turbine=1, lead=None, wind=4.0):
    valid = datetime(2026, 2, 1, tzinfo=UTC) + timedelta(hours=hour)
    return {"turbine_id": turbine, "valid_time_utc": valid.isoformat(),
            "valid_time_local": valid.astimezone(LOCAL).isoformat(),
            "lead_hours": hour + 1 if lead is None else lead,
            "power_normalized": power, "wind_speed_ms": wind}


def forecast(issue_date, rows):
    return {"issue_date": issue_date, "model": {"id": "fixture"},
            "source": {"provider": "fixture archive"}, "rows": rows}


class ForecastFixture:
    def __init__(self, values):
        self.values = values

    def list_issues(self):
        return list(self.values)

    def get_forecast(self, issue_date):
        return self.values[issue_date]


class ForecastComparisonTests(unittest.TestCase):
    def test_matches_target_time_and_turbine_instead_of_position_or_lead(self):
        older = forecast("2026-01-31", [forecast_row(23, .9, lead=24),
            forecast_row(24, .2, lead=25, wind=3), forecast_row(25, .7, lead=26, wind=8),
            forecast_row(24, .6, turbine=2, lead=25, wind=6)])
        newer = forecast("2026-02-01", [forecast_row(26, .4, lead=3),
            forecast_row(24, .3, turbine=2, lead=1, wind=4),
            forecast_row(25, .4, lead=2, wind=5), forecast_row(24, .5, lead=1, wind=7)])
        service = ForecastFixture({older["issue_date"]: older, newer["issue_date"]: newer})
        result = compare_forecasts(service, "2026-02-01")
        self.assertEqual([(r["turbine_id"], r["new_lead_hours"]) for r in result["rows"]],
                         [(1, 1), (1, 2), (2, 1)])
        self.assertEqual([r["old_lead_hours"] for r in result["rows"]], [25, 26, 25])
        self.assertAlmostEqual(result["rows"][0]["delta_power"], .3)
        self.assertAlmostEqual(result["rows"][1]["delta_power"], -.3)
        self.assertEqual(result["rows"][0]["delta_wind"], 4)
        self.assertEqual(result["rows"][1]["delta_wind"], -3)
        summary = result["summary"][0]
        self.assertEqual(summary["count"], 2)
        self.assertAlmostEqual(summary["mean_delta_power"], 0)
        self.assertAlmostEqual(summary["max_abs_delta_power"], .3)
        self.assertEqual(result["newer"]["issue_date"], "2026-02-01")
        self.assertEqual(result["older"]["source"], older["source"])
        self.assertIn("не улучшение точности", result["caveat"])

    def test_honors_snapshot_and_equivalent_utc_timestamp_spelling(self):
        row = forecast_row(24, .2, lead=25)
        row["valid_time_utc"] = row["valid_time_utc"].replace("T", " ")
        older = forecast("2026-01-31", [row])
        newer = forecast("2026-02-01", [forecast_row(24, .6, lead=1)])
        before = deepcopy(newer)
        result = compare_forecasts(ForecastFixture({"2026-01-31": older}),
                                   "2026-02-01", forecast=newer)
        self.assertEqual(len(result["rows"]), 1)
        self.assertAlmostEqual(result["rows"][0]["delta_power"], .4)
        self.assertEqual(newer, before)

    def test_missing_prior_calendar_issue_does_not_use_nearest_available(self):
        service = ForecastFixture({"2026-01-31": forecast("2026-01-31", []),
                                   "2026-02-02": forecast("2026-02-02", [])})
        with self.assertRaisesRegex(ValueError, "предыдущ.*2026-02-01"):
            compare_forecasts(service, "2026-02-02")


class ForecastEventTests(unittest.TestCase):
    def test_drop_low_and_peak_have_real_times_and_zero_based_turbine_indices(self):
        values = [.4, .8, .3, .05, .02, .08, .2]
        snapshot = forecast("2026-01-31", [forecast_row(i, p, turbine=t)
                            for t in (1, 2) for i, p in enumerate(values)])
        before = deepcopy(snapshot)
        events = forecast_events(snapshot)
        self.assertEqual(len(events), 6)
        for turbine in (1, 2):
            grouped = {r["kind"]: r for r in events if r["turbine_id"] == turbine}
            self.assertEqual(set(grouped), {"drop", "low", "peak"})
            self.assertEqual((grouped["drop"]["start_hour"], grouped["drop"]["end_hour"]), (1, 2))
            self.assertAlmostEqual(grouped["drop"]["delta_power"], -.5)
            self.assertEqual((grouped["low"]["start_hour"], grouped["low"]["end_hour"]), (3, 5))
            self.assertEqual(grouped["low"]["duration_hours"], 3)
            self.assertEqual(grouped["low"]["threshold"], .1)
            self.assertEqual(grouped["peak"]["start_hour"], 1)
            for event in grouped.values():
                self.assertEqual(event["start_time_utc"], forecast_row(event["start_hour"], 0)["valid_time_utc"])
                self.assertNotIn("МВт", event["detail"])
        self.assertEqual(len({e["id"] for e in events}), 6)
        self.assertEqual(snapshot, before)
        self.assertEqual(events, forecast_events(snapshot))

    def test_gaps_break_low_runs_and_cannot_create_hourly_drop(self):
        rows = [forecast_row(0, .8), forecast_row(2, .01), forecast_row(3, .02),
                forecast_row(5, .03), forecast_row(6, .04)]
        kinds = {e["kind"] for e in forecast_events(forecast("2026-01-31", rows))}
        self.assertEqual(kinds, {"peak"})

    def test_low_threshold_is_strict_and_longest_tie_uses_earliest(self):
        values = [.05, .04, .03, .1, .01, .02, .03]
        events = forecast_events(forecast("2026-01-31", [forecast_row(i, p) for i, p in enumerate(values)]))
        low = next(e for e in events if e["kind"] == "low")
        self.assertEqual((low["start_hour"], low["end_hour"]), (0, 2))
        self.assertFalse(any(e["kind"] == "drop" for e in events))

    def test_exact_tenth_drop_is_included_despite_float_roundoff(self):
        events = forecast_events(forecast("2026-01-31", [forecast_row(0, .3), forecast_row(1, .2)]))
        self.assertIn("drop", [e["kind"] for e in events])


class JanuaryValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "outputs/baseline/predictions.csv"
        self.path.parent.mkdir(parents=True)

    def row(self, target="2025-12-31 19:00:00+00:00", lead=1, actual=.2, predicted=.3, **overrides):
        valid = datetime.fromisoformat(target)
        return {"turbine_id": "1", "forecast_origin_utc": (valid-timedelta(hours=lead)).isoformat(),
                "valid_time_utc": target, "lead_hours": lead, "model": "empirical_curve",
                "utc_offset_hours": "5", "score_eligible": "True", "target_hour_complete": "True",
                "actual_power_normalized": actual, "predicted_power_normalized": predicted, **overrides}

    def write(self, rows):
        with self.path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_filters_and_metrics_keep_origin_specific_cases_in_each_horizon(self):
        first = self.row(actual=.2, predicted=.3)
        second = self.row(target="2025-12-31 20:00:00+00:00", lead=2, actual=.6, predicted=.3)
        other_origin = self.row(lead=25, actual=.2, predicted=.7)
        self.write([first, second, other_origin, dict(first),
                    self.row(turbine_id="2", predicted=.9), self.row(model="catboost"),
                    self.row(utc_offset_hours="6"), self.row(score_eligible="False"),
                    self.row(target_hour_complete="False"), self.row(actual="nan"),
                    self.row(predicted="inf"), self.row(actual=""),
                    self.row(target="2025-12-31 18:00:00+00:00"),
                    self.row(target="2026-01-31 19:00:00+00:00")])
        first_day = january_validation(self.root)
        self.assertEqual(first_day["model"]["id"], "empirical_curve")
        self.assertEqual(first_day["days"], ["2026-01-01"])
        self.assertEqual(first_day["metrics"]["count"], 2)
        self.assertEqual(first_day["metrics"]["unique_hours"], 2)
        self.assertAlmostEqual(first_day["metrics"]["mae"], .2)
        self.assertAlmostEqual(first_day["metrics"]["rmse"], math.sqrt(.05))
        self.assertAlmostEqual(first_day["rows"][0]["error"], .1)
        self.assertAlmostEqual(first_day["rows"][1]["error"], -.3)
        self.assertEqual(first_day["rows"][0]["valid_time_local"], "2026-01-01T00:00:00+05:00")
        self.assertEqual([m["count"] for m in first_day["metrics_by_horizon"]], [2, 1])
        second_day = january_validation(self.root, horizon="25-48")
        self.assertEqual(second_day["metrics"]["count"], 1)
        self.assertEqual(second_day["rows"][0]["lead_hours"], 25)
        self.assertAlmostEqual(second_day["metrics"]["mae"], .5)
        self.assertIn("не независимый", first_day["caveat"])

    def test_all_month_days_are_returned_and_horizon_boundaries_are_inclusive(self):
        self.write([self.row(lead=24), self.row(lead=25),
                    self.row(target="2026-01-31 18:00:00+00:00", lead=48)])
        result = january_validation(self.root, horizon="25-48")
        self.assertEqual(result["days"], ["2026-01-01", "2026-01-31"])
        self.assertEqual([r["lead_hours"] for r in result["rows"]], [25, 48])
        self.assertEqual(january_validation(self.root)["metrics"]["count"], 1)

    def test_invalid_selection_and_empty_rows_have_explicit_results(self):
        self.write([self.row(score_eligible="False")])
        for horizon in ("1-48", "all", ""):
            with self.subTest(horizon=horizon), self.assertRaises(ValueError):
                january_validation(self.root, horizon=horizon)
        with self.assertRaises(ValueError):
            january_validation(self.root, turbine_id=3)
        result = january_validation(self.root)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["metrics"], {"count": 0, "unique_hours": 0, "mae": None, "rmse": None})

    def test_conflicting_duplicate_cases_are_rejected(self):
        self.write([self.row(), self.row(predicted=.9)])
        with self.assertRaisesRegex(ValueError, "[Дд]убликат"):
            january_validation(self.root)


if __name__ == "__main__":
    unittest.main()
