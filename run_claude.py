"""Run `claude -p` as a subprocess and stream the response to Telegram.

Edits a single Telegram message progressively as chunks arrive.
Handles long responses by splitting into multiple messages.
Saves session_id + name so the session picker can display it by name.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

TELEGRAM_ENV_MARKER = "CLAUDE_TELEGRAM_INITIATED"
MAX_LEN = 3800
EDIT_INTERVAL = 0.8

TOOL_ICONS = {
    "Bash": "🔧",
    "Read": "📖",
    "Write": "✏️",
    "Edit": "✏️",
    "NotebookEdit": "✏️",
}
RESULT_LINES = 5


def _format_input_snippet(name: str, input_dict: dict) -> str:
    """Extract key input field for display, truncated to 60 chars."""
    if name == "Bash":
        raw = input_dict.get("command", "")
    elif name in ("Read", "Write", "Edit", "NotebookEdit"):
        raw = input_dict.get("file_path", "")
    else:
        raw = json.dumps(input_dict)
    if len(raw) > 60:
        return raw[:57] + "..."
    return raw


def _truncate_result(content) -> str:
    """Return first RESULT_LINES lines of tool result content."""
    if content is None:
        return ""
    if isinstance(content, list):
        text = "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    else:
        text = str(content)
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= RESULT_LINES:
        return text
    return "\n".join(lines[:RESULT_LINES]) + "\n…"


def _render_activity(tool_calls: list) -> str:
    """Render live activity log for the Telegram message."""
    parts = ["⌛ _Working…_\n"]
    for tc in tool_calls:
        icon = TOOL_ICONS.get(tc["name"], "🔩")
        snippet = tc["snippet"]
        if tc["done"]:
            result = tc.get("result_lines", "")
            if result:
                parts.append(
                    f"{icon} *{tc['name']}:* `{snippet}`\n```\n{result}\n```✓\n"
                )
            else:
                parts.append(f"{icon} *{tc['name']}:* `{snippet}` ✓\n")
        else:
            parts.append(f"{icon} *{tc['name']}:* `{snippet}` ⏳\n")
    text = "".join(parts)
    if len(text) > MAX_LEN:
        return "…" + text[-MAX_LEN:]
    return text


# Add this dir to path so sessions.py can be imported
sys.path.insert(0, str(Path(__file__).parent))
import sessions as sess_store


def _load_config() -> dict:
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


def send_message(token: str, chat_id: str, text: str) -> int:
    result = _api_post(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text or "…",
        "parse_mode": "Markdown",
    })
    return result["result"]["message_id"]


def edit_message(token: str, chat_id: str, message_id: int, text: str) -> None:
    try:
        _api_post(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text or "…",
            "parse_mode": "Markdown",
        })
    except Exception:
        pass


def run_and_stream(token: str, chat_id: str, prompt: str,
                   session_id: str | None = None,
                   on_session_id=None) -> None:
    """Run claude -p and stream the response to Telegram.

    Args:
        token: Telegram bot token.
        chat_id: Telegram chat ID to send messages to.
        prompt: The user's message to send to Claude.
        session_id: If provided, resume this session. If None, start a new one.
    """
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if session_id:
        cmd += ["--resume", session_id]

    env = {**os.environ, TELEGRAM_ENV_MARKER: "1"}

    # On Windows, Claude is installed as claude.cmd which requires shell=True
    use_shell = sys.platform == "win32"
    popen_cmd = subprocess.list2cmdline(cmd) if use_shell else cmd

    message_id = send_message(token, chat_id, "⌛ _Thinking…_")

    tool_calls: list[dict] = []   # {id, name, snippet, result_lines, done}
    id_to_idx: dict[str, int] = {}
    final_text = ""
    new_session_id: str | None = None
    last_edit = 0.0
    proc = None

    try:
        proc = subprocess.Popen(
            popen_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=use_shell,
        )

        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        final_text += block.get("text", "")
                    elif btype == "tool_use":
                        tool_id = block.get("id", "")
                        name = block.get("name", "")
                        snippet = _format_input_snippet(name, block.get("input", {}))
                        idx = len(tool_calls)
                        tool_calls.append({
                            "id": tool_id,
                            "name": name,
                            "snippet": snippet,
                            "result_lines": "",
                            "done": False,
                        })
                        id_to_idx[tool_id] = idx

            elif etype == "user":
                for block in event.get("message", {}).get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        tool_id = block.get("tool_use_id", "")
                        if not tool_id:
                            continue
                        idx = id_to_idx.get(tool_id)
                        if idx is not None:
                            tool_calls[idx]["result_lines"] = _truncate_result(
                                block.get("content", ""))
                            tool_calls[idx]["done"] = True

            elif etype == "text":
                # Streaming text delta — only present in non-verbose modes.
                # In --verbose stream-json, text comes via assistant events above.
                pass

            elif etype == "result":
                new_session_id = event.get("session_id")
                if new_session_id and on_session_id:
                    on_session_id(new_session_id)
                # Without --verbose, text arrives here instead of assistant events
                if not final_text:
                    final_text = event.get("result", "") or ""

            now = time.monotonic()
            if now - last_edit > EDIT_INTERVAL:
                if tool_calls and not final_text:
                    edit_message(token, chat_id, message_id,
                                 _render_activity(tool_calls))
                elif final_text:
                    edit_message(token, chat_id, message_id, _tail(final_text))
                last_edit = now

    except Exception as exc:
        final_text = final_text or f"_(Error: {exc})_"
    finally:
        if proc is not None:
            proc.wait()

    # Save / update session with a human-readable name
    resolved_id = new_session_id or session_id
    if resolved_id:
        existing = sess_store.get(resolved_id)
        if existing:
            sess_store.upsert(resolved_id)           # just update last_used
        else:
            name = prompt[:50].strip()               # use prompt as initial name
            if len(prompt) > 50:
                name += "…"
            sess_store.upsert(resolved_id, name)

    # Mirror final response to terminal for local monitoring
    if final_text:
        print(f"\n[Telegram → Claude]\n{final_text}\n", file=sys.stderr)

    # Final publish — replace live activity with the actual response
    if final_text:
        if len(final_text) <= MAX_LEN:
            edit_message(token, chat_id, message_id, final_text)
        else:
            CONT = " _(cont.)_"
            edit_message(token, chat_id, message_id,
                         final_text[:MAX_LEN - len(CONT)] + CONT)
            rest = final_text[MAX_LEN - len(CONT):]
            while rest:
                send_message(token, chat_id, rest[:MAX_LEN])
                rest = rest[MAX_LEN:]
    elif tool_calls:
        edit_message(token, chat_id, message_id,
                     f"_(Done — {len(tool_calls)} tool call(s))_")
    else:
        edit_message(token, chat_id, message_id, "_(No response received)_")


def _tail(text: str) -> str:
    if len(text) <= MAX_LEN:
        return text
    return "…" + text[-MAX_LEN:]
