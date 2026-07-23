"""Processor registry — maps model-card names to StreamProcessor factories."""

from pathlib import Path

from .base import StreamProcessor
from .passthrough import Passthrough

_MODEL_FILES = {
    "gtcrn": "gtcrn_simple.onnx",
    "dpdfnet": "dpdfnet_baseline.onnx",
    "dpdfnet_hr": "dpdfnet2_48khz_hr.onnx",
    "dtln": ("dtln_model_1.onnx", "dtln_model_2.onnx"),
    "speechdenoiser": "speechdenoiser.onnx",
}


def _files_for(name: str) -> tuple[str, ...]:
    f = _MODEL_FILES.get(name)
    if f is None:
        return ()
    return f if isinstance(f, tuple) else (f,)


def model_file(name: str) -> str | None:
    """Filename for a processor's ONNX weights (first file, for
    multi-file processors), or None for passthrough."""
    files = _files_for(name)
    return files[0] if files else None


def available(models_dir: Path) -> list[str]:
    names = ["passthrough"]
    names += [n for n in _MODEL_FILES
              if all((models_dir / f).is_file() for f in _files_for(n))]
    return names


def create(name: str, models_dir: Path) -> StreamProcessor:
    if name == "passthrough":
        return Passthrough()
    files = _files_for(name)
    if not files:
        raise ValueError(f"unknown processor {name!r}")
    paths = [models_dir / f for f in files]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            f"{missing[0]} missing — run scripts/import_models.py or download from releases"
        )
    if name == "gtcrn":
        from .gtcrn import GtcrnProcessor
        return GtcrnProcessor(str(paths[0]))
    if name == "dpdfnet":
        from .dpdfnet import DpdfnetProcessor
        return DpdfnetProcessor(str(paths[0]))
    if name == "dpdfnet_hr":
        from .dpdfnet import Dpdfnet48kProcessor
        return Dpdfnet48kProcessor(str(paths[0]))
    if name == "dtln":
        from .dtln import DtlnProcessor
        return DtlnProcessor(str(paths[0]), str(paths[1]))
    from .speechdenoiser import SpeechDenoiserProcessor
    return SpeechDenoiserProcessor(str(paths[0]))
