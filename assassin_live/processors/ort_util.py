"""Shared onnxruntime session setup (thread caps for a CPU-constrained box)."""

import os


def make_session(model_path: str):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # Streaming frames are tiny; more threads adds sync overhead and steals
    # cores from the rest of the system.
    opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

    providers = ["CPUExecutionProvider"]
    try:
        if "OpenVINOExecutionProvider" in ort.get_available_providers():
            providers.insert(0, "OpenVINOExecutionProvider")
    except Exception:
        pass
    return ort.InferenceSession(model_path, opts, providers=providers)
