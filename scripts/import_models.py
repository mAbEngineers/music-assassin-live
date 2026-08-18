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

FILES = ["gtcrn_simple.onnx", "dpdfnet_baseline.onnx", "dpdfnet2_48khz_hr.onnx",
        "dtln_model_1.onnx", "dtln_model_2.onnx", "speechdenoiser.onnx"]

# Copied under a different name than upstream ships. Spleeter's two files are
# called vocals.int8.onnx / accompaniment.int8.onnx, which say nothing about
# which model they belong to once they are sitting in a flat models dir
# alongside six others -- and the registry looks them up by name.
RENAMED = {
    "vocals.int8.onnx": "spleeter_vocals.int8.onnx",
    "accompaniment.int8.onnx": "spleeter_accompaniment.int8.onnx",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path,
                    default=Path(__file__).resolve().parents[2].parent / "Music-Assassin" / "models",
                    help="directory containing the .onnx files (searched recursively — "
                         "the research repo nests them under sherpa_onnx/, speechdenoiser/, etc.)")
    args = ap.parse_args()

    target = data_dir() / "models"
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    wanted = [(f, f) for f in FILES] + list(RENAMED.items())
    for src_name, dst_name in wanted:
        matches = sorted(args.source.rglob(src_name))
        if matches:
            shutil.copy2(matches[0], target / dst_name)
            print(f"  {dst_name}  ->  {target}  "
                  f"(from {matches[0].relative_to(args.source)})")
            copied += 1
        else:
            print(f"  {dst_name}  MISSING under {args.source}")
    print(f"{copied}/{len(wanted)} models installed to {target}")


if __name__ == "__main__":
    main()
