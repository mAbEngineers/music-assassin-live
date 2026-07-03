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
