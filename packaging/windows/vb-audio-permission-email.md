# Draft: VB-Audio redistribution permission request

Not sent by anyone yet — this is a draft for a human to review, edit, and send
via VB-Audio's own contact form (https://vb-audio.com — "Contact" / licensing
page) or the address given there. Do not send as-is without reading it.

Their licensing page states that bundling/distributing VB-CABLE beyond
personal use, or volume deployment (>10 units), requires contacting them for
an agreement — see https://vb-audio.com/Services/licensing.htm. This email is
that contact.

---

**Subject:** Redistribution permission request — free open-source app, VB-CABLE as an optional Windows dependency

Hello,

I maintain Music Assassin Live (https://github.com/A-Ahmad-02/music-assassin-live),
a free, open-source (MIT-licensed) desktop application that removes background
noise/music from whatever audio a Windows machine is playing, in real time. It
is not sold, has no paid tier, and is distributed at no cost.

On Linux, the app uses PipeWire's native trap-sink routing to intercept and
replace the system's default audio output. On Windows there is no equivalent
built into the OS, so we'd like to use VB-CABLE to provide that same routing
role: a virtual output device the app can set as the Windows default, capture
from, process, and play back out the real device.

We'd like to bundle the official, unmodified VB-CABLE installer inside our
own Windows installer and run it silently during setup, so a non-technical
user gets a single-click install rather than a separate manual step. Before
doing that, per your licensing page's note on volume/distribution deals, I
wanted to ask directly:

1. Would you grant permission to bundle and silently install the unmodified
   VB-CABLE installer as part of our free, open-source app's Windows
   installer?
2. Is there attribution, a version pin, or any other condition you'd want
   in place for that?
3. If a no-cost grant isn't something you offer for open-source/freeware
   projects, what would a distribution agreement look like for a project
   at this scale (currently pre-release, unknown install volume)?

Happy to link to VB-Audio in the app's credits/about screen and documentation
either way. Thanks for making VB-CABLE available — it's the only practical
option we found for this on Windows.

Best,
[your name]
[project URL]
[contact email]

---

## If they say no / don't respond

Fall back to the documented, unbundled path: ship the app without VB-CABLE,
and have first-run setup detect its absence and open
https://vb-audio.com/Cable/ for the user to install manually — same shape as
the Linux build depending on PipeWire being present rather than shipping it.
The Inno Setup script in this directory has a toggle for this
(`BundleVBCable`) — see `music-assassin-live.iss`.
