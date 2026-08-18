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
    "spleeter": ("spleeter_vocals.int8.onnx",
                 "spleeter_accompaniment.int8.onnx"),
}

# Spleeter is chunk-parameterised: `spleeter_1000ms` is the same two ONNX
# files run with a 1 s block. The chunk is the dominant latency term and the
# whole point of sweeping it (ROADMAP A1), so it belongs in the config name
# rather than in a constructor nobody can reach from --sweep.
#
# create() accepts ANY spleeter_<n>ms; this tuple is only what available()
# advertises, and bench_quality.py rejects a swept model that is not
# advertised. Sweeping some other chunk means adding it here.
SPLEETER_CHUNKS_MS = (250, 500, 1000, 2000, 4000)
_SPLEETER_DEFAULT_MS = 1000


def _spleeter_chunk_ms(name: str) -> int | None:
    """-> chunk in ms for a spleeter config name, or None if not one."""
    if name == "spleeter":
        return _SPLEETER_DEFAULT_MS
    if not name.startswith("spleeter_") or not name.endswith("ms"):
        return None
    body = name[len("spleeter_"):-len("ms")]
    if not body.isdigit() or int(body) <= 0:
        return None
    return int(body)


def _files_for(name: str) -> tuple[str, ...]:
    if _spleeter_chunk_ms(name) is not None:
        name = "spleeter"
    f = _MODEL_FILES.get(name)
    if f is None:
        return ()
    return f if isinstance(f, tuple) else (f,)


def _have_sherpa() -> bool:
    """sherpa-onnx is deliberately not an app dependency (see spleeter.py),
    so the separator is only offered when the research venv provides it."""
    import importlib.util
    return importlib.util.find_spec("sherpa_onnx") is not None


def model_file(name: str) -> str | None:
    """Filename for a processor's ONNX weights (first file, for
    multi-file processors), or None for passthrough."""
    files = _files_for(name)
    return files[0] if files else None


def available(models_dir: Path) -> list[str]:
    def installed(n: str) -> bool:
        return all((models_dir / f).is_file() for f in _files_for(n))

    names = ["passthrough"]
    names += [n for n in _MODEL_FILES if n != "spleeter" and installed(n)]
    # ONE entry, not one per chunk: this is what the model picker shows and
    # what test_processors_offline iterates, and neither wants five copies of
    # a 52 MB separator. The per-chunk variants are a sweep axis — ask for
    # them by name via is_available()/create().
    #
    # Gated on sherpa-onnx being importable too, so a sweep cannot pass the
    # availability check and then die on the first create() halfway through.
    if installed("spleeter") and _have_sherpa():
        names.append("spleeter")
    return names


def is_available(name: str, models_dir: Path) -> bool:
    """Can `name` be created? Understands the parameterised spleeter names,
    which available() does not enumerate."""
    if name in available(models_dir):
        return True
    if _spleeter_chunk_ms(name) is None:
        return False
    return (all((models_dir / f).is_file() for f in _files_for(name))
            and _have_sherpa())


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
    chunk_ms = _spleeter_chunk_ms(name)
    if chunk_ms is not None:
        from .spleeter import SpleeterProcessor
        proc = SpleeterProcessor(
            str(paths[0]), str(paths[1]),
            chunk_samples=round(chunk_ms * SpleeterProcessor.sample_rate / 1000))
        proc.name = name
        return proc
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
