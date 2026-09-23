#!/usr/bin/env python3
"""Export all 29 frozen February forecast issues and the February day-ahead series."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from windops.forecast import ForecastService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--model-manifest", type=Path, help="Preview an explicitly chosen saved model without activating it")
    args = parser.parse_args()
    if args.model_manifest is not None and (args.output_dir is None or
            args.output_dir.resolve() == (args.root / "outputs/february").resolve()):
        parser.error("--model-manifest requires a separate --output-dir to preserve the working February export")
    try:
        paths = ForecastService(args.root, model_manifest=args.model_manifest).export_february(args.output_dir)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"February replay failed: {exc}\n")
    print(json.dumps(paths, indent=2))


if __name__ == "__main__":
    main()
