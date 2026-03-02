# Streaming Activity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show all intermediate Claude tool activity (name + input + truncated result) as a live Telegram message while a task runs, then replace it with the final text response when done.

**Architecture:** Only `run_claude.py` is modified. Three pure helper functions are added for formatting. The event loop is extended to parse `tool_use` and `tool_result` blocks in addition to existing `text` blocks. `thinking` blocks are explicitly skipped. At completion the live activity message is replaced by the final text response.

**Tech Stack:** Python 3.10+ stdlib only (no new dependencies). pytest for tests.

---

### Task 1: Add helper functions and unit tests

**Files:**
- Modify: `run_claude.py` (add 3 helper functions + 2 constants after line 21)
- Create: `tests/test_run_claude.py`

**Step 1: Create the test file**

```python
# tests/test_run_claude.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from run_claude import _format_input_snippet, _truncate_result, _render_activity


# --- _format_input_snippet ---

def test_bash_extracts_command():
    assert _format_input_snippet("Bash", {"command": "ls -la"}) == "ls -la"

def test_read_extracts_file_path():
    assert _format_input_snippet("Read", {"file_path": "src/main.py"}) == "src/main.py"

def test_edit_extracts_file_path():
    assert _format_input_snippet("Edit", {"file_path": "config.json"}) == "config.json"

def test_unknown_tool_serializes_json():
    result = _format_input_snippet("Glob", {"pattern": "**/*.py"})
    assert "**/*.py" in result

def test_long_input_truncated_to_60():
    long_cmd = "x" * 80
    result = _format_input_snippet("Bash", {"command": long_cmd})
    assert len(result) <= 60
    assert result.endswith("...")


# --- _truncate_result ---

def test_short_result_unchanged():
    assert _truncate_result("line1\nline2") == "line1\nline2"

def test_long_result_truncated_to_5_lines():
    text = "\n".join(f"line{i}" for i in range(10))
    result = _truncate_result(text)
    lines = result.splitlines()
    assert lines[-1] == "…"
    assert len(lines) == 6  # 5 content + ellipsis

def test_list_content_joined():
    content = [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
    result = _truncate_result(content)
    assert "hello" in result
    assert "world" in result

def test_empty_result_returns_empty():
    assert _truncate_result("") == ""
    assert _truncate_result(None) == ""


# --- _render_activity ---

def test_render_shows_working_header():
    result = _render_activity([])
    assert "Working" in result

def test_render_running_tool_shows_hourglass():
    calls = [{"name": "Bash", "snippet": "ls", "result_lines": "", "done": False}]
    result = _render_activity(calls)
    assert "⏳" in result
    assert "ls" in result

def test_render_done_tool_shows_checkmark():
    calls = [{"name": "Bash", "snippet": "ls", "result_lines": "file.py", "done": True}]
    result = _render_activity(calls)
    assert "✓" in result

def test_render_skips_thinking_not_present():
    # thinking blocks should never appear in tool_calls
    calls = [{"name": "Read", "snippet": "main.py", "result_lines": "", "done": False}]
    result = _render_activity(calls)
    assert "Read" in result
    assert "main.py" in result
```

**Step 2: Run tests to confirm they all fail**

```
cd C:\Users\ThinkPad\.claude\telegram
python -m pytest tests/test_run_claude.py -v
```

Expected: All tests FAIL with `ImportError` (functions don't exist yet).

**Step 3: Add the helper functions to `run_claude.py`**

After line 19 (`EDIT_INTERVAL = 0.8`), add:

```python
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
    text = "\n".join(parts)
    if len(text) > MAX_LEN:
        return "…" + text[-MAX_LEN:]
    return text
```

**Step 4: Run tests to confirm they pass**

```
python -m pytest tests/test_run_claude.py -v
```

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add run_claude.py tests/test_run_claude.py
git commit -m "feat: add tool activity helper functions with tests"
```

---

### Task 2: Update the event processing loop

**Files:**
- Modify: `run_claude.py` — replace the body of `run_and_stream` starting at `accumulated = ""`

**Context:** The current loop uses `accumulated` (str) and only reads text blocks. We replace it with `tool_calls` (list) + `final_text` (str) + `id_to_idx` (dict for matching tool results).

**Step 1: Replace variables and loop in `run_and_stream`**

Replace the section from `accumulated = ""` through `proc.wait()` (lines 89–134 in the original file) with:

```python
    tool_calls: list[dict] = []   # {id, name, snippet, result_lines, done}
    id_to_idx: dict[str, int] = {}
    final_text = ""
    new_session_id: str | None = None
    last_edit = 0.0

    try:
        proc = subprocess.Popen(
            popen_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
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
                    if btype == "thinking":
                        pass  # explicitly skip thinking blocks
                    elif btype == "text":
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
                        idx = id_to_idx.get(tool_id)
                        if idx is not None:
                            tool_calls[idx]["result_lines"] = _truncate_result(
                                block.get("content", ""))
                            tool_calls[idx]["done"] = True

            elif etype == "text":
                final_text += event.get("text", "")

            elif etype == "result":
                new_session_id = event.get("session_id")

            now = time.monotonic()
            if now - last_edit > EDIT_INTERVAL:
                if tool_calls and not final_text:
                    edit_message(token, chat_id, message_id,
                                 _render_activity(tool_calls))
                elif final_text:
                    edit_message(token, chat_id, message_id, _tail(final_text))
                last_edit = now

        proc.wait()

    except Exception as exc:
        final_text = final_text or f"_(Error: {exc})_"
```

**Step 2: Update final publish section**

Replace the old `if not accumulated:` / split block (lines 156–167 in original) with:

```python
    # Mirror final response to terminal for local monitoring
    if final_text:
        print(f"\n[Telegram → Claude]\n{final_text}\n", file=sys.stderr)

    # Final publish — replace live activity with the actual response
    if final_text:
        if len(final_text) <= MAX_LEN:
            edit_message(token, chat_id, message_id, final_text)
        else:
            edit_message(token, chat_id, message_id,
                         final_text[:MAX_LEN] + " _(cont.)_")
            rest = final_text[MAX_LEN:]
            while rest:
                send_message(token, chat_id, rest[:MAX_LEN])
                rest = rest[MAX_LEN:]
    elif tool_calls:
        edit_message(token, chat_id, message_id,
                     f"_(Done — {len(tool_calls)} tool call(s))_")
    else:
        edit_message(token, chat_id, message_id, "_(No response received)_")
```

**Step 3: Run the existing tests to confirm nothing broke**

```
python -m pytest tests/test_run_claude.py -v
```

Expected: All tests still PASS.

**Step 4: Commit**

```bash
git add run_claude.py
git commit -m "feat: stream tool activity as live Telegram message, replace with final response"
```

---

### Task 3: Smoke-test the full flow

**No code changes — manual verification only.**

**Step 1: Ensure listener is running**

```
python listener.py
```

**Step 2: Send a simple task via Telegram that uses at least one tool**

Example prompt: `list the files in the current directory`

**Step 3: Observe the live message**

Expected sequence:
1. `⌛ Working…` appears immediately
2. Message updates to show tool activity, e.g.:
   ```
   ⌛ Working…
   🔧 Bash: `ls` ⏳
   ```
3. After tool completes:
   ```
   ⌛ Working…
   🔧 Bash: `ls`
   ▸ file1.py  file2.py
   ✓
   ```
4. When Claude finishes, message is replaced with the text response.

**Step 4: Commit verification note (no code change needed)**

If all steps above work correctly, the feature is complete. No additional commit needed.
