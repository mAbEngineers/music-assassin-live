"""Processor registry — maps model-card names to StreamProcessor factories."""

from pathlib import Path

from .base import StreamProcessor
from .passthrough import Passthrough

_MODEL_FILES = {
    "gtcrn": "gtcrn_simple.onnx",
    "dpdfnet": "dpdfnet_baseline.onnx",
    "speechdenoiser": "speechdenoiser.onnx",
}


def model_file(name: str) -> str | None:
    """Filename for a processor's ONNX weights, or None for passthrough."""
    return _MODEL_FILES.get(name)


def available(models_dir: Path) -> list[str]:
    names = ["passthrough"]
    names += [n for n, f in _MODEL_FILES.items() if (models_dir / f).is_file()]
    return names


def create(name: str, models_dir: Path) -> StreamProcessor:
    if name == "passthrough":
        return Passthrough()
    if name not in _MODEL_FILES:
        raise ValueError(f"unknown processor {name!r}")
    path = models_dir / _MODEL_FILES[name]
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} missing — run scripts/import_models.py or download from releases"
        )
    if name == "gtcrn":
        from .gtcrn import GtcrnProcessor
        return GtcrnProcessor(str(path))
    if name == "dpdfnet":
        from .dpdfnet import DpdfnetProcessor
        return DpdfnetProcessor(str(path))
    from .speechdenoiser import SpeechDenoiserProcessor
    return SpeechDenoiserProcessor(str(path))
