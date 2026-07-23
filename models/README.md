# Models

ONNX weights are NOT committed. Each model ships as a release asset together
with its `model_card.json` (produced by the Music-Assassin research repo's
benchmark harness).

For development, either:

```bash
python scripts/import_models.py --source ../Music-Assassin/models
```

which installs to `~/.local/share/music-assassin/models/`, or drop `.onnx`
files directly into this folder (dev fallback, gitignored), or set
`MUSIC_ASSASSIN_MODELS=/path/to/models`.

The `.json` files here are the model cards for the current model set.

## Licensing (verified 2026-07-24)

| model | license | redistributable as release asset? |
|---|---|---|
| gtcrn | MIT | yes — include upstream license text |
| dpdfnet | Apache-2.0 (Ceva-IP) | yes — include license text + attribution |
| dpdfnet_hr | Apache-2.0 (Ceva-IP, same collection as dpdfnet) | yes — include license text + attribution |
| speechdenoiser | **unresolved** — upstream repo has no license | **no** — see its model card |

speechdenoiser stays usable for local development (user drops the file in
themselves), but must not ship in releases until upstream adds a license or
the model is re-exported from the dual-licensed DeepFilterNet3 source.
