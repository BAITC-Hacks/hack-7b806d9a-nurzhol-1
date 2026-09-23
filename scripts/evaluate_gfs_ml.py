#!/usr/bin/env python3
"""Run the preregistered December selection and January descriptive GFS experiment."""
import argparse
import io
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from windops.ml import run_experiment, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--hourly", type=Path)
    parser.add_argument("--pre-weather", type=Path)
    parser.add_argument("--january-weather", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    sources = {
        "hourly": args.hourly or root / "data/processed/hourly.csv",
        "november_december_weather": args.pre_weather or root / "data/weather/noaa_nov_dec.csv",
        "january_weather": args.january_weather or root / "data/weather/noaa_forecasts.csv",
    }
    try:
        frames, hashes = {}, {}
        for name, path in sources.items():
            payload = path.read_bytes()
            frames[name] = pd.read_csv(io.BytesIO(payload), float_precision="round_trip")
            hashes[name] = {"file": path.name, "sha256": sha256(payload)}
        protocol = root / "docs/superpowers/plans/2026-09-23-gfs-ml-experiment.md"
        if protocol.exists():
            hashes["locked_protocol"] = {"file": protocol.name, "sha256": sha256(protocol.read_bytes())}
        result = run_experiment(frames["hourly"], frames["november_december_weather"], frames["january_weather"],
                                args.output_dir or root / "outputs/gfs_ml", source_hashes=hashes)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"GFS experiment failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
