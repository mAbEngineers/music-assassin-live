"""A1 spike: RTF vs chunk size for sherpa-onnx Spleeter 2-stem int8.

ROADMAP A1 calls this the highest-leverage item, on the strength of RTF 0.067
measured in the research repo. That number is real but was taken on a 60 s
buffer, and realtime use needs small chunks. Per-call overhead does not shrink
with the chunk, so the question this answers is whether the headroom survives
at a chunk size anyone would actually ship.

MEASURED 2026-08-18 (32-core box, 1 thread, 44.1 kHz synthetic input):

    chunk    ms/call     RTF   headroom
    0.25 s     153.3   0.613       1.6x
    0.50 s     131.9   0.264       3.8x
    1.00 s     129.0   0.129       7.8x
    2.00 s     137.4   0.069      14.6x
    4.00 s     160.3   0.040      25.0x
    8.00 s     205.1   0.026      39.0x
    peak RSS 478 MB, model load 4.2 s

The shape is the finding: cost per call is nearly FLAT. It is fixed overhead,
not work proportional to the audio, so RTF improves only because the
denominator grows. Latency is therefore the binding constraint and RTF is
close to irrelevant -- the opposite of how A1 was framed.

API notes, learned the hard way and worth not re-deriving:
  * process(sample_rate, samples) with samples (channels, n) float32; it
    resamples internally to 44.1 kHz and reports that in output.sample_rate.
  * stems[0] is vocals, stems[1] accompaniment -- confirmed by
    cross-correlating each against the corpus's own stems, not assumed.
  * stems[i].data is (channels, m) with m < n: 44100 in gave 44032 out. A
    streaming wrapper has to carry the remainder.
  * EVERY call emits a startup transient: ~26 samples reaching |466| at the
    head of the buffer, against a p99.99 of 0.6 for the rest. Harmless in a
    one-shot offline call, a click per chunk in a streaming one. The wrapper
    must trim or cross-fade it.

Run with the research venv, which already has sherpa-onnx:
  ~/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python \
      scripts/spike_a1_separator.py <dir with vocals.int8.onnx>
"""
import resource, sys, time
import numpy as np
import sherpa_onnx as so

md = sys.argv[1]
cfg = so.OfflineSourceSeparationConfig()
cfg.model.spleeter.vocals = f"{md}/vocals.int8.onnx"
cfg.model.spleeter.accompaniment = f"{md}/accompaniment.int8.onnx"
cfg.model.num_threads = 1
t0 = time.time()
sep = so.OfflineSourceSeparation(cfg)
print(f"model load: {time.time() - t0:.2f} s")

SR = 44100
rng = np.random.default_rng(0)
t = np.arange(SR * 8) / SR
music = 0.2 * (np.sin(2*np.pi*220*t) + np.sin(2*np.pi*330*t))
voice = 0.3 * np.sin(2*np.pi*(180+40*np.sin(2*np.pi*2.5*t))*t) * (np.sin(2*np.pi*3*t) > 0)
mix = (music + voice + 0.01*rng.standard_normal(len(t))).astype(np.float32)
stereo = np.stack([mix, mix])

print(f"\n{'chunk':>8} {'calls':>6} {'ms/call':>9} {'RTF':>7} {'x-realtime':>11}")
print("-" * 46)
for secs in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
    n = int(SR * secs)
    times = []
    for i in range(0, stereo.shape[1] - n + 1, n):
        chunk = np.ascontiguousarray(stereo[:, i:i+n])
        t0 = time.perf_counter()
        sep.process(SR, chunk)
        times.append(time.perf_counter() - t0)
    if not times:
        continue
    ms = 1000 * np.mean(times)
    rtf = np.mean(times) / secs
    print(f"{secs:>7.2f}s {len(times):>6} {ms:>9.1f} {rtf:>7.3f} {1/rtf:>10.1f}x")

rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"\npeak RSS: {rss:.0f} MB")
