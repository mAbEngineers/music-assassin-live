"""XDG-style paths: models in data dir, crash-recovery state in state dir."""

import os
from pathlib import Path

APP = "music-assassin"


def data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / APP


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    d = base / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    env = os.environ.get("MUSIC_ASSASSIN_MODELS")
    if env:
        return Path(env)
    xdg = data_dir() / "models"
    if xdg.is_dir() and any(xdg.glob("*.onnx")):
        return xdg
    # dev fallback: repo-local models/ next to the package
    local = Path(__file__).resolve().parent.parent / "models"
    if local.is_dir() and any(local.glob("*.onnx")):
        return local
    xdg.mkdir(parents=True, exist_ok=True)
    return xdg


ROUTING_STATE = state_dir() / "routing.json"
