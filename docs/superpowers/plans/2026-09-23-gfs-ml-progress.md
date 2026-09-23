# GFS experiment execution record

Locked protocol: 2026-09-23-gfs-ml-experiment.md. The protocol file was not changed after real results.

- Completed: 62 NOAA issues downloaded; 5,952 rows, 2,976 decoded GRIB hours independently matched against points/listings.
- Completed: three candidates fitted; December cases1,480/1,468 and January1,464/1,464 independently checked.
- Completed: November-trained affine correction passed all December gates; CatBoost did not.
- Completed: January descriptive test found affine regression9.01%/7.53%; independent least-squares/interpolation reproduction confirmed this is not an implementation error.
- Ruling: withhold default activation despite December selection success. Preserve the December-selected algorithm and hyperparameters; record this operational deviation explicitly in outputs/gfs_ml/release_decision.json. An observed regression is not a reason to silently claim an improvement.
- Completed: frozen refit candidate exported separately to outputs/gfs_ml/february_experimental; working exports and default empirical curve preserved.
- Completed: explicit model-preview CLI, activation/version checks, stale export protections, UI metadata refresh; independent review findings fixed.
- Verification:81 tests passed, JavaScript syntax passed, real HTTP status and1,344-row download verified. Browser smoke tested separately.

No OpenAI or NVIDIA calls were made for model training. No source CSV was edited.
