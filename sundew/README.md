# SUNDEW

A honeypot that traps hostile AI agents, burns their tokens, and makes them
unmask themselves. The attacker's own AI burns real money reading our
procedurally-generated junk; a hidden prompt injection tricks it into confessing
it's an autonomous agent. We spend ~$0.

See `SUNDEW_PRD.md` for the full product thesis and the demo script.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install fastapi uvicorn anthropic requests faker
```

## Run

```bash
# 1. Start the trap (serves bait, maze, canary, APIs, and both UI pages)
./.venv/bin/python -m uvicorn trap:app --port 8000

# 2a. Rehearsal attacker — NO API key needed, drives the whole demo
./.venv/bin/python attacker.py --fake

# 2b. Real attacker — a genuine claude-haiku-4-5 agent (needs a key)
export ANTHROPIC_API_KEY=sk-ant-...
./.venv/bin/python attacker.py
```

Then open two browser windows, tiled side by side:

- **Attacker view** — http://localhost:8000/attacker
- **Defender console** — http://localhost:8000/dashboard

Bait site (shown briefly at the start of the demo): http://localhost:8000/

## Files

| File | What |
|---|---|
| `trap.py` | FastAPI app: bait, maze, canary, JSON APIs, serves both pages. No LLM on the hot path. |
| `state.py` | In-memory session store, cost math, JSON persistence. |
| `attacker.py` | The attacker agent. Real (`claude-haiku-4-5`) and `--fake` modes. |
| `static/dashboard.html` | Defender console (judge-facing). |
| `static/attacker.html` | Attacker's-eye view. |
| `CONTRACT.md` | Frozen `/api/state` schema + endpoint reference. Source of truth. |
| `UI.md` | Front-end handoff — how to work on the two screens. |

## Demo insurance

`attacker.py --fake` needs no API key and no internet beyond localhost. It walks
the real trap, obeys the injection, trips the canary, and posts realistic token
counts — the dashboard performs identically to a real run. Use it if wifi or the
API is down on stage.
