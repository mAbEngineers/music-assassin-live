"""Small stdlib-only Canvas widgets for the control panel (no image assets,
no extra dependencies)."""

import tkinter as tk

TRACK_THICKNESS = 5
DOT_RADIUS = 9  # noticeably wider than the track — easy to grab


class VSlider(tk.Canvas):
    """A vertical fader with a round drag handle (ttk.Scale's thumb can't be
    styled into a circle without image assets, so this draws its own).
    Drag up to increase, down to decrease."""

    def __init__(self, parent, *, width=26, height=140, color, track, bg,
                 value=100.0, minv=0.0, maxv=150.0, command=None):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self.w, self.h = width, height
        self.color, self.track = color, track
        self.minv, self.maxv = minv, maxv
        self.value = value
        self.command = command
        self.pad = DOT_RADIUS + 2
        self.bind("<ButtonPress-1>", self._on_pointer)
        self.bind("<B1-Motion>", self._on_pointer)
        self._draw()

    def _frac(self) -> float:
        span = self.maxv - self.minv
        return 0.0 if span == 0 else (self.value - self.minv) / span

    def _y_for(self, frac: float) -> float:
        usable = self.h - 2 * self.pad
        return (self.h - self.pad) - usable * frac  # frac 0 -> bottom, 1 -> top

    def _draw(self):
        self.delete("all")
        cx = self.w / 2
        y_top, y_bot = self.pad, self.h - self.pad
        self.create_line(cx, y_bot, cx, y_top, fill=self.track,
                         width=TRACK_THICKNESS, capstyle=tk.ROUND)
        frac = max(0.0, min(1.0, self._frac()))
        yh = self._y_for(frac)
        if frac > 0.001:
            self.create_line(cx, y_bot, cx, yh, fill=self.color,
                             width=TRACK_THICKNESS, capstyle=tk.ROUND)
        self.create_oval(cx - DOT_RADIUS, yh - DOT_RADIUS,
                         cx + DOT_RADIUS, yh + DOT_RADIUS,
                         fill=self.color, outline="")

    def set(self, value: float, notify: bool = False):
        self.value = max(self.minv, min(self.maxv, value))
        self._draw()
        if notify and self.command:
            self.command(self.value)

    def _on_pointer(self, event):
        usable = self.h - 2 * self.pad
        frac = ((self.h - self.pad) - event.y) / usable if usable > 0 else 0.0
        frac = max(0.0, min(1.0, frac))
        self.set(self.minv + frac * (self.maxv - self.minv), notify=True)
