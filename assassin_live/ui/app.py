"""Layer 4 — control panel: on/off, a single Original/De-Musiced mix fader
(with independent mute for each side), a live output-level meter, and
pipeline / output-device pickers. Every control is live — changing pipeline,
output device, the mix, or mute while the engine is running takes effect
immediately, no need to stop first.

tkinter + ttk only (stdlib, no extra weight). Supervision loop runs every
second: routing.check() re-asserts the trap sink and retargets the engine
when the output device changes (Bluetooth headset reconnects etc.).
"""

import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

from ..audio.engine import AudioEngine
from ..audio.routing import RoutingSession, list_sinks, SINK_NAME
from ..paths import models_dir, SETTINGS_FILE
from .. import processors
from .widgets import HSlider

BG = "#0d0d10"
PANEL = "#17171c"
TEXT = "#f2f2f4"
SUBTEXT = "#87878f"
TRACK = "#2b2b32"
RED = "#ef4056"
ON_COLOR = "#2ecc71"
OFF_COLOR = "#3a3d45"

WAVE_BARS = 42
WAVE_WIDTH = 380
WAVE_HEIGHT = 68


def _icon_path() -> Path:
    # PyInstaller onefile: bundled at the archive root (see scripts/build_deb.sh).
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "icon.png"
    return Path(__file__).resolve().parent / "assets" / "icon.png"


class App:
    def __init__(self):
        saved = self._load_settings()
        self.routing = RoutingSession()
        self.routing.preferred_name = saved.get("output_name")
        self.engine: AudioEngine | None = None
        self.mute_dry = saved.get("mute_dry", False)
        self.mute_wet = saved.get("mute_wet", False)
        self.mix_pct = saved.get("mix_pct", 100.0)
        self.midside_enabled = saved.get("midside_enabled", False)
        self.bandlimit_enabled = saved.get("bandlimit_enabled", True)
        self.atten_db = saved.get("atten_db", 0.0)
        self._initial_pipeline = saved.get("pipeline", "dpdfnet_hr")
        self.output_map: dict[str, str] = {}

        self.root = tk.Tk()
        self.root.title("Music Assassin Live")
        self.root.geometry("420x600")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        icon_path = _icon_path()
        if icon_path.is_file():
            self._icon_img = tk.PhotoImage(file=str(icon_path))  # kept alive on self
            self.root.iconphoto(True, self._icon_img)

        self._build_style()
        self._build_header()
        self._build_pickers()
        self._build_midside_toggle()
        self._build_bandlimit_toggle()
        self._build_atten_slider()
        self._build_waveform()
        self._build_sliders()
        self._update_atten_visibility()

        self.status = tk.Label(self.root, text="idle", justify=tk.LEFT,
                               font=("Mono", 9), bg=BG, fg=SUBTEXT)
        self.status.pack(pady=(4, 10))

        self.enabled = False
        RoutingSession.recover_stale()
        self._refresh_outputs()
        self.root.after(1000, self._tick)
        self.root.after(90, self._wave_tick)

    # -- settings persistence ----------------------------------------------------
    @staticmethod
    def _load_settings() -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        try:
            SETTINGS_FILE.write_text(json.dumps({
                "pipeline": self.model.get(),
                "output_name": self.routing.preferred_name,
                "mix_pct": self.mix_pct,
                "mute_dry": self.mute_dry,
                "mute_wet": self.mute_wet,
                "midside_enabled": self.midside_enabled,
                "bandlimit_enabled": self.bandlimit_enabled,
                "atten_db": self.atten_db,
            }))
        except OSError:
            pass  # best-effort; a failed save shouldn't block quitting

    # -- style -----------------------------------------------------------------
    def _build_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL,
                        foreground=TEXT, arrowcolor=TEXT, borderwidth=0,
                        selectbackground=PANEL, selectforeground=TEXT)
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", TEXT)],
                  selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", RED)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    # -- layout builders ---------------------------------------------------------
    def _build_header(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill=tk.X, padx=20, pady=(18, 14))
        title = tk.Frame(row, bg=BG)
        title.pack(side=tk.LEFT)
        tk.Label(title, text="Music Assassin", font=("Sans", 16, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(title, text="live music removal", font=("Sans", 9),
                 bg=BG, fg=SUBTEXT).pack(anchor="w")

        self.btn = tk.Button(row, text="OFF", font=("Sans", 11, "bold"),
                             fg="white", bg=OFF_COLOR, activebackground=OFF_COLOR,
                             activeforeground="white", bd=0, relief=tk.FLAT,
                             highlightthickness=0, padx=18, pady=8,
                             cursor="hand2", command=self._toggle)
        self.btn.pack(side=tk.RIGHT, anchor="e")

    def _pick_default_model(self, names: list[str]) -> str:
        for candidate in (self._initial_pipeline, "dpdfnet_hr", "gtcrn"):
            if candidate and candidate in names:
                return candidate
        return names[0]

    def _build_pickers(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill=tk.X, padx=20, pady=(0, 14))

        left = tk.Frame(row, bg=PANEL)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Label(left, text="Pipeline", font=("Sans", 8), bg=PANEL,
                 fg=SUBTEXT).pack(anchor="w", padx=12, pady=(6, 0))
        names = processors.available(models_dir())
        self.model = tk.StringVar(value=self._pick_default_model(names))
        self.model_box = ttk.Combobox(left, textvariable=self.model, values=names,
                                      state="readonly", style="TCombobox")
        self.model_box.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.model_box.bind("<<ComboboxSelected>>", self._on_model_change)

        right = tk.Frame(row, bg=PANEL)
        right.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        tk.Label(right, text="Output", font=("Sans", 8), bg=PANEL,
                 fg=SUBTEXT).pack(anchor="w", padx=12, pady=(6, 0))
        self.output_var = tk.StringVar()
        self.output_box = ttk.Combobox(right, textvariable=self.output_var,
                                       state="readonly", style="TCombobox")
        self.output_box.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.output_box.bind("<<ComboboxSelected>>", self._on_output_change)

    def _style_toggle_btn(self, btn: tk.Button, label: str, enabled: bool) -> None:
        btn.config(
            text=f"{label}: {'ON' if enabled else 'OFF'}",
            fg=TEXT if enabled else SUBTEXT,
            bg=RED if enabled else PANEL,
            activebackground=RED if enabled else PANEL,
            activeforeground=TEXT if enabled else SUBTEXT)

    def _build_midside_toggle(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill=tk.X, padx=20, pady=(0, 14))
        self.midside_btn = tk.Button(
            row, font=("Sans", 9, "bold"), bd=0, relief=tk.FLAT,
            highlightthickness=0, padx=10, pady=8, cursor="hand2",
            command=self._toggle_midside)
        self._style_toggle_btn(self.midside_btn, "Mid/Side Prefilter", self.midside_enabled)
        self.midside_btn.pack(fill=tk.X)

    def _build_bandlimit_toggle(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill=tk.X, padx=20, pady=(0, 14))
        self.bandlimit_btn = tk.Button(
            row, font=("Sans", 9, "bold"), bd=0, relief=tk.FLAT,
            highlightthickness=0, padx=10, pady=8, cursor="hand2",
            command=self._toggle_bandlimit)
        self._style_toggle_btn(self.bandlimit_btn, "Band-Limit (20 Hz-20 kHz)",
                               self.bandlimit_enabled)
        self.bandlimit_btn.pack(fill=tk.X)

    def _build_atten_slider(self):
        # only meaningful for speechdenoiser (its ONNX model takes an
        # atten_lim_db input); shown/hidden by _update_atten_visibility()
        # based on the selected pipeline. A slim single row, matching the
        # Mid/Side toggle's proportions — this is a secondary, occasional
        # control, not a primary fader like the mix below.
        self.atten_card = tk.Frame(self.root, bg=PANEL)
        row = tk.Frame(self.atten_card, bg=PANEL)
        row.pack(fill=tk.X, padx=14, pady=10)
        tk.Label(row, text="Suppression Limit", font=("Sans", 9, "bold"),
                 bg=PANEL, fg=TEXT).pack(side=tk.LEFT)
        self.atten_val_lbl = tk.Label(row, text=self._atten_label(self.atten_db),
                                      font=("Sans", 9), bg=PANEL, fg=RED)
        self.atten_val_lbl.pack(side=tk.RIGHT)
        self.atten_slider = HSlider(row, width=180, height=20, color=RED,
                                    track=TRACK, bg=PANEL, value=self.atten_db,
                                    minv=0, maxv=40, command=self._on_atten_change)
        self.atten_slider.pack(side=tk.RIGHT, padx=(0, 12))

    def _update_atten_visibility(self):
        if self.model.get() == "speechdenoiser":
            self.atten_card.pack(fill=tk.X, padx=20, pady=(0, 14), before=self.wave_wrap)
        else:
            self.atten_card.pack_forget()

    def _build_waveform(self):
        self.wave_wrap = tk.Frame(self.root, bg=PANEL)
        self.wave_wrap.pack(padx=20, pady=(0, 16))
        self.wave_canvas = tk.Canvas(self.wave_wrap, width=WAVE_WIDTH, height=WAVE_HEIGHT,
                                     bg=PANEL, highlightthickness=0)
        self.wave_canvas.pack(padx=10, pady=10)

    def _build_sliders(self):
        card = tk.Frame(self.root, bg=PANEL)
        card.pack(fill=tk.X, padx=20, pady=(0, 6))

        row = tk.Frame(card, bg=PANEL)
        row.pack(fill=tk.X, padx=14, pady=(14, 4))
        self.dry_mute_btn = tk.Button(
            row, font=("Sans", 11), bg=PANEL, bd=0, relief=tk.FLAT,
            highlightthickness=0, activebackground=PANEL, cursor="hand2",
            command=self._toggle_mute_dry)
        self.dry_mute_btn.pack(side=tk.LEFT)
        tk.Label(row, text="Original", font=("Sans", 10, "bold"), bg=PANEL,
                 fg=TEXT).pack(side=tk.LEFT, padx=(6, 0))

        self.wet_mute_btn = tk.Button(
            row, font=("Sans", 11), bg=PANEL, bd=0, relief=tk.FLAT,
            highlightthickness=0, activebackground=PANEL, cursor="hand2",
            command=self._toggle_mute_wet)
        self.wet_mute_btn.pack(side=tk.RIGHT)
        tk.Label(row, text="De-Musiced", font=("Sans", 10, "bold"), bg=PANEL,
                 fg=TEXT).pack(side=tk.RIGHT, padx=(0, 6))
        self._style_mute_btn(self.dry_mute_btn, self.mute_dry)
        self._style_mute_btn(self.wet_mute_btn, self.mute_wet)

        self.mix_slider = HSlider(card, width=340, height=26, color=RED, track=TRACK,
                                  bg=PANEL, value=self.mix_pct, minv=0, maxv=300,
                                  markers=(50, 100), marker_color=SUBTEXT,
                                  command=self._on_mix_change)
        self.mix_slider.pack(padx=14, pady=(6, 2))
        self.mix_val_lbl = tk.Label(card, text=f"{round(self.mix_pct)}%", font=("Sans", 9),
                                    bg=PANEL, fg=RED)
        self.mix_val_lbl.pack(pady=(0, 14))

    # -- mix / mute callbacks -----------------------------------------------------
    @staticmethod
    def _wet_boost(mix_pct: float) -> float:
        # past 100% the crossfade is already fully wet; extra travel boosts
        # wet gain instead, up to 3x (+9.5 dB) at 300%, to offset
        # post-processing loudness loss. engine._soft_limit keeps this from
        # ever clipping/overdriving the output.
        return 1.0 + max(0.0, mix_pct - 100.0) / 100.0

    def _on_mix_change(self, value):
        self.mix_pct = value
        self.mix_val_lbl.config(text=f"{round(value)}%")
        if self.engine:
            self.engine.set_intensity(value / 100.0)
            self.engine.set_volumes(wet=self._wet_boost(value))

    @staticmethod
    def _style_mute_btn(btn: tk.Button, muted: bool) -> None:
        btn.config(text="\U0001F507" if muted else "\U0001F50A",
                  fg=RED if muted else SUBTEXT, activeforeground=RED if muted else SUBTEXT)

    def _toggle_mute_dry(self):
        self.mute_dry = not self.mute_dry
        self._style_mute_btn(self.dry_mute_btn, self.mute_dry)
        self._push_mutes()

    def _toggle_mute_wet(self):
        self.mute_wet = not self.mute_wet
        self._style_mute_btn(self.wet_mute_btn, self.mute_wet)
        self._push_mutes()

    def _push_mutes(self):
        if self.engine:
            self.engine.set_volumes(mute_dry=self.mute_dry, mute_wet=self.mute_wet)

    @staticmethod
    def _atten_label(value: float) -> str:
        return "0 dB (unlimited)" if value < 0.5 else f"{round(value)} dB"

    def _on_atten_change(self, value):
        self.atten_db = value
        self.atten_val_lbl.config(text=self._atten_label(value))
        if self.engine:
            self.engine.set_atten_limit(value)

    def _toggle_midside(self):
        self.midside_enabled = not self.midside_enabled
        self._style_toggle_btn(self.midside_btn, "Mid/Side Prefilter", self.midside_enabled)
        if self.engine:
            self.engine.set_midside(self.midside_enabled)

    def _toggle_bandlimit(self):
        self.bandlimit_enabled = not self.bandlimit_enabled
        self._style_toggle_btn(self.bandlimit_btn, "Band-Limit (20 Hz-20 kHz)",
                               self.bandlimit_enabled)
        if self.engine:
            self.engine.set_bandlimit(self.bandlimit_enabled)

    # -- pipeline / output pickers (live-switchable) -----------------------------
    def _on_model_change(self, _evt=None):
        self._update_atten_visibility()
        if not (self.enabled and self.engine):
            return
        try:
            proc = processors.create(self.model.get(), models_dir())
        except Exception as e:  # noqa: BLE001 — surface anything to the user
            messagebox.showerror("Pipeline switch failed", str(e))
            return
        self.engine.set_processor(proc)

    def _refresh_outputs(self):
        sinks = [s for s in list_sinks() if s.name != SINK_NAME]
        labels = [s.description or s.name for s in sinks]
        self.output_map = {(s.description or s.name): s.name for s in sinks}
        self.output_box.config(values=labels)
        current = self.output_var.get()
        if labels and current not in labels:
            default_label = None
            if self.routing.real:
                default_label = self.routing.real.description or self.routing.real.name
            elif self.routing.preferred_name:
                # not enabled yet (no routing.real) — try the saved device
                for s in sinks:
                    if s.name == self.routing.preferred_name:
                        default_label = s.description or s.name
                        break
            self.output_var.set(default_label if default_label in labels else labels[0])

    def _on_output_change(self, _evt=None):
        name = self.output_map.get(self.output_var.get())
        if not name:
            return
        self.routing.preferred_name = name
        if self.enabled and self.engine:
            for s in list_sinks():
                if s.name == name:
                    self.routing.real = s
                    self.engine.retarget(self.routing.monitor_source, s.name)
                    break

    # -- actions ---------------------------------------------------------------
    def _toggle(self):
        if self.enabled:
            self._turn_off()
        else:
            self._turn_on()

    def _turn_on(self):
        try:
            proc = processors.create(self.model.get(), models_dir())
            real = self.routing.enable()
            if real is None:
                self.routing.disable()
                messagebox.showwarning(
                    "No output device",
                    "No hardware audio sink found (Bluetooth asleep?). "
                    "Play something / reconnect and try again.")
                return
            self.engine = AudioEngine(proc)
            self.engine.set_intensity(self.mix_pct / 100.0)
            self.engine.set_volumes(wet=self._wet_boost(self.mix_pct))
            self.engine.set_midside(self.midside_enabled)
            self.engine.set_bandlimit(self.bandlimit_enabled)
            self.engine.set_atten_limit(self.atten_db)
            self._push_mutes()
            self.engine.start(self.routing.monitor_source, real.name)
            self.enabled = True
            self.btn.config(text="ON", bg=ON_COLOR, activebackground=ON_COLOR)
        except Exception as e:  # noqa: BLE001 — surface anything to the user
            self.routing.disable()
            if self.engine:
                self.engine.stop()
                self.engine = None
            messagebox.showerror("Enable failed", str(e))

    def _turn_off(self):
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.routing.disable()
        self.enabled = False
        self.btn.config(text="OFF", bg=OFF_COLOR, activebackground=OFF_COLOR)

    # -- live waveform meter -----------------------------------------------------
    def _wave_tick(self):
        c = self.wave_canvas
        c.delete("all")
        levels = self.engine.recent_levels() if self.engine else []
        if len(levels) < WAVE_BARS:
            levels = [0.0] * (WAVE_BARS - len(levels)) + levels
        else:
            levels = levels[-WAVE_BARS:]
        gap = 3
        bar_w = max(1.0, (WAVE_WIDTH - gap * (WAVE_BARS + 1)) / WAVE_BARS)
        cy = WAVE_HEIGHT / 2
        for i, lvl in enumerate(levels):
            bar_h = min(WAVE_HEIGHT - 6, max(2.0, lvl * WAVE_HEIGHT * 6))
            x0 = gap + i * (bar_w + gap)
            c.create_rectangle(x0, cy - bar_h / 2, x0 + bar_w, cy + bar_h / 2,
                               fill=RED, outline="")
        self.root.after(90, self._wave_tick)

    # -- supervision -------------------------------------------------------------
    def _tick(self):
        if self.enabled and self.engine:
            if not self.engine.stream_ok:
                # PortAudio stream died silently (uncaught callback
                # exception or the device vanishing) — the button would
                # otherwise keep showing "on" with no audio flowing until
                # manually toggled. Try the same recovery as a real
                # sink change; only give up and turn off if that fails too.
                try:
                    self.engine.retarget(self.routing.monitor_source,
                                         self.routing.real.name)
                    self.status.config(text="audio stream recovered automatically")
                except Exception as e:  # noqa: BLE001 — see retarget handling below
                    self._turn_off()
                    self.status.config(text=f"audio stream died, restart failed: {e}")
                return
            event = self.routing.check()
            if event == "real_sink_changed" and self.routing.real:
                try:
                    self.engine.retarget(self.routing.monitor_source,
                                         self.routing.real.name)
                except Exception as e:  # noqa: BLE001 — a broken stream must not
                    # wedge the app; drop back to a clean, known-off state
                    # instead of leaving the trap sink stuck as default with
                    # no audio flowing.
                    self._turn_off()
                    self.status.config(text=f"retarget failed, turned off: {e}")
                    return
            elif event == "real_sink_lost":
                self._turn_off()
                self.status.config(text="output device lost — turned off")
            s = self.engine.stats if self.engine else None
            if s:
                out = self.routing.real.description or self.routing.real.name \
                    if self.routing.real else "?"
                self.status.config(text=(
                    f"out: {out}\n"
                    f"model: {self.model.get()}  "
                    f"{s.worker_ms_avg:.1f} ms/block (20 ms budget)\n"
                    f"blocks: {s.blocks_in}   fallbacks: {s.fallback_blocks}   "
                    f"xruns: {s.xruns}"))
        elif not self.enabled:
            self.status.config(text="idle")
        self._refresh_outputs()
        self.root.after(1000, self._tick)

    def _quit(self):
        self._save_settings()
        self._turn_off()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    App().run()
