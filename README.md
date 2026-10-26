# Wingman — Personal AI Operator

> A local-first, self-hosted AI operator that **actually does things**: web research, files,
> Python/shell, SSH/VPS, Gmail, a document knowledge base, autonomous multi-agent runs — and
> a Telegram bot with the same powers.

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](#requirements)
[![Version](https://img.shields.io/badge/version-1.2.0-ff2d87)](https://github.com/arpitsingh404/wingman)
[![License](https://img.shields.io/badge/license-MIT-ff2d87)](LICENSE)
[![Backend](https://img.shields.io/badge/backend-Python%20stdlib-2ea44f)](#architecture)
[![Local-first](https://img.shields.io/badge/local--first-127.0.0.1-ff2d87)](#security-notes)

Wingman runs entirely on your machine. There is no build step, no database server and no
cloud account — just one Python file, a vanilla JS frontend and a JSON state file. Your API
keys never leave your laptop.

---

## Features

| Area | What you get |
| --- | --- |
| **Chat** | Multi-model chat (OpenRouter / DeepSeek / Groq / Gemini), markdown rendering, per-chat memory |
| **Autonomous runs** | Give a goal → planner → researcher / developer / tester → reviewer. Max 8 steps, pause / resume / cancel |
| **Knowledge base** | Upload PDF/TXT/MD/CSV into `docs/`, chunked + indexed; the AI answers with `search_docs` |
| **Web** | Real web search (DuckDuckGo) + URL fetch/extract |
| **Files** | Sandboxed `workspace/` read/write/list |
| **Code & shell** | Sandboxed Python (`python -I`, 20s) and local shell with a destructive-command denylist |
| **SSH / VPS** | Run commands on a configured remote server (password or SSH key) |
| **Email** | Gmail OAuth (recommended) or App Password fallback |
| **Telegram** | Auto-owner binding, typing indicator, reactions (👀 → ✅), document ingest, read-only web mirror |
| **Tasks & notes** | The AI can add/complete/delete tasks and save notes via tools |
| **Safety** | Per-tool approval toggles, denylist, audit log, tool-loop limits |
| **UI** | Dark neumorphic theme, Inter/Sora/JetBrains Mono, responsive drawers |

---

## Architecture

```
Browser (vanilla HTML/CSS/JS)
        │  fetch /api/*
        ▼
server.py  ── ThreadingHTTPServer ── agent loop (max tool steps)
        │
        ├── tools: web_search · fetch_url · search_docs · files · python · shell · ssh_exec · email · tasks/notes
        ├── runs engine: planner → researcher/developer/tester → reviewer
        ├── Telegram poller thread (long-poll, offset persisted)
        └── data.json (+ data.bak), atomic writes
```

- **No framework, no bundler.** The HTTP server is Python stdlib; the frontend is plain
  HTML/CSS/JS.
- **Atomic persistence.** State is written to a temp file and swapped in; a `data.bak`
  copy is kept.
- **Bounded everything.** Chat history, payload size, tool steps, run duration and document
  chunks all have hard limits.

---

## Requirements

- Python **3.11+**
- `paramiko` (SSH) and `pypdf` (PDF text extraction) — both in `requirements.txt`
- At least one model provider API key

---

## Quick start

```powershell
git clone https://github.com/arpitsingh404/wingman.git
cd wingman

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# edit .env → add at least one provider key
python server.py
```

Open <http://127.0.0.1:8200/>. On Windows you can also double-click `start.bat`.

> Prefer another port? Set `WINGMAN_PORT` in `.env` or the environment.

---

## Configuration (`.env`)

```ini
# Server
WINGMAN_HOST=127.0.0.1      # keep localhost — LAN exposure has no auth
WINGMAN_PORT=8200

# Model providers (set the ones you use)
OPENROUTER_API_KEY=
DEEPSEEK_API_KEY=
GROQ_API_KEY=
GEMINI_API_KEY=

# Gmail — App Password fallback; OAuth is configured in the UI
WINGMAN_GMAIL_USER=
WINGMAN_GMAIL_PASS=
WINGMAN_GMAIL_CLIENT_ID=
WINGMAN_GMAIL_CLIENT_SECRET=

# Telegram bot
WINGMAN_TELEGRAM_TOKEN=
WINGMAN_TELEGRAM_BOT=

# SSH / VPS (or configure in the UI)
WINGMAN_SSH_PASSWORD=
```

Secrets are read at startup and are **never** returned by the API — only booleans such as
`gmail_configured` / `ssh_password_set`.

---

## Tools (all real)

| Tool | Approval required | Notes |
| --- | --- | --- |
| `web_search`, `fetch_url` | — | DuckDuckGo + page text extraction |
| `search_docs` | — | keyword search over uploaded documents |
| `read_file`, `list_files` | — | sandboxed to `workspace/` |
| `write_file` | `allow_risky` | sandboxed |
| `run_python` | `allow_risky` | isolated `python -I`, 20s timeout |
| `run_shell` | `allow_risky` | local, 20s, denylist |
| `ssh_exec` | `allow_ssh` | remote host from settings, 30s, denylist |
| `send_email` | `allow_email` | Gmail OAuth or SMTP App Password |
| `add_task` / `complete_task` / `add_note` / `search_docs` | — | persistent / read-only |

Approvals live in **Settings → Permissions**. Destructive commands (`rm -rf /`, `mkfs`,
`dd`, `wipefs`, `shutdown`, `passwd`, `iptables -P`, …) are blocked. Every shell/SSH command
is appended to `ssh_audit.log`.

### Knowledge base

**Files** tab → choose a `.pdf` / `.txt` / `.md` / `.csv` / `.json` file → **Upload**.
Wingman extracts the text, splits it into ~900-character chunks and stores the index in
`docs_index.json`. Then ask in chat:

> *"aurora-plan.txt ke hisaab se budget aur deadline batao"*

The model calls `search_docs`, receives the matching chunks with file names, and answers from
them. Limits: 8 MB per file, up to 500 chunks per document. Delete a document from the same
tab — file and index entries are removed together.

---

## Autonomous runs

**Runs** tab → describe a goal (for example *"VPS ka disk aur memory check karke report
save karo"*). Wingman:

1. Plans the work.
2. Picks specialist roles (researcher / developer / tester).
3. Executes steps with the same real tools.
4. Has a reviewer re-verify the result before finishing.

Runs are capped at **8 steps / 15 minutes** and can be paused, resumed or cancelled live.
Step-by-step progress streams into the Runs tab.

---

## Telegram bot

1. `@BotFather` → `/newbot` → copy the token.
2. Wingman → **Settings → Telegram** → paste token → **Save** (auto-enables).
3. Send **`/start`** (or any message) — the first sender becomes the **owner**
   (auto-bind, no verify code).
4. Everyone else gets *"private bot"*.

Commands: `/start`, `/help`, `/whoami`, `/tasks`, `/notes`, `/clear`.

**Send a document** (`.pdf`, `.txt`, `.md`, `.csv`, `.json`, code/log files, ≤ 20 MB) to the
bot — Wingman downloads it, indexes it into the same knowledge base as the **Files** tab, and
replies with the chunk count. Then ask *"invoice.pdf ka total batao"* right there in Telegram.

The bot reacts 👀 on receipt and ✅ after replying, and shows **typing…** while the model is
working. Telegram conversations are mirrored into the web panel as a **read-only** chat
(`TG` badge). Web chats are **never** sent to Telegram.

---

## Gmail (OAuth — recommended)

1. Google Cloud Console → enable the **Gmail API**.
2. OAuth consent screen → add yourself as a test user.
3. Create an **OAuth client ID** (Web application) with redirect URI:
   `http://127.0.0.1:8200/oauth/callback`
4. Paste Client ID + Secret in **Settings → Gmail → Connect Gmail**.
5. Enable **Allow sending emails**.

Scopes: `gmail.send`, `gmail.readonly`. Tokens are stored locally in `gmail_oauth.json`
(gitignored).

---

## SSH / VPS

Configure host, port, user and auth method in **Settings → SSH / VPS**. Prefer **SSH keys**:

```bash
ssh-keygen -t ed25519
ssh-copy-id root@YOUR_SERVER
# then set auth = SSH Key + key path, and disable password login:
# PasswordAuthentication no
```

Password auth works, but the password is stored locally in `.env`. Rotate any password that
has ever been shared in a chat.

---

## Data & privacy

- State: `data.json` (+ `data.bak`), atomic writes, bounded history.
- Workspace: `workspace/`.
- Documents: `docs/` + `docs_index.json`.
- Audit: `ssh_audit.log`.
- Secrets: `.env`, `gmail_oauth.json` (both gitignored).
- Nothing leaves your machine except the calls you make to the model / Telegram / Gmail APIs.

---

## API (local)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | version, uptime, providers, flags |
| GET | `/api/state` | chats, tasks, notes, settings |
| POST | `/api/chat` | `{text}` → agent run |
| POST | `/api/chat/new` / `select` / `delete` / `clear` | chat management |
| POST | `/api/clear-all`, `/api/reset` | bulk clear |
| POST | `/api/tasks`, `/api/notes` | CRUD |
| POST | `/api/settings` | provider/model/flags/gmail/telegram |
| POST | `/api/ssh/config`, `/api/ssh/test` | SSH setup |
| GET | `/api/gmail/auth-url`, `/oauth/callback` | Gmail OAuth |
| POST | `/api/gmail/config`, `/api/gmail/disconnect` | Gmail OAuth setup |
| GET | `/api/docs` | list indexed documents |
| POST | `/api/docs/upload` | `{name, data:<base64>}` — extract + index (max 8 MB) |
| POST | `/api/docs/delete` | `{name}` — remove file + index |
| GET / POST | `/api/runs` | list / start an autonomous run |
| POST | `/api/runs/action` | pause / resume / cancel |

---

## Repo structure

```
wingman/
├── server.py          # HTTP server, agent loop, tools, Telegram bot, runs engine
├── static/
│   ├── index.html     # layout, settings modal, tabs (Tasks / Notes / Runs / Files)
│   ├── app.js         # state, markdown renderer, runs & files UI
│   └── style.css      # pink neumorphic theme
├── requirements.txt
├── .env.example
├── start.bat
└── README.md
```

---

## Security notes

- The server binds **127.0.0.1** by default. If you set `WINGMAN_HOST=0.0.0.0`, anyone on
  your network can reach the API — there is no login. Trusted networks only.
- Keep risky tools (`allow_risky`, `allow_ssh`, `allow_email`) off unless you need them.
- Rotate any key or password that has been pasted into a chat.
- The agent can make mistakes. Review destructive actions yourself.
- Use the same token/approval hygiene you would for any RPA tool.

---

## Roadmap

- [ ] Scheduled runs (daily briefing / cron jobs)
- [ ] Full backup & restore (state + documents)
- [ ] Streaming responses (SSE)
- [ ] Access token for LAN / remote deployment
- [ ] Embedding-based retrieval (current search is keyword-ranked)

---

## License

[MIT](LICENSE) © 2026 Arpit Singh

- GitHub: [@arpitsingh404](https://github.com/arpitsingh404)
- Repository: [wingman](https://github.com/arpitsingh404/wingman)
