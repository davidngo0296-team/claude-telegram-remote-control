"""Tail an active Claude session and forward assistant messages to Telegram.

Usage:
    python3 tail_session.py [session_id]

If session_id is omitted, the most recently modified session is used.
Watches the session JSONL file for new assistant turns and sends them
to Telegram as they appear. Does not interrupt the running session.
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen


def load_config() -> dict:
    path = Path(__file__).parent / "config.env"
    cfg = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def _api_post(token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=10)
    return json.loads(resp.read())


def send_message(token: str, chat_id: str, text: str) -> None:
    MAX = 4000
    while text:
        chunk, text = text[:MAX], text[MAX:]
        try:
            _api_post(token, "sendMessage", {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
            })
        except Exception:
            # Retry without markdown if parse error
            try:
                _api_post(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": chunk,
                })
            except Exception as e:
                print(f"[send error] {e}", file=sys.stderr)


def find_jsonl(session_id: str | None) -> Path:
    home = os.path.expanduser("~")
    pattern = os.path.join(home, ".claude", "projects", "**", "*.jsonl")
    files = glob.glob(pattern, recursive=True)
    if not files:
        raise FileNotFoundError("No Claude session files found.")

    if session_id:
        matches = [f for f in files if os.path.splitext(os.path.basename(f))[0] == session_id]
        if not matches:
            raise FileNotFoundError(f"Session {session_id} not found.")
        return Path(matches[0])

    # Most recently modified
    return Path(max(files, key=os.path.getmtime))


def extract_assistant_text(line: str) -> str | None:
    """Return assistant text from a JSONL line, or None if not applicable."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    msg = obj.get("message", obj)
    if msg.get("role") != "assistant":
        return None

    content = msg.get("content", "")
    if isinstance(content, str):
        return content.strip() or None

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "").strip()
                if t:
                    parts.append(t)
        return "\n".join(parts) or None

    return None


def tail(token: str, chat_id: str, jsonl: Path) -> None:
    print(f"[tail] Watching {jsonl}")
    print(f"[tail] Forwarding assistant messages to Telegram. Ctrl+C to stop.")

    # Seek to end so we only forward new messages, not history
    with open(jsonl, encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # seek to end
        pos = f.tell()

    while True:
        try:
            size = jsonl.stat().st_size
        except FileNotFoundError:
            time.sleep(1)
            continue

        if size > pos:
            with open(jsonl, encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                new_data = f.read()
            pos = size

            for line in new_data.splitlines():
                line = line.strip()
                if not line:
                    continue
                text = extract_assistant_text(line)
                if text:
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] -> Telegram ({len(text)} chars)")
                    send_message(token, chat_id, text)

        time.sleep(0.5)


if __name__ == "__main__":
    cfg = load_config()
    token = cfg["TELEGRAM_BOT_TOKEN"]
    chat_id = cfg["TELEGRAM_CHAT_ID"]

    session_id = sys.argv[1] if len(sys.argv) > 1 else None

    try:
        jsonl = find_jsonl(session_id)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    sid = os.path.splitext(jsonl.name)[0]
    print(f"[tail] Session: {sid}")

    try:
        tail(token, chat_id, jsonl)
    except KeyboardInterrupt:
        print("\n[tail] Stopped.")
