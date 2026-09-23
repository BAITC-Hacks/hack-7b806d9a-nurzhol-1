# WindOps: February replay and agent demo

The user has 3.5 hours left and accepted the working direction through the ongoing hackathon conversation: finish February forecasts, an agent, and a demonstrable local screen. Use the existing validated empirical curves, confirmed fixed UTC+5, and NOAA archive. No further ML tuning in this deliverable.

## Outcomes

1. Daily issues January 31 through February 28, 2026, at 23:00 UTC+5, each covering the next 48 hourly intervals for both turbines. Keep March spillover with an explicit February-target flag; also export one day-ahead February series with 672 hours per turbine.
2. A local dashboard at 127.0.0.1:8765 with date/turbine selection, power/weather chart, 24/48-hour comparison, weather provenance, agent tool log, explanation and CSV download.
3. An OpenAI Responses agent calls bounded application functions for obtaining weather, validating as-of provenance, predicting power and analyzing results. Application code enforces chronology and computes every numerical forecast. API failure or missing key gives a clearly labelled deterministic workflow, never a fake LLM trace.
4. Changed weather fingerprint invalidates cached predictions. User-triggered recalculation creates a new run and an auditable log. No automatic API calls on page load.
5. One local launch command and a concise demo/validation guide.

## Scientific constraints

Freeze the existing empirical curves trained before December 31 for the entire replay. Do not silently retrain on late-January measurements. Keep raw timestamps, units 0..1, no MW/MWh claims, no invented February actuals or confidence intervals. Weather run 12 UTC, issue 18 UTC, leads f007..054. Publication of GRIB and index must precede issue. Source CSV interval-start convention remains an assumption.

## Interfaces

`windops/forecast.py`: `ForecastService(root)` with `list_issues()`, `get_weather(issue_date, refresh=False)`, `predict(issue_date, weather)`, `get_forecast(issue_date, refresh=False)`, and `export_february(output_dir=None)`.

`get_weather`: JSON dict with issue_date, forecast_origin_utc, forecast_origin_local, run_time_utc, available_at_utc, source_fingerprint, source, rows (96 validated weather rows). Read `data/weather/noaa_february.csv` when present, or complete per-run cached points. Missing data produces actionable error, not partial forecast. Refresh can fetch one run with existing downloader. All methods reject dates outside Jan31..Feb28.

`predict` / `get_forecast`: JSON dict with issue_date, forecast_origin_utc/local, model (id, label, training_cutoff, version), source (provider, run_time_utc, available_at_utc, fingerprint), rows (turbine_id, valid_time_utc, valid_time_local, lead_hours, power_normalized, wind_speed_ms, temperature_c, is_february_target), summary (per-turbine numbers), cached. Exact source rows may be included internally but not sent wholesale to the LLM.

HTTP API: GET `/api/status` -> {ready, available_issues, default_issue_date, timezone:'UTC+5', openai_configured, model, baseline_metrics, data_status}; GET `/api/forecast?issue_date=YYYY-MM-DD` -> forecast dict; POST `/api/run` with {issue_date, mode:'deterministic'|'openai', refresh:boolean} -> {job_id}; GET `/api/jobs/{id}` -> {status:'running'|'complete'|'failed', events:[{time,tool,status,message}], result:forecast|null, explanation:string, error:string|null, mode}; GET `/api/download?issue_date=...` -> CSV; GET `/api/download-february` -> day-ahead CSV. No secrets exposed. The local server serves only explicit API routes and files under web/.

## Dashboard brief

Russian wind-farm operations desk: warm off-white paper, graphite typography, deep petrol forecast line and restrained orange weather accent. The signature is a single large 48-hour timeline with a day boundary and a clearly visible issue-time provenance strip. Avoid ornamental gradients, generic card grids, chat-first UI, or mock metrics. Keyboard-accessible controls, responsive layout, real loading/error states. All assets local; no CDN dependency.

## Validation

Time boundary local00=UTC19 previous day; 48h per turbine, 29 issues, 2784 rows; February day-ahead 1344 rows. Reject incomplete/late weather, invalid date, nonfinite values and changed fingerprints. API tests cover unavailable key, bad arguments, LLM tools in wrong order and upstream failure. UI smoke check date selection, run, status/error, CSV and small screen. Never represent mocked API tests as a live call.
