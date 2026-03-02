# Enhanced Approval Buttons Design

**Date:** 2026-03-03
**Status:** Approved

## Goal

Add two new approval options to the Telegram permission request message:
- **🔒 Always allow** — approve + write a `permissions.allow` rule to the project's `.claude/settings.json`
- **💬 Deny with feedback** — deny with a user-typed reason sent to Claude via force_reply

## Button Layout

```
[ ✅ Approve ]        [ 🔒 Always allow ]
[ ❌ Deny    ]        [ 💬 Deny with feedback ]
```

Callback data format:
- `approve:<session_id>` — allow once (unchanged)
- `allow_always:<session_id>` — allow + write rule
- `deny:<session_id>` — deny with default reason (unchanged)
- `feedback:<session_id>` — collect typed reason, then deny

## Architecture

**Approach: Extended temp file protocol.**
`listener.py` writes to the temp file; `approve.py` reads and acts. `approve.py` owns all Claude-side logic (settings write, decision formatting). No new IPC mechanism needed.

## Files Changed

- `approve.py` — 4 changes:
  1. Update `send_approval_request` to send 2×2 button grid
  2. Add `build_allow_rule(tool_name, tool_input)` helper
  3. Add `write_allow_rule(cwd, rule)` helper
  4. Update `main()` to handle `allow_always` and `deny:<text>` decisions

- `listener.py` — 2 changes:
  1. Add `_pending_feedback: dict[str, str]` state (`chat_id → session_id`)
  2. Update `handle_callback` to handle `allow_always` and `feedback` actions
  3. Update `handle_text_message` to detect and route pending feedback replies

## Data Flow

### ✅ Approve (unchanged)
1. User taps → listener writes `approve` to temp file → message edited to ✅ Approved
2. `approve.py` reads `approve` → exits 0

### 🔒 Always allow
1. User taps → listener writes `allow_always` to temp file → message edited to 🔒 Always allowed
2. `approve.py` reads `allow_always` → calls `build_allow_rule` + `write_allow_rule` → exits 0

### ❌ Deny (unchanged)
1. User taps → listener writes `deny` to temp file → message edited to ❌ Denied
2. `approve.py` reads `deny` → prints `{decision: block, reason: "Denied via Telegram."}` → exits 2

### 💬 Deny with feedback
1. User taps → listener stores `_pending_feedback[chat_id] = session_id`
2. Listener edits approval message to 💬 *Awaiting feedback…* (buttons removed)
3. Listener sends new message with `force_reply: true`: "What should Claude do instead?"
4. User types reply → listener detects `_pending_feedback[chat_id]`, clears state
5. Listener writes `deny:<user_text>` to temp file, sends confirmation message
6. `approve.py` reads `deny:<text>` → strips prefix → uses text as reason → exits 2

## Rule Construction (`build_allow_rule`)

| Tool | Rule written |
|------|-------------|
| `Bash` | `Bash(<first-word> *)` — e.g. command `git status` → `Bash(git *)` |
| `Edit`, `Write`, `NotebookEdit` | `Edit` (covers all file edit tools per Claude docs) |
| Anything else | Tool name only, e.g. `WebFetch` |

## Settings Write (`write_allow_rule`)

Target: `<cwd>/.claude/settings.json` under `permissions.allow`.
Fallback: `~/.claude/settings.json` if `cwd` is empty or invalid.

1. Load existing JSON (or start with `{}`)
2. Ensure `permissions.allow` list exists
3. Skip write if rule already present (no duplicates)
4. Write back with `indent=2`

Errors (parse failure, write failure) are logged to stderr only — always exit 0 so Claude isn't blocked.

## Out of Scope

- No change to `notify.py`, `run_claude.py`, or `sessions.py`
- No new dependencies
- Desktop popup (`show_desktop_popup`) unchanged
- "Approve for session only" (not permanent) not implemented — the 2 existing options (once vs always) are sufficient
