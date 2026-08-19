"""Small stdlib-only Canvas widgets for the control panel (no image assets,
no extra dependencies).

Everything here exists because ttk cannot be styled into the shape the
control panel wants. That is not a complaint about ttk: its widgets follow
the platform theme, and a fixed-size dark panel with capsule switches and
pill dropdowns is not what any platform theme draws. A Canvas will draw
anything, costs nothing, and stays inside the stdlib — which the app's
`.deb` depends on.

Where a native widget *is* close enough it is kept. Cards are plain
`tk.Frame`s with a one-pixel `highlightbackground`, not Canvas panels:
rounding a card means drawing the group on a Canvas and placing its children
with `create_window`, which trades every layout guarantee `pack()` gives for
a corner radius nobody will notice behind a hairline border. The radius is
worth having on the pill, the switches and the dropdown fields — the things
whose *shape* says what they do.

Colours, fonts and pixel sizes are all passed in. These widgets own their
drawing and nothing else; the palette and the display scaling live in
ui/app.py, so there is one place to change either.
"""

import math
import tkinter as tk

TRACK_THICKNESS = 5
DOT_RADIUS = 9  # noticeably wider than the track — easy to grab

# Level meters: the floor of the dB scale the bars are drawn on.
METER_FLOOR_DB = -60.0


def round_rect(canvas: tk.Canvas, x0, y0, x1, y1, r, **kw) -> int:
    """A rounded rectangle, as a smoothed polygon.

    Tk has no rounded-rectangle primitive. Doubling the corner points and
    asking for a smoothed polygon gets one whose corners are a spline rather
    than a true arc — indistinguishable at these radii, and one item instead
    of the four arcs plus two rectangles the exact construction needs.
    """
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
           x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
           x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


def db_fraction(rms: float, floor: float = METER_FLOOR_DB) -> float:
    """RMS to a 0..1 meter height on a dB scale.

    Linear amplitude is the wrong axis for a level meter: music sits around
    -25 dBFS, which is 5 % of full scale and a bar you cannot see move. It
    also makes any dB annotation a lie, since the marks would not be where
    the scale says they are.
    """
    if rms <= 1e-7:
        return 0.0
    db = 20.0 * math.log10(rms)
    return max(0.0, min(1.0, (db - floor) / -floor))


class HSlider(tk.Canvas):
    """A horizontal slider with a round drag handle (ttk.Scale's thumb can't
    be styled into a circle without image assets, so this draws its own).
    Drag left/right."""

    def __init__(self, parent, *, width=300, height=24, color, track, bg,
                 value=0.0, minv=0.0, maxv=40.0, command=None,
                 markers=(), marker_color="#9a9aa2", scale=1.0):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self.w, self.h = width, height
        self.color, self.track = color, track
        self.minv, self.maxv = minv, maxv
        self.value = value
        self.command = command
        self.markers, self.marker_color = markers, marker_color
        # The handle has to grow with the display, or it is a 9 px target on
        # a 200 % screen while everything around it doubled.
        self.dot = max(6, int(round(DOT_RADIUS * scale)))
        self.thickness = max(3, int(round(TRACK_THICKNESS * scale)))
        self.pad = self.dot + 2
        self.bind("<ButtonPress-1>", self._on_pointer)
        self.bind("<B1-Motion>", self._on_pointer)
        self._draw()

    def _frac(self) -> float:
        span = self.maxv - self.minv
        return 0.0 if span == 0 else (self.value - self.minv) / span

    def _x_for(self, frac: float) -> float:
        return self.pad + (self.w - 2 * self.pad) * frac

    def _draw(self):
        self.delete("all")
        cy = self.h / 2
        x0, x1 = self.pad, self.w - self.pad
        self.create_line(x0, cy, x1, cy, fill=self.track,
                         width=self.thickness, capstyle=tk.ROUND)
        frac = max(0.0, min(1.0, self._frac()))
        xh = self._x_for(frac)
        if frac > 0.001:
            self.create_line(x0, cy, xh, cy, fill=self.color,
                             width=self.thickness, capstyle=tk.ROUND)
        span = self.maxv - self.minv
        for marker in self.markers:
            mfrac = 0.0 if span == 0 else (marker - self.minv) / span
            mfrac = max(0.0, min(1.0, mfrac))
            xm = self._x_for(mfrac)
            self.create_line(xm, cy - self.dot, xm, cy + self.dot,
                             fill=self.marker_color, width=2)
        self.create_oval(xh - self.dot, cy - self.dot,
                         xh + self.dot, cy + self.dot,
                         fill=self.color, outline="")

    def set(self, value: float, notify: bool = False):
        self.value = max(self.minv, min(self.maxv, value))
        self._draw()
        if notify and self.command:
            self.command(self.value)

    def _on_pointer(self, event):
        usable = self.w - 2 * self.pad
        frac = (event.x - self.pad) / usable if usable > 0 else 0.0
        frac = max(0.0, min(1.0, frac))
        self.set(self.minv + frac * (self.maxv - self.minv), notify=True)


class Switch(tk.Canvas):
    """A capsule toggle: label lives outside, state lives here.

    Replaces three full-width `tk.Button`s that read `Mid/Side Prefilter:
    OFF`. Every shipping audio tool looked at for this (SoundSource, Easy
    Effects, NVIDIA Broadcast, Krisp) uses a switch in a list, and none of
    them paints a bar across the window to say a filter is on — which the
    band-limit button did, making the calmest state in the app the loudest
    thing in it.
    """

    STEPS = 5           # frames of knob travel; ~60 ms total
    STEP_MS = 12

    def __init__(self, parent, *, bg, track, accent, knob_off, knob_on,
                 value=False, command=None, width=40, height=22):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.w, self.h = width, height
        self.track, self.accent = track, accent
        self.knob_off, self.knob_on = knob_off, knob_on
        self.value = bool(value)
        self.command = command
        self._pos = 1.0 if self.value else 0.0
        self._anim = None
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self):
        self.delete("all")
        r = self.h / 2
        fill = self.accent if self._pos > 0.5 else self.track
        round_rect(self, 1, 1, self.w - 1, self.h - 1, r, fill=fill, outline="")
        kr = r - 3
        cx = (1 + r) + (self.w - 2 - 2 * r) * self._pos
        self.create_oval(cx - kr, r - kr, cx + kr, r + kr,
                         fill=self.knob_on if self._pos > 0.5 else self.knob_off,
                         outline="")

    def _on_click(self, _evt=None):
        self.set(not self.value)
        if self.command:
            self.command(self.value)

    def set(self, value: bool, animate: bool = True):
        """Move to `value`. Never fires the command — callers that flip a
        switch programmatically are reporting state, not requesting it."""
        self.value = bool(value)
        target = 1.0 if self.value else 0.0
        if self._anim is not None:
            self.after_cancel(self._anim)
            self._anim = None
        if not animate:
            self._pos = target
            self._draw()
            return
        self._step(target)

    def _step(self, target: float):
        delta = (target - self._pos)
        if abs(delta) < 1e-3:
            self._pos = target
            self._anim = None
            self._draw()
            return
        self._pos += delta / max(1, self.STEPS) * 2
        self._pos = max(0.0, min(1.0, self._pos))
        self._draw()
        self._anim = self.after(self.STEP_MS, lambda: self._step(target))


class PillButton(tk.Canvas):
    """The power control: a rounded pill with a status dot and a word.

    C9 gave the button four states and put its work on a thread; what was
    left was the shape — a flat rectangle with a `●` glyph standing in for a
    light. The states and their colours still come from the caller, because
    the judgement about what "on" means belongs with the engine, not here.
    """

    def __init__(self, parent, *, bg, font, width, height, command=None):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self.w, self.h = width, height
        self.font = font
        self.command = command
        self._enabled = True
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self._state = None
        self._hovering = False

    def _on_click(self, _evt=None):
        if self._enabled and self.command:
            self.command()

    def _hover(self, on: bool):
        self._hovering = on
        if self._state:
            self.paint(*self._state)

    def paint(self, text, fill, fg, dot=None, enabled=True,
              cursor="hand2", outline=""):
        self._state = (text, fill, fg, dot, enabled, cursor, outline)
        self._enabled = enabled
        self.config(cursor=cursor)
        self.delete("all")
        shade = fill
        if enabled and self._hovering:
            shade = _lighten(fill, 0.12)
        round_rect(self, 1, 1, self.w - 1, self.h - 1, self.h / 2,
                   fill=shade, outline=outline or shade)
        x = self.w / 2
        if dot:
            self.create_oval(x - 26, self.h / 2 - 4, x - 18, self.h / 2 + 4,
                             fill=dot, outline="")
            x += 6
        self.create_text(x, self.h / 2, text=text, fill=fg, font=self.font)


class HoldButton(tk.Canvas):
    """Outline pill that reports press and release separately.

    For hold-to-compare: judging whether the model is helping means hearing
    the original *now*, not clicking twice and remembering. `command(True)`
    on press, `command(False)` on release, including when the pointer leaves
    while held — otherwise a drag off the button strands the app in compare.
    """

    def __init__(self, parent, *, bg, fg, border, active_bg, font,
                 text, width, height, command=None):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.w, self.h = width, height
        self.fg, self.border, self.active_bg = fg, border, active_bg
        self.font, self.text = font, text
        self.command = command
        self.held = False
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Leave>", self._release)
        self._draw()

    def _draw(self):
        self.delete("all")
        round_rect(self, 1, 1, self.w - 1, self.h - 1, self.h / 2,
                   fill=self.active_bg if self.held else "",
                   outline=self.border)
        self.create_text(self.w / 2, self.h / 2, text=self.text,
                         fill=self.fg, font=self.font)

    def _press(self, _evt=None):
        if self.held:
            return
        self.held = True
        self._draw()
        if self.command:
            self.command(True)

    def _release(self, _evt=None):
        if not self.held:
            return
        self.held = False
        self._draw()
        if self.command:
            self.command(False)


class Dropdown(tk.Canvas):
    """A filled pill with a chevron, backed by a `tk.Menu`.

    `ttk.Combobox` was styled through `option_add("*TCombobox*Listbox...")`
    incantations that reach into a widget's internals by name and silently
    do nothing when they miss. A `tk.Menu` takes its colours directly, pops
    up where it is told, and cannot be themed out from under us.

    Reads and writes a `StringVar`, so callers that already hold one keep it.
    """

    def __init__(self, parent, *, variable, values=(), bg, field, border, fg,
                 muted, accent, font, width, height=30, command=None):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.w, self.h = width, height
        self.field, self.border, self.fg = field, border, fg
        self.muted, self.accent = muted, accent
        self.font = font
        self.var = variable
        self.values = list(values)
        self.command = command
        self._menu = tk.Menu(self, tearoff=0, bg=field, fg=fg,
                             activebackground=accent, activeforeground=fg,
                             bd=0, relief=tk.FLAT, font=font)
        self.bind("<Button-1>", self._popup)
        self.var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def set_values(self, values) -> None:
        self.values = list(values)

    def _draw(self):
        self.delete("all")
        round_rect(self, 1, 1, self.w - 1, self.h - 1, 6,
                   fill=self.field, outline=self.border)
        pad = 10
        chevron_x = self.w - 14
        text = self._fit(self.var.get() or "—", self.w - pad - 26)
        self.create_text(pad, self.h / 2, text=text, anchor="w",
                         fill=self.fg, font=self.font)
        cy = self.h / 2
        self.create_line(chevron_x - 4, cy - 1, chevron_x, cy + 3,
                         fill=self.muted, width=1.4)
        self.create_line(chevron_x, cy + 3, chevron_x + 4, cy - 1,
                         fill=self.muted, width=1.4)

    def _fit(self, text: str, room: int) -> str:
        """Device descriptions are long and the field is not. Truncate with
        an ellipsis rather than letting the text run under the chevron."""
        probe = self.create_text(0, -50, text=text, font=self.font, anchor="w")
        try:
            while text and self.bbox(probe)[2] - self.bbox(probe)[0] > room:
                text = text[:-1]
                self.itemconfig(probe, text=text + "…")
            return self.itemcget(probe, "text")
        finally:
            self.delete(probe)

    def _popup(self, _evt=None):
        self._menu.delete(0, tk.END)
        for value in self.values:
            self._menu.add_command(
                label=value, command=lambda v=value: self._choose(v))
        if not self.values:
            self._menu.add_command(label="(none available)", state=tk.DISABLED)
        self._menu.tk_popup(self.winfo_rootx(),
                            self.winfo_rooty() + self.h + 2)

    def _choose(self, value: str):
        if value == self.var.get():
            return
        self.var.set(value)
        if self.command:
            self.command(value)


class Meter(tk.Canvas):
    """Two rows of level history: what was captured, above what was emitted.

    One row cannot distinguish a silent session from an idle one, which is
    the open "sometimes no audio at all" report — every other way the audio
    stops writes a status message that names itself (ROADMAP C10).

    Bars are drawn on a dB scale (see db_fraction) with gridlines at -6 and
    -20 dBFS. A linear scale puts music at 5 % of the row and makes any
    annotation beside it wrong.
    """

    GRID_DB = (-6.0, -20.0)

    def __init__(self, parent, *, bg, bars, in_color, out_color, grid,
                 label_color, label_font, width, height, gutter=22):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self.w, self.h = width, height
        self.bars, self.gutter = bars, gutter
        self.in_color, self.out_color = in_color, out_color
        self.grid, self.label_color, self.label_font = grid, label_color, label_font
        self.row_h = (height - 6) / 2

    def render(self, in_levels, out_levels) -> None:
        self.delete("all")
        rows = (("in", in_levels, self.in_color, self.row_h),
                ("out", out_levels, self.out_color, self.h - 2))
        span = self.w - self.gutter
        gap = 3
        bar_w = max(1.0, (span - gap * (self.bars + 1)) / self.bars)
        for label, levels, colour, base in rows:
            for db in self.GRID_DB:
                y = base - self.row_h * ((db - METER_FLOOR_DB) / -METER_FLOOR_DB)
                self.create_line(self.gutter, y, self.w, y, fill=self.grid)
            self.create_text(0, base - self.row_h / 2, text=label, anchor="w",
                             fill=self.label_color, font=self.label_font)
            for i, lvl in enumerate(_right_aligned(levels, self.bars)):
                bar_h = max(1.0, db_fraction(lvl) * self.row_h)
                x0 = self.gutter + gap + i * (bar_w + gap)
                self.create_rectangle(x0, base - bar_h, x0 + bar_w, base,
                                      fill=colour, outline="")


def _right_aligned(levels, n) -> list:
    """Pad on the left so the trace scrolls in from the right rather than
    stretching to fit the width it has."""
    levels = list(levels)
    if len(levels) < n:
        return [0.0] * (n - len(levels)) + levels
    return levels[-n:]


def _lighten(hex_colour: str, amount: float) -> str:
    """Hover shade. Kept here so widgets never need the palette."""
    try:
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return hex_colour
    mix = lambda c: min(255, int(c + (255 - c) * amount))  # noqa: E731
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


# Vertical counterpart of HSlider — used for the main mix fader and the
# Suppression Limit control before both moved to horizontal. Not currently
# wired up anywhere; kept in case a future control wants an up/down drag.
#
# class VSlider(tk.Canvas):
#     """A vertical fader with a round drag handle (ttk.Scale's thumb can't
#     be styled into a circle without image assets, so this draws its own).
#     Drag up to increase, down to decrease."""
#
#     def __init__(self, parent, *, width=26, height=140, color, track, bg,
#                  value=100.0, minv=0.0, maxv=150.0, command=None):
#         super().__init__(parent, width=width, height=height, bg=bg,
#                          highlightthickness=0, bd=0)
#         self.w, self.h = width, height
#         self.color, self.track = color, track
#         self.minv, self.maxv = minv, maxv
#         self.value = value
#         self.command = command
#         self.pad = DOT_RADIUS + 2
#         self.bind("<ButtonPress-1>", self._on_pointer)
#         self.bind("<B1-Motion>", self._on_pointer)
#         self._draw()
#
#     def _frac(self) -> float:
#         span = self.maxv - self.minv
#         return 0.0 if span == 0 else (self.value - self.minv) / span
#
#     def _y_for(self, frac: float) -> float:
#         usable = self.h - 2 * self.pad
#         return (self.h - self.pad) - usable * frac  # frac 0 -> bottom, 1 -> top
#
#     def _draw(self):
#         self.delete("all")
#         cx = self.w / 2
#         y_top, y_bot = self.pad, self.h - self.pad
#         self.create_line(cx, y_bot, cx, y_top, fill=self.track,
#                          width=TRACK_THICKNESS, capstyle=tk.ROUND)
#         frac = max(0.0, min(1.0, self._frac()))
#         yh = self._y_for(frac)
#         if frac > 0.001:
#             self.create_line(cx, y_bot, cx, yh, fill=self.color,
#                              width=TRACK_THICKNESS, capstyle=tk.ROUND)
#         self.create_oval(cx - DOT_RADIUS, yh - DOT_RADIUS,
#                          cx + DOT_RADIUS, yh + DOT_RADIUS,
#                          fill=self.color, outline="")
#
#     def set(self, value: float, notify: bool = False):
#         self.value = max(self.minv, min(self.maxv, value))
#         self._draw()
#         if notify and self.command:
#             self.command(self.value)
#
#     def _on_pointer(self, event):
#         usable = self.h - 2 * self.pad
#         frac = ((self.h - self.pad) - event.y) / usable if usable > 0 else 0.0
#         frac = max(0.0, min(1.0, frac))
#         self.set(self.minv + frac * (self.maxv - self.minv), notify=True)
