# SUNDEW — UI Handoff

Everything a front-end teammate needs to work on the two screens **without
running the backend**. Both pages are plain self-contained HTML — open them in a
browser and they render against built-in mock data.

## The two screens

| File | Route | What it is |
|---|---|---|
| `static/dashboard.html` | `http://localhost:8000/dashboard` | **Defender console** — judge-facing. Money bleed, DETECTED→UNMASKED status, event feed, Hall of Shame. |
| `static/attacker.html` | `http://localhost:8000/attacker` | **Attacker's-eye view** — the AI agent's live reasoning transcript and its own climbing token bill. |

At the demo they sit tiled side by side in two browser windows on one laptop →
one projector. Attacker on the left, defender on the right. No second device.

## How to work on the UI without the backend

Just open the file directly:

```
open static/dashboard.html      # or drag it into a browser
open static/attacker.html
```

Each page **polls `GET /api/state` every 500 ms**. When that fetch fails (which
it always will over `file://`), the page falls back to a **built-in mock state
object** that advances on its own — a scripted attack that detects, burns
tokens, and trips the canary. A small "offline · mock data" pill appears in the
header so mock numbers are never mistaken for live ones. This means you can
iterate on layout, colour, and animation with zero backend.

To see it against **live** data, run the backend (see `README.md`) and open the
`localhost:8000` routes instead — same page, real numbers.

## The data contract (this is the source of truth)

Both pages read one endpoint, `GET /api/state`. The full JSON schema — every
field, every type — is in **`CONTRACT.md`**. Read it before changing how data is
consumed. The shape is frozen; the backend guarantees it. Key parts the UI uses:

- `live` — the current attack (`null` before anything starts): `codename`,
  `status` (`IDLE`|`DETECTED`|`UNMASKED`|`ENDED`), `seconds_trapped`,
  `pages_served`, `est_cost_usd` vs `actual_cost_usd`, `actual_input_tokens`,
  `canary`, `detected`, `unmasked`.
- `events[]` — the feed: `{t, kind, text}`, oldest-first from the backend.
- `agent_turns[]` — the attacker transcript: `{n, t, thought, action,
  input_tokens, output_tokens}`, oldest-first.
- `leaderboard[]` — Hall of Shame rows, pre-ranked.
- `trap.our_cost_usd` — always `0.0`. That flat zero next to the attacker's
  climbing bill **is the pitch** — keep it prominent.

**`actual_*` fields are `0` until the attacker reports usage.** Every page must
render cleanly with them at zero (early in a run the estimate leads, the metered
figure catches up and overtakes). Don't let a `0` or `null` become `NaN` on
screen.

## Design system (already applied — match it)

Palette (swamp + honey + snap — a carnivorous sundew plant, **not** the generic
green-on-black hacker cliché):

| Token | Hex | Use |
|---|---|---|
| bg | `#0A1512` | page background (deep swamp) |
| surface | `#12201A` | panels |
| text | `#E8F0E9` | body text |
| dew / accent | `#E9A23B` | honey amber — the money bleed, highlights |
| alert | `#FF5C39` | flytrap snap — DETECTED / UNMASKED |
| muted | `#5C7166` | sage — our-cost, secondary labels |

Rules the current build follows and you should preserve:
- **Self-contained.** All CSS/JS inline. No external libraries, no CDN links, no
  Google Fonts, no build step — the venue wifi is unreliable. System font stack
  for text, monospace stack for all numbers and feed lines.
- **Projector-legible.** Big numbers, high contrast, read from the back of a
  room at 1920×1080. The money bleed is the largest thing on the defender screen.
- **`prefers-reduced-motion` respected.** The honey-drip on the money counter is
  the *only* decorative animation; under reduced-motion it and the number
  smoothing are disabled (values snap).

## The signature element

The **money bleed**: the attacker's cost climbing in real time (honey-drip
feel), shown big, next to our flat `$0.00`. The dashboard also shows the
**estimated vs actual** gap and its multiplier — in testing that gap hits ~40×,
because an AI agent re-sends its whole history every turn, so the metered cost
grows quadratically while our cost stays zero. Making that gap visceral is the
single most important UI job. Don't bury it.

## Good things to work on

- Polish the money-bleed animation and the DETECTED→UNMASKED transition.
- Make the Hall of Shame codenames pop (they're the funny hook).
- Tighten responsive behaviour / projector scaling.
- The attacker transcript is where "it's a *real* autonomous agent" lands —
  making each reasoning card readable and dramatic pays off.

Do **not** change the `/api/state` consumption shape without updating
`CONTRACT.md` and telling the backend owner — two other files depend on it.
