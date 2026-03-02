"""Session store for Telegram-initiated Claude conversations.

Maintains a list of named sessions in telegram_sessions.json so the
user can pick a conversation by name instead of raw UUID.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_FILE = Path(__file__).parent / "telegram_sessions.json"
MAX_SESSIONS = 10  # Keep only the N most recently used


def _load() -> list[dict]:
    try:
        return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(sessions: list[dict]) -> None:
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2), encoding="utf-8")


def all_sessions() -> list[dict]:
    """Return sessions sorted by last_used (most recent first)."""
    return sorted(_load(), key=lambda s: s.get("last_used", ""), reverse=True)


def get(session_id: str) -> dict | None:
    return next((s for s in _load() if s["id"] == session_id), None)


def upsert(session_id: str, name: str | None = None) -> None:
    """Add a new session or update last_used on an existing one."""
    sessions = _load()
    existing = next((s for s in sessions if s["id"] == session_id), None)

    now = datetime.now(timezone.utc).isoformat()

    if existing:
        existing["last_used"] = now
        if name:
            existing["name"] = name
    else:
        sessions.append({
            "id": session_id,
            "name": name or "New conversation",
            "last_used": now,
        })

    # Keep only the most recent MAX_SESSIONS
    sessions = sorted(sessions, key=lambda s: s.get("last_used", ""), reverse=True)
    _save(sessions[:MAX_SESSIONS])


def rename(session_id: str, new_name: str) -> bool:
    """Rename a session. Returns True if found."""
    sessions = _load()
    for s in sessions:
        if s["id"] == session_id:
            s["name"] = new_name
            _save(sessions)
            return True
    return False


def remove(session_id: str) -> None:
    sessions = [s for s in _load() if s["id"] != session_id]
    _save(sessions)
