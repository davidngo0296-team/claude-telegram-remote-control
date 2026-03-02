"""Send a one-way Telegram notification from a Claude Code hook.

Called by Stop and Notification hooks. Reads hook JSON from stdin,
sends a formatted message to Telegram, then exits cleanly.

Usage (set in settings.json):
    python notify.py stop
    python notify.py notification
"""

import json
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError


SESSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_sessions.json")


def get_session_name(session_id: str) -> str:
    """Look up a stored session name from telegram_sessions.json."""
    try:
        sessions = json.loads(open(SESSIONS_FILE, encoding="utf-8").read())
        for s in sessions:
            if s.get("id") == session_id:
                return s.get("name", "")
    except Exception:
        pass
    return ""


def register_session_if_new(session_id: str, name: str) -> None:
    """Add session to telegram_sessions.json if not already present."""
    if not session_id or not name:
        return
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        try:
            sessions = json.loads(open(SESSIONS_FILE, encoding="utf-8").read())
        except Exception:
            sessions = []
        if any(s.get("id") == session_id for s in sessions):
            return  # already registered — don't overwrite a custom name
        sessions.append({"id": session_id, "name": name, "last_used": now})
        sessions = sorted(sessions, key=lambda s: s.get("last_used", ""), reverse=True)[:10]
        open(SESSIONS_FILE, "w", encoding="utf-8").write(json.dumps(sessions, indent=2))
    except Exception:
        pass


def load_config() -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.env")
    cfg = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg


def send(token: str, chat_id: str, text: str, reply_markup: dict = None) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    req = Request(url, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"})
    urlopen(req, timeout=10)


def session_label(hook: dict) -> str:
    """Return a short human-readable session identifier.

    Priority:
      1. Name stored in telegram_sessions.json (set via /rename or Telegram-initiated)
      2. First user message from transcript
      3. Last 8 chars of session UUID
    """
    session_id = hook.get("session_id", "")
    short_id = f"`…{session_id[-8:]}`" if session_id else "`unknown`"

    # 1. Check sessions store for a saved name
    if session_id:
        name = get_session_name(session_id)
        if name:
            return f"{short_id} _{name}_"

    # 2. Fall back to first user message from transcript (Stop/Notification hooks include it)
    first_prompt = ""
    transcript = hook.get("transcript", [])
    for msg in transcript:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    first_prompt = block.get("text", "")
                    break
        elif isinstance(content, str):
            first_prompt = content
        if first_prompt:
            break

    # 3. If transcript empty, read from Claude's session JSONL file
    if not first_prompt and session_id:
        import glob as _glob
        home = os.path.expanduser("~")
        pattern = os.path.join(home, ".claude", "projects", "**", f"{session_id}.jsonl")
        for jsonl_path in _glob.glob(pattern, recursive=True):
            try:
                with open(jsonl_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        obj = json.loads(line)
                        msg = obj.get("message", obj)
                        if msg.get("role") != "user":
                            continue
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    first_prompt = block.get("text", "").strip()
                                    if first_prompt:
                                        break
                        elif isinstance(content, str) and content.strip():
                            first_prompt = content.strip()
                        if first_prompt:
                            break
            except Exception:
                pass
            if first_prompt:
                break

    if first_prompt:
        topic = first_prompt[:60].strip().replace("\n", " ")
        if len(first_prompt) > 60:
            topic += "…"
        # Auto-register so future notifications + /rename work on this session
        register_session_if_new(session_id, topic)
        return f"{short_id} _{topic}_"
    return short_id


def last_prompt(hook: dict) -> str:
    """Return the most recent user message from the transcript."""
    transcript = hook.get("transcript", [])
    for msg in reversed(transcript):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            return text[:200]
            elif isinstance(content, str) and content.strip():
                return content.strip()[:200]
    return ""


def _extract_text(content) -> str:
    """Pull plain text out of a content field (str or list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(parts).strip()
    return ""


def last_assistant_response(hook: dict, max_chars: int = 800) -> str:
    """Return the most recent assistant text response.

    Tries the hook transcript first (handles both raw and JSONL-wrapped formats),
    then falls back to reading the session JSONL file directly.
    """
    def _trunc(t: str) -> str:
        return (t[:max_chars] + "…") if len(t) > max_chars else t

    # 1. Scan transcript (may be raw {role,content} or JSONL-wrapped {message:{role,content}})
    transcript = hook.get("transcript", [])
    for entry in reversed(transcript):
        msg = entry.get("message", entry)          # unwrap if JSONL-style
        if msg.get("role") != "assistant":
            continue
        text = _extract_text(msg.get("content", ""))
        if text:
            return _trunc(text)

    # 2. Fall back to the session JSONL file (most reliable)
    session_id = hook.get("session_id", "")
    if not session_id:
        return ""

    import glob as _glob
    home = os.path.expanduser("~")
    pattern = os.path.join(home, ".claude", "projects", "**", f"{session_id}.jsonl")
    for jsonl_path in _glob.glob(pattern, recursive=True):
        try:
            with open(jsonl_path, encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
            for line in reversed(lines):
                obj = json.loads(line)
                msg = obj.get("message", obj)
                if msg.get("role") != "assistant":
                    continue
                text = _extract_text(msg.get("content", ""))
                if text:
                    return _trunc(text)
        except Exception:
            pass

    return ""


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    # Skip notifications for sessions launched by listener.py — the response
    # is already being streamed to Telegram directly via run_claude.py.
    if os.environ.get("CLAUDE_TELEGRAM_INITIATED"):
        sys.exit(0)

    try:
        cfg = load_config()
        token = cfg["TELEGRAM_BOT_TOKEN"]
        chat_id = cfg["TELEGRAM_CHAT_ID"]

        hook = json.load(sys.stdin)
        label = session_label(hook)

        if event == "stop":
            prompt = last_prompt(hook)
            if hook.get("stop_hook_active"):
                # Claude is waiting for input — show its response so you have context
                response = last_assistant_response(hook)
                prompt_line = f"\n\n*You:* {prompt}" if prompt else ""
                response_line = f"\n\n*Claude:* {response}" if response else ""
                text = f"⏳ *Waiting for input* — {label}{prompt_line}{response_line}"
            else:
                response = last_assistant_response(hook)
                prompt_line = f"\n\n*You:* {prompt}" if prompt else ""
                response_line = f"\n\n*Claude:* {response}" if response else ""
                text = f"✅ *Done* — {label}{prompt_line}{response_line}"
                session_id = hook.get("session_id", "")
                markup = {"inline_keyboard": [[
                    {"text": "▶ Continue", "callback_data": f"continue:{session_id}"}
                ]]} if session_id else None
                send(token, chat_id, text, reply_markup=markup)
                sys.exit(0)

        elif event == "notification":
            msg = hook.get("message", "")
            text = f"🔔 *Notice* — {label}\n\n{msg}" if msg else f"🔔 *Notice* — {label}"

        else:
            text = f"🤖 `{event}` — {label}"

        send(token, chat_id, text)

    except Exception as exc:
        # Never block Claude due to notification failures
        sys.stderr.write(f"[telegram/notify] {exc}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
