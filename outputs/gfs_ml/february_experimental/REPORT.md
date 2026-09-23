# February 2026 forecast replay

29 daily issues, January 31–February 28 at 23:00 UTC+5 (18:00 UTC). Each issue uses the same-day NOAA GFS 12 UTC run and f007–f054, with publication no later than the issue.

- Full 48-hour trajectories: **2,784 rows**, 48 hours per turbine per issue.
- February day-ahead series: **1,344 rows**, 672 unique hours per turbine.
- March spillover retained in full trajectories: **144 rows**.
- Model: Коррекция ветра GFS + эмпирическая кривая; training cutoff 2026-01-31T22:00:00+05:00. No retraining during replay.

`manifest.json` records model hash, issue fingerprints and export SHA-256 hashes.

- No February actuals are available; no February accuracy is claimed.
- Power is normalized to 0..1, not MW or MWh.
- Hourly source timestamps are assumed to label interval starts.
- Selected GFS model is frozen for February; January metrics describe an earlier training version.
