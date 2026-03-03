# Claude Code — Telegram Integration

Receive notifications when Claude finishes or waits for input, approve tool use (Bash, Edit, Write) from your phone, and send prompts to Claude remotely.

---

## Prerequisites

- Python 3.10+ installed and on PATH
- Claude Code CLI installed
- A Telegram account

---

## Step 1 — Create your Telegram bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** (looks like `123456:ABCdef...`)
4. Start a chat with your new bot (search for it by the username you chose)
5. Message **@userinfobot** to get your **chat ID** (a number like `1286841320`)

---

## Step 2 — Copy the files

Copy the `~/.claude/telegram/` folder to the same path on your machine:

```
~/.claude/telegram/
  approve.py          # handles tool approval (Telegram buttons or desktop popup)
  notify.py           # sends Stop / Notification events to Telegram
  listener.py         # background polling loop (runs persistently)
  run_claude.py       # runs claude -p and streams response to Telegram
  sessions.py         # session name store
  config.env.example  # credentials template — copy to config.env and fill in
```

---

## Step 3 — Configure credentials

Copy `config.env.example` to `config.env` and fill in your values:

```bash
cp config.env.example config.env
```

Edit `~/.claude/telegram/config.env`:

```env
TELEGRAM_BOT_TOKEN=<your-bot-token-from-BotFather>
TELEGRAM_CHAT_ID=<your-chat-id-from-userinfobot>

# Approval mode: auto | telegram | local
# auto     = Telegram when idle >= threshold, desktop popup when active
# telegram = always use Telegram (even when at desk)
# local    = always use desktop popup (Telegram only used for notifications)
APPROVAL_MODE=auto
IDLE_THRESHOLD_SECONDS=300
```

> **Keep `config.env` private** — it contains your bot token and chat ID.

---

## Step 4 — Configure Claude Code hooks

Create or edit `~/.claude/settings.json`. Replace `C:/Users/<you>` with your actual home path:

```json
{
  "permissions": {
    "allow": [
      "Bash(*)", "Edit(*)", "Write(*)", "NotebookEdit(*)",
      "WebSearch(*)", "WebFetch(*)",
      "Read(*)", "Glob(*)", "Grep(*)",
      "Task(*)", "TodoRead(*)", "TodoWrite(*)"
    ]
  },
  "hooks": {
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python C:/Users/<you>/.claude/telegram/notify.py stop"
      }]
    }],
    "Notification": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python C:/Users/<you>/.claude/telegram/notify.py notification"
      }]
    }],
    "PreToolUse": [
      { "matcher": "Bash",        "hooks": [{ "type": "command", "command": "python C:/Users/<you>/.claude/telegram/approve.py" }] },
      { "matcher": "Edit",        "hooks": [{ "type": "command", "command": "python C:/Users/<you>/.claude/telegram/approve.py" }] },
      { "matcher": "Write",       "hooks": [{ "type": "command", "command": "python C:/Users/<you>/.claude/telegram/approve.py" }] },
      { "matcher": "NotebookEdit","hooks": [{ "type": "command", "command": "python C:/Users/<you>/.claude/telegram/approve.py" }] }
    ]
  }
}
```

> **Windows tip:** You can use `%USERPROFILE%` instead of `C:/Users/<you>` to make the config portable across user accounts.

---

## Step 5 — Start the listener

Run this in a dedicated terminal (keep it running in the background):

```bash
python ~/.claude/telegram/listener.py
```

On Windows:
```bash
python %USERPROFILE%/.claude/telegram/listener.py
```

---

## What you get

| Event                               | Telegram message                                             |
| ----------------------------------- | ------------------------------------------------------------ |
| Claude finishes a task              | ✅ Done — session name + last prompt + Claude's response      |
| Claude is waiting for input         | ⏳ Waiting for input — response shown for context             |
| Claude is working                   | ⌛ Live activity log — each tool call shown as it runs        |
| Claude wants to run Bash/Edit/Write | 🔧 Permission Request — full command + 4 approval buttons    |
| Claude needs info (notification)    | 🔔 Notice — message text                                      |

**Done notifications** include a **▶ Continue** button — tap it, then type your next task to resume the same session remotely.

### Approval buttons

Each tool request shows a 2×2 button layout:

| Button | Action |
| ------ | ------ |
| ✅ Approve | Allow this one tool call |
| 🔒 Always allow | Allow and write a permanent rule to `settings.json` |
| ❌ Deny | Block the tool call |
| 💬 Deny with feedback | Block and send a reason to Claude |

The same 4 buttons are shown in the desktop popup when you are at your machine.

---

## Telegram commands

| Command      | Action                                                                      |
| ------------ | --------------------------------------------------------------------------- |
| `<any text>` | Send a prompt to Claude (session picker appears if multiple sessions exist) |
| `/new`       | Start a fresh conversation                                                  |
| `/sessions`  | List saved sessions with full IDs (for `claude --resume`)                   |
| `/rename`    | Pick a conversation to rename (shows inline session picker)                 |
| `/mode`      | Show current approval mode                                                  |
| `/mode auto` | Auto-detect: Telegram when idle ≥ threshold, popup when active              |
| `/mode telegram` | Always use Telegram for approvals                                       |
| `/mode local` | Always use desktop popup for approvals                                     |
| `/help`      | Show command list                                                           |

---

## Resuming a session at the CLI

After working remotely via Telegram, resume the same conversation in your terminal:

```bash
claude --resume <session-id>
```

Get the full session ID from `/sessions` in Telegram.

---

## Dependencies

Standard library only — no `pip install` required. Python 3.10+.
