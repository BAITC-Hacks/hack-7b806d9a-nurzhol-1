# GFS → power experiment

User approved the four-step experiment: download November–December forecasts,
compare wind correction and direct CatBoost, choose on December, then integrate
and replay February only if improvement is confirmed. No presentation work.

## Locked protocol (before December results)

- Download GFS 12 UTC issues October 30–December 30, 2025 (62 dates), f007–f054.
- Use fixed UTC+5, interval-start labels. A target hour is fully observed only
  when its ending time precedes the forecast issue.
- Training pairs: target local times November 1 inclusive–November 29 exclusive.
  Train the empirical curve on all complete station hours before November 29.
- December selection: targets December 1 inclusive–January 1 exclusive. Training
  labels end before the earliest included issue; duplicated target hours in
  different forecast issues cannot cross the split. No random train/test split.
- Candidates, fixed before evaluation: raw empirical curve; affine correction
  of GFS speed fitted against station wind, then the same empirical curve;
  CatBoost directly predicting power from GFS speed, temperature, wind direction
  sin/cos, horizon, and local hour sin/cos. Separate turbine models, 400 trees,
  depth 4, learning rate 0.03, L2 10, RMSE, seed 42; no hyperparameter search.
- Compare identical complete cases, by turbine and 1–24 / 25–48 / 1–48 hours.
- Promotion gate: each turbine's December total RMSE improves at least 5%,
  neither horizon's RMSE worsens for either turbine, and December first-half
  (Dec1–15) and second-half (Dec16–31) pooled RMSE both improve. Choose lowest
  pooled December RMSE among passing candidates.
  This is a model-selection result, not an independent generalization guarantee.
- January: descriptive check only, using models frozen before December 31;
  do not use January to select the candidate or alter parameters.
  Use only existing noaa_forecasts.csv (issues Dec31 onward), preserving the
  baseline's 1,464 cases per turbine. Do not add Dec30 issues: those precede
  the conservative Dec31 00:00 local training cutoff. Assert label-end times
  precede the actual earliest included January issue.
- February deployment: if gate passes, refit the selected algorithm on all
  available November–January pairs whose target hour ends before the first
  February issue (Jan31 18 UTC), and station curve labels under that same bound.
  Model frozen for all February issues; never train on February actuals.
- Preserve baseline artifacts and exports. New artifacts under outputs/gfs_ml;
  promotion uses an explicit manifest, saved model files and content hashes.

## Work and ownership

- [ ] Root: download archive, audit coverage/provenance, independent verification.
- [ ] ML agent: windops/ml.py, scripts/evaluate_gfs_ml.py,
  tests/test_gfs_ml.py. Test chronology, feature isolation and serialization.
- [ ] Root: integration only after passing December gate, cache/version handling,
  dynamic UI labels/metrics, February export and documentation.
- [ ] Independent review and full tests; record measured outcome and limitations.

## Execution decisions

This folder has no Git repository. Work in the existing authorized folder, retain
original models and CSVs. The download uses existing resumable caches and no API
credits. Parallel bounded implementation/review is used to save hackathon time.
