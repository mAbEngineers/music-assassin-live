#!/usr/bin/env python3
"""Copy ONNX models from the research repo (or any dir) into the app's
model directory. Until models are published as release assets this is the
hand-off path from Music-Assassin/models/."""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.paths import data_dir  # noqa: E402

FILES = ["gtcrn_simple.onnx", "dpdfnet_baseline.onnx", "speechdenoiser.onnx"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path,
                    default=Path(__file__).resolve().parents[2].parent / "Music-Assassin" / "models",
                    help="directory containing the .onnx files")
    args = ap.parse_args()

    target = data_dir() / "models"
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in FILES:
        src = args.source / f
        if src.is_file():
            shutil.copy2(src, target / f)
            print(f"  {f}  ->  {target}")
            copied += 1
        else:
            print(f"  {f}  MISSING in {args.source}")
    print(f"{copied}/{len(FILES)} models installed to {target}")


if __name__ == "__main__":
    main()
