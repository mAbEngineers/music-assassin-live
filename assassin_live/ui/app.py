"""Layer 4 — control panel: on/off, a single Original/De-Musiced mix fader
(with independent mute for each side), a live output-level meter, and
pipeline / output-device pickers. Every control is live — changing pipeline,
output device, the mix, or mute while the engine is running takes effect
immediately, no need to stop first.

tkinter + ttk only (stdlib, no extra weight). Supervision loop runs every
second: routing.check() re-asserts the trap sink and retargets the engine
when the output device changes (Bluetooth headset reconnects etc.).
"""

import tkinter as tk
from tkinter import ttk, messagebox

from ..audio.engine import AudioEngine
from ..audio.routing import RoutingSession, list_sinks, SINK_NAME
from ..paths import models_dir
from .. import processors
from .widgets import VSlider

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


class App:
    def __init__(self):
        self.routing = RoutingSession()
        self.engine: AudioEngine | None = None
        self.mute_dry = False
        self.mute_wet = False
        self.mix_pct = 100.0
        self.midside_enabled = False
        self.output_map: dict[str, str] = {}

        self.root = tk.Tk()
        self.root.title("Music Assassin Live")
        self.root.geometry("420x600")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        self._build_style()
        self._build_header()
        self._build_pickers()
        self._build_midside_toggle()
        self._build_waveform()
        self._build_sliders()

        self.status = tk.Label(self.root, text="idle", justify=tk.LEFT,
                               font=("Mono", 9), bg=BG, fg=SUBTEXT)
        self.status.pack(pady=(4, 10))

        self.enabled = False
        RoutingSession.recover_stale()
        self._refresh_outputs()
        self.root.after(1000, self._tick)
        self.root.after(90, self._wave_tick)

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

    def _build_pickers(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill=tk.X, padx=20, pady=(0, 14))

        left = tk.Frame(row, bg=PANEL)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Label(left, text="Pipeline", font=("Sans", 8), bg=PANEL,
                 fg=SUBTEXT).pack(anchor="w", padx=12, pady=(6, 0))
        self.model = tk.StringVar(value="dpdfnet_hr")
        names = processors.available(models_dir())
        self.model_box = ttk.Combobox(left, textvariable=self.model, values=names,
                                      state="readonly", style="TCombobox")
        if "dpdfnet_hr" not in names:
            self.model.set("gtcrn" if "gtcrn" in names else names[0])
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

    def _build_midside_toggle(self):
        row = tk.Frame(self.root, bg=BG)
        row.pack(fill=tk.X, padx=20, pady=(0, 14))
        self.midside_btn = tk.Button(
            row, text="Mid/Side Prefilter: OFF", font=("Sans", 9, "bold"),
            fg=SUBTEXT, bg=PANEL, activebackground=PANEL, activeforeground=SUBTEXT,
            bd=0, relief=tk.FLAT, highlightthickness=0, padx=10, pady=8,
            cursor="hand2", command=self._toggle_midside)
        self.midside_btn.pack(fill=tk.X)

    def _build_waveform(self):
        wrap = tk.Frame(self.root, bg=PANEL)
        wrap.pack(padx=20, pady=(0, 16))
        self.wave_canvas = tk.Canvas(wrap, width=WAVE_WIDTH, height=WAVE_HEIGHT,
                                     bg=PANEL, highlightthickness=0)
        self.wave_canvas.pack(padx=10, pady=10)

    def _build_sliders(self):
        card = tk.Frame(self.root, bg=PANEL)
        card.pack(padx=20, pady=(0, 6))

        top = tk.Frame(card, bg=PANEL)
        top.pack(pady=(14, 4))
        self.wet_mute_btn = tk.Button(
            top, text="\U0001F50A", font=("Sans", 11), bg=PANEL, fg=SUBTEXT,
            bd=0, relief=tk.FLAT, highlightthickness=0, activebackground=PANEL,
            activeforeground=SUBTEXT, cursor="hand2", command=self._toggle_mute_wet)
        self.wet_mute_btn.pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(top, text="De-Musiced", font=("Sans", 10, "bold"), bg=PANEL,
                 fg=TEXT).pack(side=tk.LEFT)

        self.mix_slider = VSlider(card, width=30, height=150, color=RED, track=TRACK,
                                  bg=PANEL, value=100.0, minv=0, maxv=100,
                                  command=self._on_mix_change)
        self.mix_slider.pack(pady=6)
        self.mix_val_lbl = tk.Label(card, text="100%", font=("Sans", 9),
                                    bg=PANEL, fg=RED)
        self.mix_val_lbl.pack(pady=(2, 6))

        bottom = tk.Frame(card, bg=PANEL)
        bottom.pack(pady=(4, 14))
        self.dry_mute_btn = tk.Button(
            bottom, text="\U0001F50A", font=("Sans", 11), bg=PANEL, fg=SUBTEXT,
            bd=0, relief=tk.FLAT, highlightthickness=0, activebackground=PANEL,
            activeforeground=SUBTEXT, cursor="hand2", command=self._toggle_mute_dry)
        self.dry_mute_btn.pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(bottom, text="Original", font=("Sans", 10, "bold"), bg=PANEL,
                 fg=TEXT).pack(side=tk.LEFT)

    # -- mix / mute callbacks -----------------------------------------------------
    def _on_mix_change(self, value):
        self.mix_pct = value
        self.mix_val_lbl.config(text=f"{round(value)}%")
        if self.engine:
            self.engine.set_intensity(value / 100.0)

    def _toggle_mute_dry(self):
        self.mute_dry = not self.mute_dry
        self.dry_mute_btn.config(
            text="\U0001F507" if self.mute_dry else "\U0001F50A",
            fg=RED if self.mute_dry else SUBTEXT)
        self._push_mutes()

    def _toggle_mute_wet(self):
        self.mute_wet = not self.mute_wet
        self.wet_mute_btn.config(
            text="\U0001F507" if self.mute_wet else "\U0001F50A",
            fg=RED if self.mute_wet else SUBTEXT)
        self._push_mutes()

    def _push_mutes(self):
        if self.engine:
            self.engine.set_volumes(dry=1.0, wet=1.0,
                                    mute_dry=self.mute_dry, mute_wet=self.mute_wet)

    def _toggle_midside(self):
        self.midside_enabled = not self.midside_enabled
        self.midside_btn.config(
            text=f"Mid/Side Prefilter: {'ON' if self.midside_enabled else 'OFF'}",
            fg=TEXT if self.midside_enabled else SUBTEXT,
            bg=RED if self.midside_enabled else PANEL,
            activebackground=RED if self.midside_enabled else PANEL)
        if self.engine:
            self.engine.set_midside(self.midside_enabled)

    # -- pipeline / output pickers (live-switchable) -----------------------------
    def _on_model_change(self, _evt=None):
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
            self.engine.set_midside(self.midside_enabled)
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
        self._turn_off()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    App().run()
