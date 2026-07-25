"""Shared onnxruntime session setup (thread caps for a CPU-constrained box)."""


def make_session(model_path: str):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # Fixed at 1, NOT scaled with os.cpu_count(): ORT threads busy-spin
    # between blocks by default, so cpu_count()//2 threads meant that many
    # cores pinned near 100% on bigger machines — measured driving a 100W+
    # power jump for a workload that only needs a few ms per 20ms block.
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    opts.add_session_config_entry("session.inter_op.allow_spinning", "0")

    providers = ["CPUExecutionProvider"]
    try:
        if "OpenVINOExecutionProvider" in ort.get_available_providers():
            providers.insert(0, "OpenVINOExecutionProvider")
    except Exception:
        pass
    return ort.InferenceSession(model_path, opts, providers=providers)
