"""Background listener: polls Telegram and handles all interactions.

Handles:
  - Button taps (Approve/Deny for tool approval)
  - Session selection (picks which conversation to continue)
  - Text messages (sends to Claude, streams response back)

Run this before starting Claude Code sessions:
    python listener.py
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

# Add this dir to path so sessions.py and run_claude.py can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sessions as sess_store
from run_claude import run_and_stream, send_message, _api_post


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


def api_get(token: str, method: str, params: dict | None = None) -> dict:
    from urllib.parse import urlencode
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urlencode(params)
    resp = urlopen(url, timeout=35)
    return json.loads(resp.read())


def approval_file(session_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"claude_approval_{session_id}.txt")


# Pending prompts: maps chat_id → prompt text (waiting for session selection)
_pending_prompts: dict[str, str] = {}

# Pending sessions: maps chat_id → session_id (user tapped ▶ Continue, waiting for prompt)
_pending_sessions: dict[str, str] = {}

# Pending feedback: maps chat_id → session_id (user tapped 💬 Deny with feedback)
_pending_feedback: dict[str, str] = {}

# Pending rename: maps chat_id → session_id (user picked a session, waiting for new name)
_pending_rename: dict[str, str] = {}


# ── Approval button handler ────────────────────────────────────────────────

def handle_callback(token: str, callback_query: dict) -> None:
    callback_id = callback_query["id"]
    data = callback_query.get("data", "")
    message = callback_query["message"]
    message_id = message["message_id"]
    chat_id = str(message["chat"]["id"])

    parts = data.split(":", 1)
    action = parts[0]
    payload = parts[1] if len(parts) > 1 else ""

    # ── Tool approval ──────────────────────────────────────────────────────
    if action in ("approve", "deny"):
        session_id = payload
        _pending_feedback.pop(chat_id, None)   # clear any stale feedback state
        with open(approval_file(session_id), "w") as f:
            f.write(action)

        label = "✅ Approved!" if action == "approve" else "❌ Denied"
        _api_post(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": label,
        })
        _api_post(token, "deleteMessage", {
            "chat_id": chat_id,
            "message_id": message_id,
        })
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {action.upper():6s} — session {session_id[:8]}")

    elif action == "allow_always":
        session_id = payload
        with open(approval_file(session_id), "w") as f:
            f.write("allow_always")
        _pending_feedback.pop(chat_id, None)  # clear any stale feedback state
        _api_post(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "🔒 Always allowed!",
        })
        _api_post(token, "deleteMessage", {
            "chat_id": chat_id,
            "message_id": message_id,
        })
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] ALWAYS  — session {session_id[:8]}")

    elif action == "feedback":
        session_id = payload
        _pending_feedback[chat_id] = session_id
        _api_post(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Send your feedback",
        })
        _api_post(token, "deleteMessage", {
            "chat_id": chat_id,
            "message_id": message_id,
        })
        _api_post(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "💬 What should Claude do instead?",
            "reply_markup": {"force_reply": True, "selective": True},
        })
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] FDBK_WAIT — session {session_id[:8]}")

    # ── Continue (from Done notification) ─────────────────────────────────
    elif action == "continue":
        _pending_sessions[chat_id] = payload
        _api_post(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Ready — type your next task",
        })
        _api_post(token, "editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        })

    # ── Rename: user picked a session, now ask for the new name ───────────
    elif action == "rename_pick":
        session_id = payload
        _pending_rename[chat_id] = session_id
        _api_post(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Type the new name",
        })
        _api_post(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"✏️ *Renaming* `…{session_id[-8:]}`\n\nType the new name:",
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": []},
        })
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] RENAME_WAIT — session {session_id[:8]}")

    # ── Session selection ──────────────────────────────────────────────────
    elif action == "session":
        prompt = _pending_prompts.pop(chat_id, None)
        if not prompt:
            _api_post(token, "answerCallbackQuery", {
                "callback_query_id": callback_id,
                "text": "Session expired — please send your message again.",
            })
            return

        chosen_id = None if payload == "new" else payload

        # Acknowledge and clean up the picker message
        session_label = "new conversation" if not chosen_id else (
            sess_store.get(chosen_id) or {}
        ).get("name", chosen_id[:8])
        _api_post(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": f"Using: {session_label}",
        })
        _api_post(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"💬 *{session_label}*\n\n_{prompt[:80]}_",
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": []},
        })

        cfg = load_config()
        threading.Thread(
            target=run_and_stream,
            args=(cfg["TELEGRAM_BOT_TOKEN"], chat_id, prompt, chosen_id),
            daemon=True,
        ).start()


# ── Text message handler ───────────────────────────────────────────────────

def handle_text_message(token: str, chat_id: str, text: str) -> None:
    text = text.strip()

    # ── Pending deny-with-feedback reply ───────────────────────────────────
    if chat_id in _pending_feedback:
        session_id = _pending_feedback.pop(chat_id)
        reason = text or "Denied via Telegram."
        with open(approval_file(session_id), "w") as f:
            f.write(f"deny:{reason}")
        send_message(token, chat_id, f"💬 *Denied with feedback:*\n{reason[:100]}")
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] FDBK_SENT — session {session_id[:8]}: {reason[:60]}")
        return

    # ── Pending rename: user typed the new name after picking a session ─────
    if chat_id in _pending_rename:
        session_id = _pending_rename.pop(chat_id)
        new_name = text
        sess_store.upsert(session_id, new_name)
        send_message(token, chat_id, f"✏️ Renamed to: *{new_name}*")
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] RENAMED — session {session_id[:8]} → {new_name}")
        return

    # ── Commands ───────────────────────────────────────────────────────────
    if text.lower() in ("/new", "/reset"):
        send_message(token, chat_id, "🆕 *New conversation started.* What would you like to work on?")
        # Don't clear stored sessions — just send without a session_id
        _pending_prompts[chat_id] = text
        # Override: run immediately as new session
        _pending_prompts.pop(chat_id, None)
        threading.Thread(
            target=run_and_stream,
            args=(token, chat_id, "Start a new conversation. Say hello and ask what I want to work on."),
            daemon=True,
        ).start()
        return

    if text.lower() == "/rename":
        sessions = get_all_sessions(limit=8)
        if not sessions:
            send_message(token, chat_id, "No sessions found.")
            return
        keyboard = []
        for s in sessions:
            keyboard.append([{
                "text": s['name'][:45],
                "callback_data": f"rename_pick:{s['id']}",
            }])
        _api_post(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "✏️ *Which session do you want to rename?*",
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": keyboard},
        })
        return

    if text.lower() == "/sessions":
        _show_sessions_list(token, chat_id)
        return

    if text.lower() == "/help":
        send_message(token, chat_id,
            "*Commands:*\n"
            "/new — start a fresh conversation\n"
            "/sessions — list saved conversations\n"
            "/rename — pick a conversation to rename\n"
            "/mode — show current approval mode\n"
            "/mode telegram — always approve via Telegram\n"
            "/mode local — always use CLI dialog \\(Telegram off\\)\n"
            "/mode auto — auto\\-detect based on idle time\n"
            "/help — show this message\n\n"
            "_Any other message is sent to Claude._"
        )
        return

    if text.lower().startswith("/mode"):
        _handle_mode_command(token, chat_id, text)
        return

    # ── Continue from Done notification (session already chosen) ──────────
    if chat_id in _pending_sessions:
        session_id = _pending_sessions.pop(chat_id)
        s = sess_store.get(session_id)
        name = s["name"] if s else session_id[:8]
        send_message(token, chat_id, f"💬 *{name}*\n\n_{text[:80]}_")
        threading.Thread(
            target=run_and_stream,
            args=(token, chat_id, text, session_id),
            daemon=True,
        ).start()
        return

    # ── Session picker ─────────────────────────────────────────────────────
    sessions = get_all_sessions(limit=8)

    if len(sessions) == 0:
        # No history — start fresh immediately
        threading.Thread(
            target=run_and_stream,
            args=(token, chat_id, text, None),
            daemon=True,
        ).start()
        return

    if len(sessions) == 1:
        # One session — ask continue or new
        s = sessions[0]
        _pending_prompts[chat_id] = text
        _api_post(token, "sendMessage", {
            "chat_id": chat_id,
            "text": f"💬 Continue *{s['name']}* or start a new conversation?",
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": f"↩️ {s['name'][:30]}", "callback_data": f"session:{s['id']}"},
                    {"text": "✨ New chat",            "callback_data": "session:new"},
                ]]
            },
        })
        return

    # Multiple sessions — show picker (max 4 rows + New Conversation)
    _pending_prompts[chat_id] = text

    keyboard = []
    for s in sessions[:4]:
        keyboard.append([{
            "text": f"↩️ {s['name'][:35]}",
            "callback_data": f"session:{s['id']}",
        }])
    keyboard.append([{"text": "✨ New conversation", "callback_data": "session:new"}])

    _api_post(token, "sendMessage", {
        "chat_id": chat_id,
        "text": "💬 *Which conversation?*",
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": keyboard},
    })


MODE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode.txt")


def _first_user_message(jsonl_path: str) -> str:
    """Extract the first user text message from a session JSONL file."""
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
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
                            t = block.get("text", "").strip()
                            if t:
                                return t[:50]
                elif isinstance(content, str) and content.strip():
                    return content.strip()[:50]
    except Exception:
        pass
    return ""


def get_all_sessions(limit: int = 8) -> list:
    """Return up to `limit` recent sessions, merging stored names with JSONL discovery.

    Registered sessions (telegram_sessions.json) keep their custom names.
    Unregistered JSONL sessions are included with their first user message as name.
    All sessions sorted by recency (most recent first).
    """
    import glob as _glob

    registered = {s["id"]: s for s in sess_store.all_sessions()}

    # Discover all JSONL session files, sorted by modification time (newest first)
    home = os.path.expanduser("~")
    pattern = os.path.join(home, ".claude", "projects", "**", "*.jsonl")
    jsonl_files = sorted(
        _glob.glob(pattern, recursive=True),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )

    seen_ids = set()
    merged = []

    for path in jsonl_files:
        session_id = os.path.splitext(os.path.basename(path))[0]
        # Skip non-UUID filenames
        if len(session_id) != 36 or session_id.count("-") != 4:
            continue
        if session_id in seen_ids:
            continue
        seen_ids.add(session_id)

        if session_id in registered:
            merged.append(registered[session_id])
        else:
            name = _first_user_message(path)
            if not name:
                continue
            mtime = os.path.getmtime(path)
            import datetime
            last_used = datetime.datetime.fromtimestamp(
                mtime, tz=datetime.timezone.utc
            ).isoformat()
            merged.append({"id": session_id, "name": name, "last_used": last_used})

        if len(merged) >= limit:
            break

    return merged


def _handle_mode_command(token: str, chat_id: str, text: str) -> None:
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        # /mode with no argument — show current mode
        try:
            mode = open(MODE_FILE).read().strip()
        except Exception:
            mode = "auto (default)"
        send_message(token, chat_id,
            f"*Current approval mode:* `{mode}`\n\n"
            "Set with:\n"
            "`/mode auto` — idle\\-based auto\\-detect \\(default\\)\n"
            "`/mode telegram` — always use Telegram\n"
            "`/mode local` — always use CLI dialog"
        )
        return

    new_mode = parts[1].strip().lower()
    if new_mode not in ("auto", "telegram", "local"):
        send_message(token, chat_id, "❌ Unknown mode. Use: `auto`, `telegram`, or `local`")
        return

    with open(MODE_FILE, "w") as f:
        f.write(new_mode)

    labels = {
        "auto":     "🤖 *Auto-detect* — Telegram when idle ≥5 min, CLI when active",
        "telegram": "📱 *Telegram only* — all approvals come to your phone",
        "local":    "💻 *Local only* — CLI dialog always, Telegram disabled for approvals",
    }
    send_message(token, chat_id, f"✅ Mode set: {labels[new_mode]}")
    print(f"[{time.strftime('%H:%M:%S')}] MODE set to '{new_mode}' via Telegram")


def _show_sessions_list(token: str, chat_id: str) -> None:
    sessions = sess_store.all_sessions()
    if not sessions:
        send_message(token, chat_id, "No saved conversations yet.")
        return
    lines = ["*Saved conversations:*\n"]
    for i, s in enumerate(sessions, 1):
        last = s.get("last_used", "")[:10]
        full_id = s.get("id", "")
        lines.append(f"{i}\\. _{s['name']}_ — `{last}`\n`{full_id}`")
    lines.append("\n_Resume in CLI:_ `claude --resume <id>`")
    send_message(token, chat_id, "\n".join(lines))


# ── Main polling loop ──────────────────────────────────────────────────────

def run(token: str, chat_id: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Claude Telegram bridge running. Press Ctrl+C to stop.")
    offset = 0
    while True:
        try:
            result = api_get(token, "getUpdates", {"offset": offset, "timeout": 30})
            for update in result.get("result", []):
                offset = update["update_id"] + 1

                if "callback_query" in update:
                    handle_callback(token, update["callback_query"])

                elif "message" in update:
                    msg = update["message"]
                    if str(msg.get("chat", {}).get("id")) != str(chat_id):
                        continue  # Ignore messages from other chats
                    text = msg.get("text", "").strip()
                    if text:
                        ts = time.strftime("%H:%M:%S")
                        print(f"[{ts}] MSG — {text[:60]}")
                        handle_text_message(token, chat_id, text)

        except KeyboardInterrupt:
            print("\n[bridge] Stopped.")
            break
        except URLError as exc:
            print(f"[{time.strftime('%H:%M:%S')}] Network error: {exc} — retrying in 5s")
            time.sleep(5)
        except Exception as exc:
            print(f"[{time.strftime('%H:%M:%S')}] Error: {exc} — retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    cfg = load_config()
    run(cfg["TELEGRAM_BOT_TOKEN"], cfg["TELEGRAM_CHAT_ID"])
