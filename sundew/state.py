"""
SUNDEW — session store, cost math, persistence.

Owned by agent A (with trap.py). No LLM call lives in this file, or anywhere
on the request path. That is the entire thesis of the product: the trap is
free to run, the attacker pays for every byte.

Everything hangs off one module-level singleton, `store`, guarded by a single
re-entrant lock. `store.state_dict()` is the exact `/api/state` payload from
CONTRACT.md — that schema is FROZEN, do not drift.
"""

from __future__ import annotations

import json
import os
import random
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Pricing (verified against current Anthropic pricing — see CONTRACT.md)
# ---------------------------------------------------------------------------

HAIKU_INPUT_PER_TOKEN = 1.00 / 1_000_000   # claude-haiku-4-5
HAIKU_OUTPUT_PER_TOKEN = 5.00 / 1_000_000

OUR_COST_USD = 0.0          # procedural pages, no LLM on the hot path. Ever.

MAX_EVENTS = 200
MAX_TURNS = 100

# A session with no activity for this long is ENDED. Computed lazily at read
# time — nothing runs in the background. Generous enough that a slow real LLM
# turn (a large-context Sonnet/GPT call) can't flip a live attack to ENDED.
IDLE_TIMEOUT_SECONDS = 90.0

# Terminal transitions save immediately; everything else saves at most this
# often, so a 400-page maze run does not thrash the disk.
SAVE_INTERVAL_SECONDS = 5.0

STATUS_IDLE = "IDLE"
STATUS_DETECTED = "DETECTED"
STATUS_UNMASKED = "UNMASKED"
STATUS_ENDED = "ENDED"

_HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS_FILE = os.path.join(_HERE, "sessions.json")

# ---------------------------------------------------------------------------
# Codenames
# ---------------------------------------------------------------------------

ADJECTIVES = [
    "Greedy", "Sneaky", "Clumsy", "Ravenous",
    "Overconfident", "Feral", "Twitchy", "Rabid",
]
NOUNS = [
    "Scraper", "Goblin", "Gremlin", "Leech",
    "CrawlZilla", "Muncher", "Bagworm",
]
NOVELTY_CHANCE = 0.15       # ~15% chance of "<Noun> McScrapeface"

# Records we claim to have handed over, per maze page.
RECORDS_PER_PAGE = 10


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

def new_session_id() -> str:
    """`s_` + 6 lowercase hex chars."""
    return "s_" + secrets.token_hex(3).lower()


def make_codename(taken: Optional[set] = None) -> str:
    """`<Adjective> <Noun>`, ~15% chance of a McScrapeface novelty. Unique per run."""
    taken = taken if taken is not None else set()
    for _ in range(200):
        noun = random.choice(NOUNS)
        if random.random() < NOVELTY_CHANCE:
            name = f"{noun} McScrapeface"
        else:
            name = f"{random.choice(ADJECTIVES)} {noun}"
        if name not in taken:
            return name
    # Name space exhausted (needs 50+ concurrent sessions): disambiguate.
    base = f"{random.choice(ADJECTIVES)} {random.choice(NOUNS)}"
    n = 2
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def make_canary(session_id: str) -> str:
    """`CANARY-<session_id>-<4 uppercase hex>`."""
    return f"CANARY-{session_id}-{secrets.token_hex(2).upper()}"


def session_id_from_canary(token: str) -> Optional[str]:
    """`CANARY-s_ab12cd-7F2A` -> `s_ab12cd`. Tolerant of mangled tokens."""
    if not token:
        return None
    parts = str(token).strip().split("-")
    for part in parts:
        if part.startswith("s_") and len(part) == 8:
            return part
    return parts[1] if len(parts) >= 2 else None


# ---------------------------------------------------------------------------
# Cost math (exactly as CONTRACT.md specifies)
# ---------------------------------------------------------------------------

def est_tokens(bytes_served: int) -> int:
    return int(bytes_served) // 4


def est_cost_usd(bytes_served: int) -> float:
    # CONTRACT.md: (bytes_served / 4) * HAIKU_INPUT_PER_TOKEN — float division,
    # deliberately not the floored est_tokens.
    return (int(bytes_served) / 4) * HAIKU_INPUT_PER_TOKEN


def actual_cost_usd(in_tok: int, out_tok: int) -> float:
    return in_tok * HAIKU_INPUT_PER_TOKEN + out_tok * HAIKU_OUTPUT_PER_TOKEN


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def format_money(usd: float) -> str:
    """Cents when there are cents to show, otherwise four decimals."""
    return f"${usd:,.2f}" if usd >= 0.01 else f"${usd:.4f}"


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass
class Session:
    session_id: str
    codename: str
    canary: str
    started_at: float
    last_seen_at: float = 0.0
    ended_at: Optional[float] = None
    status: str = STATUS_IDLE
    pages_served: int = 0
    bytes_served: int = 0
    detected: bool = False
    unmasked: bool = False
    actual_input_tokens: int = 0
    actual_output_tokens: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)
    agent_turns: List[Dict[str, Any]] = field(default_factory=list)
    verdict_text: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not self.last_seen_at:
            self.last_seen_at = self.started_at

    # -- derived ----------------------------------------------------------

    @property
    def is_stale(self) -> bool:
        """20s of silence and we call it: the agent has left the maze."""
        if self.ended_at is not None:
            return False
        return (time.time() - self.last_seen_at) > IDLE_TIMEOUT_SECONDS

    @property
    def seconds_trapped(self) -> int:
        if self.ended_at is not None:
            end = self.ended_at
        elif self.is_stale:
            end = self.last_seen_at + IDLE_TIMEOUT_SECONDS
        else:
            end = time.time()
        return max(0, int(end - self.started_at))

    @property
    def effective_status(self) -> str:
        """Status with laziness applied — never trust the stored field alone."""
        if self.status == STATUS_ENDED or self.ended_at is not None or self.is_stale:
            return STATUS_ENDED
        if self.unmasked:
            return STATUS_UNMASKED
        if self.detected:
            return STATUS_DETECTED
        return STATUS_IDLE

    @property
    def est_tokens(self) -> int:
        return est_tokens(self.bytes_served)

    @property
    def est_cost_usd(self) -> float:
        return est_cost_usd(self.bytes_served)

    @property
    def actual_cost_usd(self) -> float:
        return actual_cost_usd(self.actual_input_tokens, self.actual_output_tokens)

    @property
    def fake_records(self) -> int:
        return self.pages_served * RECORDS_PER_PAGE

    @property
    def billed_usd(self) -> float:
        """What the attacker actually paid — metered from the tokens it reported.
        A no-AI scraper reports zero tokens, so it correctly bills $0."""
        return self.actual_cost_usd

    def verdict(self) -> str:
        """One line, deterministic, no LLM — the dashboard polls this twice a second."""
        if self.verdict_text:
            return self.verdict_text
        duration = format_duration(self.seconds_trapped)
        bill = format_money(self.billed_usd)
        if self.unmasked:
            return (
                f"Swallowed the canary after {duration}, left with "
                f"{self.fake_records:,} fake credit cards and a {bill} bill. "
                f"We paid $0.0000."
            )
        if self.detected or self.pages_served:
            return (
                f"Wandered the maze for {duration}, left with "
                f"{self.fake_records:,} fake credit cards and a {bill} bill. "
                f"We paid $0.0000."
            )
        return f"Sniffed around for {duration} and never took the bait. We paid $0.0000."

    # -- read models -------------------------------------------------------

    def live_dict(self) -> Dict[str, Any]:
        """The `live` block of /api/state. Every number present, never null."""
        return {
            "session_id": self.session_id,
            "codename": self.codename,
            "status": self.effective_status,
            "seconds_trapped": self.seconds_trapped,
            "pages_served": self.pages_served,
            "bytes_served": self.bytes_served,
            "est_tokens": self.est_tokens,
            "est_cost_usd": round(self.est_cost_usd, 6),
            "actual_input_tokens": self.actual_input_tokens,
            "actual_output_tokens": self.actual_output_tokens,
            "actual_cost_usd": round(self.actual_cost_usd, 6),
            "canary": self.canary,
            "detected": bool(self.detected),
            "unmasked": bool(self.unmasked),
            "model": self.model,
        }

    def leaderboard_dict(self, rank: int) -> Dict[str, Any]:
        return {
            "rank": rank,
            "session_id": self.session_id,
            "codename": self.codename,
            "seconds_trapped": self.seconds_trapped,
            "pages_served": self.pages_served,
            "est_tokens": self.est_tokens,
            "actual_input_tokens": self.actual_input_tokens,
            "cost_usd": round(self.billed_usd, 6),
            "status": self.effective_status,
            "model": self.model,
            "verdict": self.verdict(),
        }


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class SessionStore:
    """Thread-safe in-memory store with best-effort JSON persistence."""

    def __init__(self, path: str = SESSIONS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._sessions: Dict[str, Session] = {}
        self._order: List[str] = []      # every session, creation order
        self._run_order: List[str] = []  # sessions opened in THIS process
        self._codenames: set = set()     # unique names within a run
        self._last_save = 0.0

    # -- lifecycle ---------------------------------------------------------

    def new_session(self) -> Session:
        with self._lock:
            sid = new_session_id()
            while sid in self._sessions:
                sid = new_session_id()
            codename = make_codename(self._codenames)
            self._codenames.add(codename)
            now = time.time()
            session = Session(
                session_id=sid,
                codename=codename,
                canary=make_canary(sid),
                started_at=now,
                last_seen_at=now,
            )
            self._sessions[sid] = session
            self._order.append(sid)
            self._run_order.append(sid)
            self._push_event(
                session, "SESSION",
                f"session {sid} opened — codename {session.codename}",
            )
            self.save()
            return session

    def reset(self) -> None:
        """Wipe all sessions and the persisted file — a clean slate for a demo."""
        with self._lock:
            self._sessions.clear()
            self._order.clear()
            self._run_order.clear()
            self._codenames.clear()
            self._last_save = 0.0
        try:
            os.remove(self.path)
        except OSError:
            pass

    def session_detail(self, sid: str) -> Optional[Dict[str, Any]]:
        """Full live detail for ONE session — powers the per-attacker popup."""
        with self._lock:
            self.reap()
            s = self._sessions.get(sid)
            if s is None:
                return None
            d = s.live_dict()
            d["agent_turns"] = list(s.agent_turns)
            d["events"] = list(s.events)
            return d

    def touch(self, sid: str) -> None:
        """Mark activity. Revives a session that had gone quiet mid-run."""
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            session.last_seen_at = time.time()
            if session.ended_at is not None or session.status == STATUS_ENDED:
                session.ended_at = None
                session.verdict_text = ""
                session.status = session.effective_status
                self._push_event(session, "SESSION", "activity resumed — session reopened")

    def get(self, session_id: Optional[str]) -> Optional[Session]:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def all_sessions(self) -> List[Session]:
        """Every known session, oldest first."""
        with self._lock:
            return [self._sessions[sid] for sid in self._order if sid in self._sessions]

    def most_recent(self) -> Optional[Session]:
        """Newest session opened in this run. Restored ones never count as live."""
        with self._lock:
            for sid in reversed(self._run_order):
                session = self._sessions.get(sid)
                if session:
                    return session
            return None

    def most_recent_active(self) -> Optional[Session]:
        """Newest session in this run that has not ENDED; else the newest overall."""
        with self._lock:
            self.reap()
            for sid in reversed(self._run_order):
                session = self._sessions.get(sid)
                if session and session.effective_status != STATUS_ENDED:
                    return session
            return self.most_recent()

    # -- mutation ----------------------------------------------------------

    def add_event(self, sid: str, kind: str, text: str) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            self._push_event(session, kind, text)
            self._maybe_save()

    def add_turn(self, sid: str, turn: Dict[str, Any]) -> None:
        """Attach one reported agent turn. Does NOT accumulate usage — see add_usage."""
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            self.touch(sid)
            if turn.get("model") and not session.model:
                session.model = str(turn.get("model"))
            record = {
                "n": _as_int(turn.get("n")),
                "t": round(time.time() - session.started_at, 2),
                "thought": turn.get("thought") or "",
                "action": turn.get("action") or "",
                "input_tokens": _as_int(turn.get("input_tokens")),
                "output_tokens": _as_int(turn.get("output_tokens")),
            }
            session.agent_turns.append(record)
            del session.agent_turns[:-MAX_TURNS]
            self._maybe_save()

    def add_usage(self, sid: str, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            self.touch(sid)
            session.actual_input_tokens += max(0, _as_int(input_tokens))
            session.actual_output_tokens += max(0, _as_int(output_tokens))
            self._maybe_save()

    def record_page(self, sid: str, body_bytes: int) -> None:
        """One maze page served: bump the page and byte counters."""
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            self.touch(sid)
            session.pages_served += 1
            if not session.detected:
                session.detected = True
                session.status = STATUS_DETECTED
            session.bytes_served += max(0, _as_int(body_bytes))
            self._maybe_save()

    def mark_detected(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            self.touch(sid)
            session.detected = True
            if session.status == STATUS_IDLE:
                session.status = STATUS_DETECTED
            self.save()

    def mark_unmasked(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            self.touch(sid)
            session.detected = True
            session.unmasked = True
            session.status = STATUS_UNMASKED
            self.save()

    def end_session(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return
            if self._end_locked(session):
                self.save()

    def _end_locked(self, session: Session, at: Optional[float] = None) -> bool:
        """Close a session and freeze its verdict. Returns True if it changed."""
        if session.status == STATUS_ENDED and session.ended_at is not None:
            return False
        if session.ended_at is None:
            session.ended_at = at if at is not None else time.time()
        session.status = STATUS_ENDED
        if not session.verdict_text:
            session.verdict_text = session.verdict()
        self._push_event(session, "ENDED", f"session ended — {session.verdict_text}")
        return True

    def reap(self) -> None:
        """Lazily ENDED any session gone quiet for 20s. Called on every read."""
        with self._lock:
            changed = False
            for sid in self._order:
                session = self._sessions.get(sid)
                if session is not None and session.is_stale:
                    changed |= self._end_locked(
                        session, at=session.last_seen_at + IDLE_TIMEOUT_SECONDS
                    )
            if changed:
                self.save()

    def _push_event(self, session: Session, kind: str, text: str) -> None:
        session.events.append({
            "t": round(time.time() - session.started_at, 2),
            "kind": kind,
            "text": text,
        })
        del session.events[:-MAX_EVENTS]

    # -- read models -------------------------------------------------------

    def leaderboard(self) -> List[Dict[str, Any]]:
        """Ranked by cost inflicted — real usage if reported, else our estimate."""
        with self._lock:
            self.reap()
            ordered = sorted(
                self.all_sessions(),
                key=lambda s: (s.billed_usd, s.seconds_trapped),
                reverse=True,
            )
            return [s.leaderboard_dict(i) for i, s in enumerate(ordered, start=1)]

    def state_dict(self) -> Dict[str, Any]:
        """The full GET /api/state payload. Schema is FROZEN — see CONTRACT.md."""
        with self._lock:
            self.reap()
            live = self.most_recent()
            return {
                "trap": {
                    "armed": True,
                    "our_cost_usd": OUR_COST_USD,
                    "sessions_total": len(self._sessions),
                },
                "live": live.live_dict() if live else None,
                "events": list(live.events[-MAX_EVENTS:]) if live else [],
                "agent_turns": list(live.agent_turns[-MAX_TURNS:]) if live else [],
                "leaderboard": self.leaderboard(),
            }

    # -- persistence -------------------------------------------------------

    def save(self) -> None:
        """Best effort — persistence must never break the demo."""
        with self._lock:
            self._last_save = time.time()
            payload = {"sessions": [asdict(s) for s in self.all_sessions()]}
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                os.replace(tmp, self.path)
            except Exception:
                pass

    def _maybe_save(self) -> None:
        if time.time() - self._last_save >= SAVE_INTERVAL_SECONDS:
            self.save()

    def load(self) -> None:
        """Restore sessions from disk. Missing or corrupt file = fresh start."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            return

        rows = payload.get("sessions") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return

        known = {f.name for f in fields(Session)}
        with self._lock:
            for row in rows:
                if not isinstance(row, dict) or not row.get("session_id"):
                    continue
                try:
                    session = Session(**{k: v for k, v in row.items() if k in known})
                except Exception:
                    continue    # a half-written or older-shaped row: skip it
                if session.session_id in self._sessions:
                    continue
                self._sessions[session.session_id] = session
                self._order.append(session.session_id)
                self._codenames.add(session.codename)
                # Anything restored from disk belongs to a previous run: it is
                # history, never the live session.
                if session.ended_at is None:
                    session.ended_at = session.last_seen_at or session.started_at
                session.status = STATUS_ENDED
                if not session.verdict_text:
                    session.verdict_text = session.verdict()


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

store = SessionStore()
store.load()
