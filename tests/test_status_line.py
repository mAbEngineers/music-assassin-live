#!/usr/bin/env python3
"""The status line's health word (ROADMAP C4). No Tk, no audio.

The line used to be a counter dump — `blocks: 48213  fallbacks: 0  xruns: 2`
— which is diagnostic gold and a poor answer to the only question it is asked
most of the time, "is this working". The counters moved behind a details
toggle and the line now states device, measured latency and health.

Health is the part with a judgement in it, so it is the part tested. The
failure that matters: reporting "healthy" while every block is falling back
to dry, i.e. while the user is hearing unprocessed audio and being told
everything is fine.

Built with object.__new__ rather than App(), because constructing the real
app builds Tk widgets and takes over a display; the health decision needs
none of that.

Run: .venv/bin/python tests/test_status_line.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.ui.app import AMBER, ON_COLOR, RED, App  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class Stats:
    def __init__(self, fallback_blocks=0, xruns=0):
        self.fallback_blocks, self.xruns = fallback_blocks, xruns


class Engine:
    def __init__(self, stream_ok=True, lag_state="measured", lag_ms=50.0):
        self.stream_ok = stream_ok
        self.lag_state = lag_state
        self.processor_lag_ms = lag_ms


def app(prev_fallbacks=0, prev_xruns=0, stream_ok=True, engine=True):
    a = object.__new__(App)
    a.engine = Engine(stream_ok) if engine else None
    a._prev_fallbacks, a._prev_xruns = prev_fallbacks, prev_xruns
    return a


def test_the_lag_readout_distinguishes_its_three_states():
    """`unmeasured` and a measured zero used to be the same reading, which
    is why the doubling report could not say which had happened (ROADMAP
    B6b). They are different answers and the panel now says which."""
    print("\nprocessor lag readout")
    a = app()
    check("a measurement reads as one", a._lag_text() == "50.0 ms", a._lag_text())

    a.engine = Engine(lag_state="measuring")
    check("still deciding says so", "measuring" in a._lag_text(), a._lag_text())

    a.engine = Engine(lag_state="unmeasured", lag_ms=None)
    txt = a._lag_text()
    check("giving up is not reported as a lag of zero",
          "unmeasured" in txt and "0" not in txt, txt)
    check("and says what that cost", "bypassed" in txt, txt)

    a.engine = None
    check("no engine, no claim", a._lag_text() == "—", a._lag_text())


def test_levels_read_as_dbfs():
    """C10's two rows are only useful if the numbers under them are, and the
    distinction being drawn is signal vs none — so silence gets a word, not
    a -inf."""
    print("\nlevel readout")
    check("no data yet", App._level_db([]) == "—", App._level_db([]))
    check("silence is named", App._level_db([0.0]) == "silent", App._level_db([0.0]))
    check("full scale is 0 dB", App._level_db([1.0]).startswith("0.0"),
          App._level_db([1.0]))
    half = App._level_db([0.5])
    check("half scale is about -6 dB", half.startswith("-6.0"), half)
    check("the most recent value wins", App._level_db([1.0, 0.5]) == half,
          App._level_db([1.0, 0.5]))


def test_healthy():
    print("\nnothing wrong")
    word, colour = app()._health(Stats())
    check("says healthy", word == "healthy", word)
    check("in green", colour == ON_COLOR)


def test_a_dead_stream_outranks_everything():
    print("\nstream stopped")
    word, colour = app(stream_ok=False)._health(Stats(fallback_blocks=999, xruns=999))
    check("reports the stream, not the symptoms", word == "stream stopped", word)
    check("in red", colour == RED)


def test_falling_back_to_dry_is_not_healthy():
    """The failure this word exists to catch: audio is flowing, the stream is
    alive, nothing has crashed — and the filter is not actually filtering."""
    print("\nworker cannot keep up")
    a = app(prev_fallbacks=0)
    word, colour = a._health(Stats(fallback_blocks=App.FALLBACK_WARN_PER_TICK))
    check("does not claim healthy", word != "healthy", word)
    check("says how many blocks went dry", "blocks dry" in word, word)
    check("in amber, not red — degraded, not broken", colour == AMBER)

    below = app(prev_fallbacks=0)._health(
        Stats(fallback_blocks=App.FALLBACK_WARN_PER_TICK - 1))[0]
    check("a handful below the threshold is still healthy", below == "healthy", below)


def test_counters_are_deltas_not_totals():
    """A long session accumulates fallbacks and xruns forever. Reading the
    totals would mean the line degrades permanently after one hiccup an hour
    ago and never recovers, which trains people to ignore it."""
    print("\ndeltas, not totals")
    a = app(prev_fallbacks=10_000, prev_xruns=250)
    word, _ = a._health(Stats(fallback_blocks=10_000, xruns=250))
    check("huge lifetime totals, nothing new this tick -> healthy",
          word == "healthy", word)

    word2, _ = app(prev_xruns=250)._health(Stats(xruns=252))
    check("two new xruns are reported", "2 xrun" in word2, word2)


def main() -> int:
    print("status line health tests (no Tk, no audio)")
    test_healthy()
    test_a_dead_stream_outranks_everything()
    test_falling_back_to_dry_is_not_healthy()
    test_counters_are_deltas_not_totals()
    test_the_lag_readout_distinguishes_its_three_states()
    test_levels_read_as_dbfs()
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
