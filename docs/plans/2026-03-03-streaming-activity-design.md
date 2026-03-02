# Streaming Activity Design

**Date:** 2026-03-03
**Status:** Approved

## Goal

Show all intermediate Claude activity (tool calls + results) as a live Telegram message while a task runs, then replace it with the final text response when done.

## Approach

**Approach B — Live activity log → final response**

One Telegram message is created at task start (`⌛ Working…`). It updates live as tools are called. When the task finishes, the entire message is replaced with Claude's final text response.

## Changes

Only `run_claude.py` is modified.

## Event Processing

| stream-json event | Action |
|-------------------|--------|
| `assistant` + `tool_use` block | Add tool entry to activity log (status: running) |
| `user` + `tool_result` block | Match by `tool_use_id`, mark done, store truncated result |
| `assistant` + `text` block | Accumulate into `final_text` |
| `assistant` + `thinking` block | Skip |
| `result` | Capture `session_id` |

## Live Message Format

```
⌛ Working…

🔧 Bash: `ls src/`
▸ file1.py  file2.py
✓

📖 Read: `src/main.py` ⏳
```

- Tool icons: `Bash` → 🔧, `Read` → 📖, `Write`/`Edit`/`NotebookEdit` → ✏️, others → 🔩
- Input snippet: `command` for Bash; `file_path` for file tools; truncated JSON for others (max 60 chars)
- Results: first 5 lines, `…` if more

## Final State

On completion, the live message is replaced with `final_text`. If no text was produced (pure tool task), show `_(Done — N tool calls)_`.

## Data Structures

```python
tool_calls: list[dict]  # [{id, name, snippet, result_lines, done}]
final_text: str         # accumulated from text blocks
```

## Overflow

If the live activity log exceeds 3800 chars, older entries are trimmed from the top with `…` prefix. Final response uses existing split logic.

## Out of Scope

- No changes to `listener.py`, `notify.py`, `approve.py`, or `sessions.py`
- No new dependencies
- Tool results are not sent as separate messages
