# HackthonCyberSec

Hackathon entry, presented as **I AM NIGERIAN PRINCE**. A scambaiting rig that
answers fraud calls in a clone of your voice, then gradually turns into somebody
else.

## Running it

This repo is the framing layer. The engine it drives lives in a separate ~8GB
project and is **not** included here — clone or place it alongside, then link it:

```
ln -s /path/to/decoy engine
./run.sh
```

Opens two pages on `localhost:8080`:

| page | what it is |
|---|---|
| `/pitch` | the pitch — problem, method, measurements, limits. **Show this to judges.** |
| `/` | the live operator console — transcript, meters, controls |

## The 60-second demo

1. `./run.sh`, browser opens
2. Click **▶ HEAR MY VOICE** — "this is a clone of me, from 45 seconds of audio"
3. **TAKE OVER**, then **▶ START CALL** (pick *HMRC — arrest warrant*)
4. Watch the detector escalate. It hit **35% → 95%** in two turns on the last run
5. Point at the moment it's asked for a six-digit code and produces an obstacle instead
6. Around turn three: pick a character, **set 25 seconds**, **START MORPH**
7. Voice drifts. The name under the bar updates as it goes

Set the morph to 25s, not the 90s default — the first quarter is *deliberately*
imperceptible, and a demo audience needs to hear the change while watching.

## Structure

```
HackthonCyberSec/
  web/pitch.html             the judge-facing page
  web/design-reference.html  layout/type reference for the pitch
  engine -> ~/decoy          the system itself (symlink, gitignored)
  run.sh                     starts everything
  Anti-phishing overlay app.pdf
```

The engine is a separate, self-contained project. This repo is the hackathon
framing around it — which is also why the pitch page lives here and not in the
engine: the presentation layer can be rewritten for a different audience without
touching the thing that works.

## What to say when asked "what's actually novel here"

**The morph is latent-space, not a crossfade.** Crossfading two voices sounds
exactly like crossfading two voices. This interpolates inside XTTS-v2's
conditioning tensors and synthesises once, so every word comes from a single
coherent voice sitting between two speakers. Slerp on the speaker embedding —
lerp collapses the midpoint's magnitude and it comes out thin and underwater.
Measured: slerp midpoint error `0.000` vs lerp `7.358`.

**The behavioural drift matters more than the acoustic one.** A perfect clone of
your voice announcing it's a barrister fools nobody. Each character defines four
bands and the system prompt is rebuilt every turn from where the morph sits, so
the vocabulary shifts before the timbre does.

**It's all local.** Silero + faster-whisper + qwen2.5:7b + XTTS-v2 on one
laptop, ~9GB VRAM, ~1.3s end to end. No cloud inference, no API keys, no audio
leaving the machine.

## If a judge pushes on safety

They should. The honest answers:

- **Voice cloning is self-only.** Cloning a third party to speak as them is
  impersonation, and a deepfaked voice used to obtain money is fraud.
- **It cannot emit a credential.** No card numbers, sort codes, one-time codes,
  addresses or dates of birth. Enforced in the prompt, checked in tests.
- **A human presses the button.** Never auto-answers, never auto-hangs-up — a
  false positive on a real call from your GP is worse than sitting through
  thirty seconds of a scam.
- **There's an output guard.** During testing the local 7B produced a sexual
  insult mid-call and it was spoken aloud. Prompt rules lower that rate; they
  don't eliminate it. Every spoken word now passes through one blocklist choke
  point that substitutes an in-character stall.
- **Defensive only.** Point it at calls that come to you, not outbound.
- **Recording consent varies** — England and Wales is one-party; much of the EU
  and several US states are not.

## Known limits, stated up front

- Whisper mishears proper nouns on 8kHz telephony audio ("HMRC" → "ANRC"). The
  detector scores nine independent categories so one garbled acronym doesn't
  matter — it still hit 95% on the next sentence.
- The speech guard is a blocklist: strong on what it knows, silent on what it
  doesn't. For a public demo, `plain` delivery is the least improvisational
  setting.
- Live phone calls need a Twilio relay (~20 min of setup). The speakerphone
  bridge demos the whole system without it.

## Engine docs

See `engine/README.md` for setup, architecture, the Twilio path, and how to
write a new character.
