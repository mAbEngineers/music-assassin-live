"""Layer 4 — minimal shell: one window, one big toggle.

tkinter only (stdlib). Supervision loop runs every second: routing.check()
re-asserts the trap sink and retargets the engine when the output device
changes (Bluetooth headset reconnects etc.).
"""

import tkinter as tk
from tkinter import ttk, messagebox

from ..audio.engine import AudioEngine
from ..audio.routing import RoutingSession
from ..paths import models_dir
from .. import processors

GREEN, GRAY = "#2e8b57", "#555555"


class App:
    def __init__(self):
        self.routing = RoutingSession()
        self.engine: AudioEngine | None = None

        self.root = tk.Tk()
        self.root.title("Music Assassin Live")
        self.root.geometry("340x300")
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        self.btn = tk.Button(self.root, text="OFF", font=("Sans", 28, "bold"),
                             fg="white", bg=GRAY, width=8, height=2,
                             command=self._toggle)
        self.btn.pack(pady=16)

        row = tk.Frame(self.root)
        row.pack()
        tk.Label(row, text="Model:").pack(side=tk.LEFT)
        self.model = tk.StringVar(value="gtcrn")
        names = processors.available(models_dir())
        self.model_box = ttk.Combobox(row, textvariable=self.model,
                                      values=names, state="readonly", width=16)
        if "gtcrn" not in names:
            self.model.set(names[0])
        self.model_box.pack(side=tk.LEFT, padx=6)

        self.bypass = tk.BooleanVar(value=False)
        tk.Checkbutton(self.root, text="Bypass (A/B compare)",
                       variable=self.bypass,
                       command=self._on_bypass).pack(pady=4)

        self.status = tk.Label(self.root, text="idle", justify=tk.LEFT,
                               font=("Mono", 9))
        self.status.pack(pady=8)

        self.enabled = False
        RoutingSession.recover_stale()
        self.root.after(1000, self._tick)

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
            self.engine.set_bypass(self.bypass.get())
            self.engine.start(self.routing.monitor_source, real.name)
            self.enabled = True
            self.btn.config(text="ON", bg=GREEN)
            self.model_box.config(state="disabled")
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
        self.btn.config(text="OFF", bg=GRAY)
        self.model_box.config(state="readonly")

    def _on_bypass(self):
        if self.engine:
            self.engine.set_bypass(self.bypass.get())

    # -- supervision -------------------------------------------------------------
    def _tick(self):
        if self.enabled and self.engine:
            event = self.routing.check()
            if event == "real_sink_changed" and self.routing.real:
                self.engine.retarget(self.routing.monitor_source,
                                     self.routing.real.name)
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
        self.root.after(1000, self._tick)

    def _quit(self):
        self._turn_off()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    App().run()
