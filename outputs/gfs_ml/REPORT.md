# GFS → power: locked experiment

Selection used only December. January was evaluated after the decision and did not alter it.

Promotion gate passed: **True**.
Selected algorithm: **affine_wind**.

## December

| Candidate | Turbine | Horizon | Cases | RMSE | MAE | Bias |
|---|---:|---|---:|---:|---:|---:|
| affine_wind | 1 | 1-24 | 740 | 0.265195 | 0.193799 | 0.018106 |
| affine_wind | 1 | 25-48 | 740 | 0.281675 | 0.206316 | 0.003999 |
| affine_wind | 1 | 1-48 | 1480 | 0.273559 | 0.200057 | 0.011052 |
| affine_wind | 2 | 1-24 | 734 | 0.270512 | 0.196507 | 0.020948 |
| affine_wind | 2 | 25-48 | 734 | 0.286850 | 0.208781 | 0.006898 |
| affine_wind | 2 | 1-48 | 1468 | 0.278801 | 0.202644 | 0.013923 |
| catboost_gfs | 1 | 1-24 | 740 | 0.290866 | 0.221239 | 0.068597 |
| catboost_gfs | 1 | 25-48 | 740 | 0.300679 | 0.233960 | 0.053903 |
| catboost_gfs | 1 | 1-48 | 1480 | 0.295813 | 0.227600 | 0.061250 |
| catboost_gfs | 2 | 1-24 | 734 | 0.295222 | 0.224444 | 0.071156 |
| catboost_gfs | 2 | 25-48 | 734 | 0.305177 | 0.236682 | 0.056238 |
| catboost_gfs | 2 | 1-48 | 1468 | 0.300241 | 0.230563 | 0.063697 |
| raw_empirical | 1 | 1-24 | 740 | 0.294042 | 0.207938 | -0.129049 |
| raw_empirical | 1 | 25-48 | 740 | 0.316333 | 0.228807 | -0.143429 |
| raw_empirical | 1 | 1-48 | 1480 | 0.305391 | 0.218372 | -0.136239 |
| raw_empirical | 2 | 1-24 | 734 | 0.303066 | 0.212765 | -0.133349 |
| raw_empirical | 2 | 25-48 | 734 | 0.323527 | 0.232815 | -0.147079 |
| raw_empirical | 2 | 1-48 | 1468 | 0.313463 | 0.222790 | -0.140214 |

The exact gate checks are recorded in decision.json. Saved bundles verify artifact hashes on load.
Deployment, when present, is frozen before January 31 18 UTC and requires separate explicit activation.

## Limitations

- December is a model-selection period, not an independent test.
- January already informed earlier model comparison; it is descriptive only.
- November–January training does not establish year-round generalization.
- Repeated forecast cases for one target hour are correlated.
- No February actual power is available; February accuracy cannot be measured.
- Fixed UTC+5; source interval-start convention remains an assumption.
