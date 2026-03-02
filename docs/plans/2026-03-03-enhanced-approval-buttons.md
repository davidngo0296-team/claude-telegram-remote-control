# Enhanced Approval Buttons Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add two new Telegram approval buttons — 🔒 Always allow (writes a `permissions.allow` rule to `.claude/settings.json`) and 💬 Deny with feedback (collects a typed reason via force_reply and sends it to Claude as the denial reason).

**Architecture:** Extend the existing temp-file IPC protocol with two new decision strings: `allow_always` and `deny:<text>`. `listener.py` writes these to the temp file; `approve.py` reads and acts on them. New helper functions `build_allow_rule` and `write_allow_rule` are added to `approve.py` and fully unit-tested. The Telegram message button grid grows from 1×2 to 2×2.

**Tech Stack:** Python 3.10+ stdlib only (json, pathlib, os). pytest for tests.

---

### Task 1: Add `build_allow_rule` and `write_allow_rule` helpers to `approve.py`

**Files:**
- Modify: `approve.py` (add 2 functions after `format_tool_detail`)
- Modify: `tests/test_run_claude.py` → create NEW file `tests/test_approve.py`

**Context:** `approve.py` already imports `json`, `os`, `sys`. It will also need `pathlib.Path` — check if already imported; if not, add it.

---

**Step 1: Create `tests/test_approve.py` with failing tests**

```python
# tests/test_approve.py
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from approve import build_allow_rule, write_allow_rule


# ── build_allow_rule ────────────────────────────────────────────────────────

def test_bash_uses_first_word_with_wildcard():
    assert build_allow_rule("Bash", {"command": "git status"}) == "Bash(git *)"

def test_bash_multi_word_takes_only_first():
    assert build_allow_rule("Bash", {"command": "npm run build"}) == "Bash(npm *)"

def test_bash_single_word_command():
    assert build_allow_rule("Bash", {"command": "ls"}) == "Bash(ls *)"

def test_bash_empty_command_returns_bare_bash():
    assert build_allow_rule("Bash", {"command": ""}) == "Bash"

def test_bash_missing_command_key_returns_bare_bash():
    assert build_allow_rule("Bash", {}) == "Bash"

def test_edit_returns_edit():
    assert build_allow_rule("Edit", {"file_path": "foo.py"}) == "Edit"

def test_write_returns_edit():
    # Write and Edit share the same permission umbrella per Claude docs
    assert build_allow_rule("Write", {"file_path": "foo.py"}) == "Edit"

def test_notebook_edit_returns_edit():
    assert build_allow_rule("NotebookEdit", {"notebook_path": "nb.ipynb"}) == "Edit"

def test_read_returns_read():
    assert build_allow_rule("Read", {"file_path": "foo.py"}) == "Read"

def test_unknown_tool_returns_tool_name():
    assert build_allow_rule("WebFetch", {"url": "https://example.com"}) == "WebFetch"


# ── write_allow_rule ────────────────────────────────────────────────────────

def test_creates_settings_file_and_directory_if_missing(tmp_path):
    write_allow_rule(str(tmp_path), "Bash(git *)")
    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "Bash(git *)" in data["permissions"]["allow"]

def test_appends_to_existing_allow_list(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({"permissions": {"allow": ["Read"]}}))
    write_allow_rule(str(tmp_path), "Bash(git *)")
    data = json.loads(settings_path.read_text())
    assert "Read" in data["permissions"]["allow"]
    assert "Bash(git *)" in data["permissions"]["allow"]

def test_does_not_duplicate_existing_rule(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({"permissions": {"allow": ["Bash(git *)"]}}))
    write_allow_rule(str(tmp_path), "Bash(git *)")
    data = json.loads(settings_path.read_text())
    assert data["permissions"]["allow"].count("Bash(git *)") == 1

def test_preserves_existing_settings_keys(tmp_path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(json.dumps({"model": "claude-opus-4-6", "permissions": {"deny": ["Bash(rm *)"]}}))
    write_allow_rule(str(tmp_path), "Edit")
    data = json.loads(settings_path.read_text())
    assert data["model"] == "claude-opus-4-6"
    assert "Bash(rm *)" in data["permissions"]["deny"]
    assert "Edit" in data["permissions"]["allow"]

def test_falls_back_to_home_if_cwd_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    write_allow_rule("", "Edit")
    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "Edit" in data["permissions"]["allow"]

def test_falls_back_to_home_if_cwd_nonexistent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    write_allow_rule("/nonexistent/path/xyz", "Read")
    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
```

**Step 2: Run tests to confirm they FAIL**

```
cd C:\Users\ThinkPad\.claude\telegram
python -m pytest tests/test_approve.py -v
```

Expected: `ImportError: cannot import name 'build_allow_rule' from 'approve'`

**Step 3: Add `pathlib.Path` import to `approve.py`**

Find the imports block at the top of `approve.py`. Add `from pathlib import Path` after the existing imports if not already present.

**Step 4: Add the two helper functions to `approve.py`**

Insert after the `format_tool_detail` function (around line 231):

```python
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
        except Exception:
            data = {}

        permissions = data.setdefault("permissions", {})
        allow_list = permissions.setdefault("allow", [])

        if rule not in allow_list:
            allow_list.append(rule)
            settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        sys.stderr.write(f"[telegram/approve] write_allow_rule failed: {exc}\n")
```

**Step 5: Run tests to confirm they PASS**

```
python -m pytest tests/test_approve.py -v
```

Expected: All 16 tests PASS.

**Step 6: Run full suite to confirm no regressions**

```
python -m pytest tests/ -v
```

Expected: All 31 tests PASS (15 existing + 16 new).

**Step 7: Commit**

```bash
git add approve.py tests/test_approve.py
git commit -m "feat: add build_allow_rule and write_allow_rule helpers with tests"
```

---

### Task 2: Update `approve.py` — 2×2 buttons and new decision handling

**Files:**
- Modify: `approve.py` — `send_approval_request` and `main`

**Context:** `send_approval_request` currently sends a 1-row inline keyboard with Approve and Deny. `main()` currently handles `approve`, `deny`, and `timeout`. We extend both.

---

**Step 1: Update `send_approval_request` button grid**

Find this section in `send_approval_request` (around line 295):

```python
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"approve:{session_id}"},
                {"text": "❌ Deny",    "callback_data": f"deny:{session_id}"},
            ]]
        },
```

Replace with:

```python
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
```

**Step 2: Update `main()` decision handling**

Find this block in `main()` (around line 343):

```python
            if decision == "approve":
                sys.exit(0)

            reason = (
                "Timed out waiting for Telegram approval (120s)."
                if decision == "timeout"
                else "Denied via Telegram."
            )
            print(json.dumps({"decision": "block", "reason": reason}))
            sys.exit(2)
```

Replace with:

```python
            if decision == "approve":
                sys.exit(0)

            if decision == "allow_always":
                rule = build_allow_rule(tool_name, tool_input)
                write_allow_rule(cwd, rule)
                sys.exit(0)

            if decision.startswith("deny:"):
                reason = decision[5:].strip() or "Denied via Telegram."
            elif decision == "timeout":
                reason = "Timed out waiting for Telegram approval."
            else:
                reason = "Denied via Telegram."

            print(json.dumps({"decision": "block", "reason": reason}))
            sys.exit(2)
```

**Step 3: Run full test suite**

```
python -m pytest tests/ -v
```

Expected: All 31 tests still PASS (no regressions — these changes are not covered by unit tests, but they must not break existing tests).

**Step 4: Commit**

```bash
git add approve.py
git commit -m "feat: add 2x2 approval buttons and allow_always/deny-with-feedback handling"
```

---

### Task 3: Update `listener.py` — handle new callbacks and feedback state

**Files:**
- Modify: `listener.py` — add `_pending_feedback`, extend `handle_callback`, extend `handle_text_message`

**Context:** `listener.py` uses module-level dicts `_pending_prompts` and `_pending_sessions` for state. We add `_pending_feedback`. The `handle_callback` function dispatches on `action` with `if/elif` chains. `handle_text_message` checks commands then state, then routes to Claude — we add a feedback check at the very top.

---

**Step 1: Add `_pending_feedback` state dict**

Find these lines (around line 53):

```python
# Pending prompts: maps chat_id → prompt text (waiting for session selection)
_pending_prompts: dict[str, str] = {}

# Pending sessions: maps chat_id → session_id (user tapped ▶ Continue, waiting for prompt)
_pending_sessions: dict[str, str] = {}
```

Add immediately after:

```python
# Pending feedback: maps chat_id → session_id (user tapped 💬 Deny with feedback)
_pending_feedback: dict[str, str] = {}
```

**Step 2: Add `allow_always` and `feedback` handlers in `handle_callback`**

Find this block (around line 73):

```python
    # ── Tool approval (approve/deny) ───────────────────────────────────────
    if action in ("approve", "deny"):
        session_id = payload
        with open(approval_file(session_id), "w") as f:
            f.write(action)

        label = "✅ Approved!" if action == "approve" else "❌ Denied"
        _api_post(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": label,
        })
        _api_post(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"{'✅ *Approved*' if action == 'approve' else '❌ *Denied*'}",
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": []},
        })
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {action.upper():6s} — session {session_id[:8]}")
```

Replace with:

```python
    # ── Tool approval ──────────────────────────────────────────────────────
    if action in ("approve", "deny"):
        session_id = payload
        with open(approval_file(session_id), "w") as f:
            f.write(action)

        label = "✅ Approved!" if action == "approve" else "❌ Denied"
        _api_post(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": label,
        })
        _api_post(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"{'✅ *Approved*' if action == 'approve' else '❌ *Denied*'}",
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": []},
        })
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {action.upper():6s} — session {session_id[:8]}")

    elif action == "allow_always":
        session_id = payload
        with open(approval_file(session_id), "w") as f:
            f.write("allow_always")
        _api_post(token, "answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "🔒 Always allowed!",
        })
        _api_post(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": "🔒 *Always allowed*",
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": []},
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
        _api_post(token, "editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": "💬 *Awaiting feedback…*",
            "parse_mode": "Markdown",
            "reply_markup": {"inline_keyboard": []},
        })
        _api_post(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "What should Claude do instead?",
            "reply_markup": {"force_reply": True, "selective": True},
        })
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] FDBK_WAIT — session {session_id[:8]}")
```

**Step 3: Add feedback interception at the top of `handle_text_message`**

Find the top of `handle_text_message` (around line 145):

```python
def handle_text_message(token: str, chat_id: str, text: str) -> None:
    text = text.strip()

    # ── Commands ───────────────────────────────────────────────────────────
    if text.lower() in ("/new", "/reset"):
```

Insert immediately after `text = text.strip()`:

```python
    # ── Pending deny-with-feedback reply ───────────────────────────────────
    if chat_id in _pending_feedback:
        session_id = _pending_feedback.pop(chat_id)
        reason = text or "Denied via Telegram."
        with open(approval_file(session_id), "w") as f:
            f.write(f"deny:{reason}")
        send_message(token, chat_id, f"💬 *Denied with feedback:* _{reason[:100]}_")
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] FDBK_SENT — session {session_id[:8]}: {reason[:60]}")
        return

```

**Step 4: Run full test suite**

```
python -m pytest tests/ -v
```

Expected: All 31 tests PASS.

**Step 5: Commit**

```bash
git add listener.py
git commit -m "feat: handle allow_always and deny-with-feedback callbacks in listener"
```

---

### Task 4: Smoke-test the full flow

**No code changes — manual verification only.**

**Step 1: Restart the listener**

Stop any running `listener.py`, then:

```
python listener.py
```

**Step 2: Trigger a permission request from Claude**

In another terminal, run any Claude command that requires approval (e.g. Bash command in a project with no pre-approved rules).

**Step 3: Verify button layout**

The Telegram message should show a 2×2 grid:
```
[ ✅ Approve ]    [ 🔒 Always allow ]
[ ❌ Deny    ]    [ 💬 Deny with feedback ]
```

**Step 4: Test "Deny with feedback"**

1. Tap 💬 Deny with feedback
2. Message changes to "💬 *Awaiting feedback…*"
3. A new force_reply message appears: "What should Claude do instead?"
4. Type a reason and send
5. Bot confirms: "💬 *Denied with feedback:* _your reason_"
6. Claude receives the denial reason

**Step 5: Test "Always allow"**

1. Trigger another permission request
2. Tap 🔒 Always allow
3. Message changes to "🔒 *Always allowed*"
4. Verify `<cwd>/.claude/settings.json` was updated with the correct rule
5. Trigger the same command again — no approval prompt should appear
