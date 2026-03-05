"""Handle Claude Code PermissionRequest hook via Telegram.

Sends an inline-button message (Approve / Deny) to Telegram, polls for
the user's tap, then writes the decision to stdout in the format Claude
Code expects for PermissionRequest hooks.

Output format (always exit 0 — Claude reads stdout for the decision):
    {"behavior": "allow"}                          → proceed
    {"behavior": "deny", "message": "<reason>"}    → block with reason

Usage (set in settings.json under PermissionRequest):
    python approve.py
"""

import ctypes
import ctypes.wintypes
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen


TIMEOUT_SECONDS = 36000  # 10 hours
MODE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode.txt")
SESSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_sessions.json")
HEARTBEAT_FILE = os.path.join(tempfile.gettempdir(), "claude_listener_alive.pid")


def get_session_name(session_id: str) -> str:
    try:
        sessions = json.loads(open(SESSIONS_FILE, encoding="utf-8").read())
        for s in sessions:
            if s.get("id") == session_id:
                return s.get("name", "")
    except Exception:
        pass
    return ""


def upsert_session(session_id: str, name: str) -> None:
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        try:
            sessions = json.loads(open(SESSIONS_FILE, encoding="utf-8").read())
        except Exception:
            sessions = []
        for s in sessions:
            if s.get("id") == session_id:
                s["last_used"] = now
                break
        else:
            sessions.append({"id": session_id, "name": name, "last_used": now})
        sessions = sorted(sessions, key=lambda s: s.get("last_used", ""), reverse=True)[:10]
        open(SESSIONS_FILE, "w", encoding="utf-8").write(json.dumps(sessions, indent=2))
    except Exception:
        pass


def plain_content(tool_name: str, tool_input: dict) -> str:
    """Return full plain-text content for the popup (no markdown)."""
    if tool_name == "Bash":
        return tool_input.get("command", "").strip()
    if tool_name == "Edit":
        path = tool_input.get("file_path", "")
        old = tool_input.get("old_string", "")
        new = tool_input.get("new_string", "")
        return f"File: {path}\n\n── Replace ──\n{old}\n\n── With ──\n{new}"
    if tool_name == "Write":
        path = tool_input.get("file_path", "")
        content = tool_input.get("content", "")
        return f"File: {path}\n\n{content}"
    if tool_name == "NotebookEdit":
        path = tool_input.get("notebook_path", "")
        return f"Notebook: {path}\n\n{json.dumps(tool_input, indent=2)}"
    return json.dumps(tool_input, indent=2)


def show_desktop_popup(tool_name: str, tool_input: dict, cwd: str = "") -> tuple:
    """Show an approval dialog mirroring the Telegram 2×2 button layout.

    Returns (decision: str, reason: str) where decision is one of:
      "allow"        — proceed
      "allow_always" — proceed and write a permanent allow rule
      "deny"         — block with reason
    Falls back to ("allow", "") if tkinter is unavailable.
    """
    try:
        import tkinter as tk
        from tkinter import scrolledtext, simpledialog

        result = {"decision": "deny", "reason": "Denied via desktop dialog."}

        root = tk.Tk()
        root.title(f"Claude — {tool_name}")
        root.attributes("-topmost", True)
        root.resizable(True, True)

        # Centre on screen at a comfortable size
        w, h = 720, 520
        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # ── Header bar ───────────────────────────────────────────────────
        hdr = tk.Frame(root, bg="#1e1e2e")
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"  {tool_name}", font=("Consolas", 12, "bold"),
                 bg="#1e1e2e", fg="#cdd6f4", anchor="w").pack(side="left", pady=8)
        if cwd:
            tk.Label(hdr, text=f"   {cwd}", font=("Consolas", 9),
                     bg="#1e1e2e", fg="#6c7086", anchor="w").pack(side="left")

        # ── Scrollable content area ──────────────────────────────────────
        txt = scrolledtext.ScrolledText(
            root, wrap=tk.WORD, font=("Consolas", 10),
            bg="#181825", fg="#cdd6f4", relief="flat",
            padx=12, pady=10, insertbackground="white",
        )
        txt.insert("1.0", plain_content(tool_name, tool_input))
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)

        # ── Button bar (2×2 matching Telegram layout) ─────────────────────
        bar = tk.Frame(root, bg="#1e1e2e", pady=10)
        bar.pack(fill="x", side="bottom")

        def _allow():
            result["decision"] = "allow"
            root.destroy()

        def _allow_always():
            result["decision"] = "allow_always"
            root.destroy()

        def _deny():
            result["decision"] = "deny"
            result["reason"] = "Denied via desktop dialog."
            root.destroy()

        def _deny_feedback():
            feedback = simpledialog.askstring(
                "Tell Claude why",
                "What should Claude do instead?",
                parent=root,
            )
            result["decision"] = "deny"
            result["reason"] = (
                feedback.strip() if feedback and feedback.strip()
                else "Denied via desktop dialog."
            )
            root.destroy()

        btn = {"font": ("Segoe UI", 10), "relief": "flat", "cursor": "hand2",
               "padx": 18, "pady": 7}
        row1 = tk.Frame(bar, bg="#1e1e2e")
        row1.pack(fill="x", padx=12, pady=(0, 4))
        row2 = tk.Frame(bar, bg="#1e1e2e")
        row2.pack(fill="x", padx=12)

        # Row 1: Allow  |  Always allow
        tk.Button(row1, text="✅  Allow",        bg="#a6e3a1", fg="#1e1e2e", command=_allow,        **btn).pack(side="left", padx=(0, 4))
        tk.Button(row1, text="🔒  Always allow", bg="#f9e2af", fg="#1e1e2e", command=_allow_always, **btn).pack(side="left")
        # Row 2: Deny   |  Deny with feedback
        tk.Button(row2, text="❌  Deny",               bg="#f38ba8", fg="#1e1e2e", command=_deny,          **btn).pack(side="left", padx=(0, 4))
        tk.Button(row2, text="💬  Deny with feedback", bg="#89b4fa", fg="#1e1e2e", command=_deny_feedback, **btn).pack(side="left")

        root.protocol("WM_DELETE_WINDOW", _deny)  # X button = Deny
        root.mainloop()
        return result["decision"], result["reason"]

    except Exception as exc:
        sys.stderr.write(f"[telegram/approve] popup failed ({exc}), auto-approving\n")
        return "allow", ""


def get_idle_seconds() -> float:
    """Return seconds since the last keyboard/mouse input.

    Windows: uses GetLastInputInfo via ctypes.
    Linux/macOS: no reliable cross-platform idle API; return a large value
    so that 'auto' mode always routes to Telegram on servers.
    """
    if sys.platform != "win32":
        return float("inf")
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    elapsed_ms = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
    return elapsed_ms / 1000.0


def read_mode(cfg: dict) -> str:
    """Return current approval mode: 'auto', 'telegram', or 'local'.

    The mode.txt file takes priority over config.env so that shell
    aliases and Telegram /mode commands take immediate effect.
    """
    if os.path.exists(MODE_FILE):
        try:
            return open(MODE_FILE).read().strip().lower()
        except Exception:
            pass
    return cfg.get("APPROVAL_MODE", "auto").lower()


def should_use_telegram(cfg: dict) -> bool:
    """Return True if the approval should be routed to Telegram."""
    mode = read_mode(cfg)
    if mode == "telegram":
        return True
    if mode == "local":
        return False
    # auto: compare idle time to threshold
    threshold = int(cfg.get("IDLE_THRESHOLD_SECONDS", "300"))
    idle = get_idle_seconds()
    return idle >= threshold


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


def api_post(token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=10)
    return json.loads(resp.read())


def format_tool_detail(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip()
        return f"`{cmd[:400]}`"
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        path = tool_input.get("file_path", tool_input.get("path", ""))
        return f"`{path}`"
    raw = json.dumps(tool_input, indent=2)
    return f"```\n{raw[:400]}\n```"


def build_allow_rule(tool_name: str, tool_input: dict) -> str:
    """Build a permissions.allow rule string for the given tool call.

    Bash → Bash(<first-word> *)   e.g. "git status" → "Bash(git *)"
    Edit/Write/NotebookEdit → "Edit"  (shared permission umbrella)
    Anything else → tool name only
    """
    if tool_name == "Bash":
        command = tool_input.get("command", "").strip()
        words = command.split()
        if not words:
            return "Bash"
        return f"Bash({words[0]} *)"
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        return "Edit"
    return tool_name


def write_allow_rule(cwd: str, rule: str) -> None:
    """Write a permissions.allow rule to .claude/settings.json.

    Uses <cwd>/.claude/settings.json if cwd is a valid directory,
    otherwise falls back to ~/.claude/settings.json.
    Creates the file and directory if they don't exist.
    Skips silently if the rule is already present.
    Logs errors to stderr but never raises (fail-open).
    """
    try:
        if cwd and os.path.isdir(cwd):
            settings_path = Path(cwd) / ".claude" / "settings.json"
        else:
            settings_path = Path.home() / ".claude" / "settings.json"

        settings_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception as parse_exc:
            sys.stderr.write(f"[telegram/approve] settings.json parse error ({parse_exc}), starting fresh\n")
            data = {}

        permissions = data.setdefault("permissions", {})
        allow_list = permissions.setdefault("allow", [])

        if rule not in allow_list:
            allow_list.append(rule)
            settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        sys.stderr.write(f"[telegram/approve] write_allow_rule failed: {exc}\n")


def approval_file(session_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"claude_approval_{session_id}.txt")


def read_first_prompt_from_jsonl(session_id: str) -> str:
    """Read the first user message from Claude's session JSONL file.

    Claude stores sessions at ~/.claude/projects/<project-hash>/<session-id>.jsonl
    We glob-search across all projects so this works regardless of cwd.
    """
    import glob as _glob
    home = os.path.expanduser("~")
    pattern = os.path.join(home, ".claude", "projects", "**", f"{session_id}.jsonl")
    matches = _glob.glob(pattern, recursive=True)
    if not matches:
        return ""
    try:
        with open(matches[0], encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                # Each line is either a raw message or wrapped in {type, message}
                msg = obj.get("message", obj)
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "").strip()
                            if text:
                                return text
                elif isinstance(content, str) and content.strip():
                    return content.strip()
    except Exception:
        pass
    return ""


def send_approval_request(token: str, chat_id: str, tool_name: str,
                           tool_input: dict, session_id: str, cwd: str = "") -> None:
    detail = format_tool_detail(tool_name, tool_input)
    short_id = f"…{session_id[-8:]}" if session_id else "unknown"
    cwd_line = f"\n*Dir:* `{cwd}`" if cwd else ""

    # Resolve session name: stored name → JSONL file → nothing
    session_name = get_session_name(session_id)
    if not session_name and session_id:
        first_prompt = read_first_prompt_from_jsonl(session_id)
        if first_prompt:
            session_name = first_prompt[:50].strip()
            upsert_session(session_id, session_name)

    label_suffix = f" — _{session_name}_" if session_name else ""
    text = (
        f"🔧 *Permission Request*{label_suffix}\n"
        f"`{short_id}`{cwd_line}\n\n"
        f"*Tool:* `{tool_name}`\n\n{detail}"
    )

    api_post(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve",            "callback_data": f"approve:{session_id}"},
                    {"text": "🔒 Always allow",        "callback_data": f"allow_always:{session_id}"},
                ],
                [
                    {"text": "❌ Deny",               "callback_data": f"deny:{session_id}"},
                    {"text": "💬 Deny with feedback", "callback_data": f"feedback:{session_id}"},
                ],
            ]
        },
    })


def wait_for_decision(session_id: str) -> str:
    path = approval_file(session_id)
    if os.path.exists(path):
        os.remove(path)

    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if os.path.exists(path):
            with open(path) as f:
                decision = f.read().strip()
            os.remove(path)
            return decision
        time.sleep(0.5)

    return "timeout"


def listener_running() -> bool:
    """Return True if the listener process is alive."""
    try:
        pid = int(open(HEARTBEAT_FILE).read().strip())
        os.kill(pid, 0)  # signal 0 = just check existence
        return True
    except Exception:
        return False


def terminal_prompt(tool_name: str, tool_input: dict, cwd: str) -> tuple:
    """Prompt for approval directly on the terminal via /dev/tty.

    Returns (decision, reason): decision is 'allow' or 'deny'.
    """
    content = plain_content(tool_name, tool_input)
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(f"\n🔧 Permission Request — {tool_name}\n")
            if cwd:
                tty.write(f"   Dir: {cwd}\n")
            tty.write(f"{content[:400]}\n")
            tty.write("Allow? [y/N] ")
            tty.flush()
            answer = tty.readline().strip().lower()
        if answer in ("y", "yes"):
            return "allow", ""
        return "deny", "Denied via terminal."
    except Exception:
        return "allow", ""  # no tty available, fail open


# Tools that are always safe — auto-approve without asking
_SAFE_TOOLS = {"Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoRead", "TodoWrite"}


def main() -> None:
    try:
        cfg = load_config()
        hook = json.load(sys.stdin)

        tool_name = hook.get("tool_name", "unknown")
        tool_input = hook.get("tool_input", {})
        session_id = hook.get("session_id", "default")
        cwd = hook.get("cwd", "")

        # Auto-approve everything inside Telegram-initiated sessions
        if os.environ.get("CLAUDE_TELEGRAM_INITIATED"):
            sys.exit(0)

        # Auto-approve read-only/safe tools immediately
        if tool_name in _SAFE_TOOLS:
            sys.exit(0)

        if should_use_telegram(cfg) and listener_running():
            # ── Away mode: Telegram approval ──────────────────────────────
            token = cfg["TELEGRAM_BOT_TOKEN"]
            chat_id = cfg["TELEGRAM_CHAT_ID"]

            send_approval_request(token, chat_id, tool_name, tool_input, session_id, cwd)
            decision = wait_for_decision(session_id)

            if decision == "approve":
                sys.exit(0)

            if decision == "allow_always":
                rule = build_allow_rule(tool_name, tool_input)
                write_allow_rule(cwd, rule)
                sys.exit(0)

            if decision.startswith("deny:"):
                reason = decision[5:].strip() or "Denied via Telegram."
            elif decision == "deny":
                reason = "Denied via Telegram."
            elif decision == "timeout":
                reason = "Timed out waiting for Telegram approval."
            else:
                reason = "Denied via Telegram."

            print(json.dumps({"decision": "block", "reason": reason}))
            sys.exit(2)

        elif sys.platform != "win32":
            # ── Terminal fallback (listener not running, Linux/macOS) ──────
            decision, reason = terminal_prompt(tool_name, tool_input, cwd)

        else:
            # ── At desk mode: desktop popup (Windows) ─────────────────────
            decision, reason = show_desktop_popup(tool_name, tool_input, cwd)

            if decision == "allow":
                print(json.dumps({"behavior": "allow"}))
                sys.exit(0)

            if decision == "allow_always":
                rule = build_allow_rule(tool_name, tool_input)
                write_allow_rule(cwd, rule)
                    sys.exit(0)

            print(json.dumps({"decision": "block", "reason": reason}))
            sys.exit(2)

    except Exception as exc:
        sys.stderr.write(f"[telegram/approve] {exc}\n")
        sys.exit(0)  # Fail open so Claude isn't permanently stuck


if __name__ == "__main__":
    main()
