#!/usr/bin/env python3
"""
WINGMAN — personal AI operator (local web app)

- Chat (multi-model), tasks, notes, voice
- REAL tools: web search/fetch, file read/write (sandbox), python exec, email (Gmail)
- Approval gates: risky tools + email opt-in
- Persistent state (JSON) + local workspace sandbox

Run:
    python wingman/server.py      # http://127.0.0.1:8200/
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import secrets
import shutil
import smtplib
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import paramiko

    PARAMIKO_OK = True
except Exception:  # noqa: BLE001
    PARAMIKO_OK = False

try:
    from pypdf import PdfReader

    PYPDF_OK = True
except Exception:  # noqa: BLE001
    PYPDF_OK = False

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
DATA_FILE = HERE / "data.json"
WORKSPACE = HERE / "workspace"
WINGMAN_ENV = HERE / ".env"
SSH_AUDIT = HERE / "ssh_audit.log"
DOCS_DIR = HERE / "docs"
DOCS_INDEX = HERE / "docs_index.json"

DENY_PATTERNS = [
    r"rm\s+-[a-z]*r[a-z]*f?\s+/(\s|$|\*)",
    r"rm\s+-rf\s+\*",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\b(shutdown|reboot|poweroff|halt)\b",
    r"^\s*passwd\b",
    r"\b(userdel|groupdel)\b",
    r"chmod\s+-R\s+777\s+/",
    r":\(\)\s*\{.*\};:",
    r"iptables\s+-F",
    r"iptables\s+-P\s+\w+\s+(DROP|ACCEPT)",
    r">\s*/etc/(passwd|shadow)",
    r"\btruncate\b.*/etc/",
    r"\b(wipefs|shred|fdisk|parted|sfdisk)\b",
    r"\bmkfs(\.\w+)?\b",
    r"\b(mount|umount)\b",
    r"\bchattr\b",
    r"systemctl\s+(stop|disable|mask)\s+(ssh|sshd|networking)",
    r"\bufw\s+disable\b",
    r"\bkill\s+-9\s+1\b",
]

PROVIDERS = {
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "models": {
            "nemotron": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "cohere": "cohere/north-mini-code:free",
        },
    },
    "deepseek": {
        "base": "https://api.deepseek.com",
        "models": {"flash": "deepseek-flash", "pro": "deepseek-v4-pro"},
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1",
        "models": {"llama": "llama-3.3-70b-versatile", "llama8b": "llama-3.1-8b-instant"},
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": {"flash": "gemini-2.0-flash", "flash-lite": "gemini-2.0-flash-lite"},
    },
}


def _load_env():
    for env in (ROOT / ".env", WINGMAN_ENV):
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

DEFAULT_KEYS = {
    "deepseek": os.environ.get("DEEPSEEK_API_KEY", ""),
    "openrouter": os.environ.get("OPENROUTER_API_KEY", ""),
    "groq": os.environ.get("GROQ_API_KEY", ""),
    "gemini": os.environ.get("GEMINI_API_KEY", ""),
}

APP_VERSION = "1.2.0"
START_TS = time.time()

SYSTEM_PROMPT = (
    "Tum Wingman ho — user ka personal AI operator. Hinglish me short, practical jawab do. "
    "Tumhare paas REAL tools hain: web_search, fetch_url, search_docs, read_file, write_file, list_files, run_python, run_shell, ssh_exec, "
    "send_email, aur task/note tools. Jab user kisi uploaded document ka reference de to pehle search_docs use karo. "
    "Jab kaam karne ka ho to tools use karo — bina kaam kiye dawa mat karo. "
    "Sandbox: files sirf workspace/ ke andar. Risky tools (write_file, run_python, send_email) sirf tab chalenge "
    "jab user ne settings me approval diya ho; agar approval nahi hai to saaf bolo ki settings me enable karo. "
    "Kisi bhi result ko fabricate mat karo — jo tool output aaya wahi batao. Markdown allowed."
)

TOOLS = [
    # ---- tasks / notes ----
    {"type": "function", "function": {"name": "add_task", "description": "Add a task/reminder.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "due": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "list_tasks", "description": "List pending tasks.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "complete_task", "description": "Mark a task done by title match.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "delete_task", "description": "Delete a task by title match.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "add_note", "description": "Save a note.", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}}, "required": ["title", "content"]}}},
    {"type": "function", "function": {"name": "list_notes", "description": "List notes.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_note", "description": "Delete a note by title match.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "get_datetime", "description": "Current local date/time.", "parameters": {"type": "object", "properties": {}}}},

    # ---- real web / file / code tools ----
    {"type": "function", "function": {"name": "web_search", "description": "Search the web (DuckDuckGo) and return top results (title, url, snippet).", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_url", "description": "Fetch a URL and return its readable text (truncated).", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "list_files", "description": "List files inside the workspace sandbox.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Relative path inside workspace (default '.')"}}}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file inside the workspace sandbox.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create/overwrite a text file inside the workspace sandbox. Requires approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_python", "description": "Execute Python code inside the workspace sandbox (20s timeout). Requires approval.", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "send_email", "description": "Send an email via the configured Gmail account. Requires approval + email enabled.", "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {"name": "search_docs", "description": "Search the uploaded document knowledge base (PDF/text) and return relevant snippets with file names.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command on the local machine inside the workspace sandbox (20s; dangerous commands blocked). Requires approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "ssh_exec", "description": "Run a shell command on the configured remote VPS over SSH (30s; dangerous commands blocked). Requires SSH approval.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
]

LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
def default_state():
    return {
        "chats": [],
        "active": None,
        "tasks": [],
        "notes": [],
        "telegram_chats": {},
        "telegram_offset": 0,
        "runs": [],
        "settings": {
            "provider": "openrouter",
            "model": "nemotron",
            "flags": {"allow_risky": False, "allow_email": False, "allow_ssh": False},
            "gmail_user": os.environ.get("WINGMAN_GMAIL_USER", ""),
            "telegram_enabled": False,
            "telegram_verify_code": "",
            "telegram_owner": None,
            "telegram_risky": False,
            "ssh_host": "",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_auth": "password",
            "ssh_key_path": "",
        },
    }


def _normalize_state(data):
    base = default_state()
    base.update(data or {})
    if not base.get("chats"):
        base["chats"] = [new_chat()]
        base["active"] = base["chats"][0]["id"]
    if not base.get("active") or not any(c["id"] == base["active"] for c in base["chats"]):
        base["active"] = base["chats"][0]["id"]
    settings = base.setdefault("settings", {})
    flags = settings.setdefault("flags", {})
    for key in ("allow_risky", "allow_email", "allow_ssh"):
        flags.setdefault(key, False)
    settings.setdefault("gmail_user", os.environ.get("WINGMAN_GMAIL_USER", ""))
    settings.setdefault("telegram_enabled", False)
    settings.setdefault("telegram_verify_code", "")
    settings.setdefault("telegram_owner", None)
    settings.setdefault("telegram_risky", False)
    settings.setdefault("ssh_host", "")
    settings.setdefault("ssh_port", 22)
    settings.setdefault("ssh_user", "root")
    settings.setdefault("ssh_auth", "password")
    settings.setdefault("ssh_key_path", "")
    base.setdefault("telegram_chats", {})
    base.setdefault("telegram_offset", 0)
    base.setdefault("runs", [])
    return base


def load_state():
    for path in (DATA_FILE, DATA_FILE.with_suffix(".bak")):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _normalize_state(data)
        except Exception:  # noqa: BLE001
            continue
    st = default_state()
    chat = new_chat()
    st["chats"].append(chat)
    st["active"] = chat["id"]
    return st


def save_state(state):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    # keep chat history bounded (avoids huge state files)
    for chat in state.get("chats", []):
        messages = chat.get("messages") or []
        if len(messages) > 200:
            chat["messages"] = messages[-200:]
    raw = json.dumps(state, ensure_ascii=False, indent=1)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(raw, encoding="utf-8")
    try:
        if DATA_FILE.exists():
            shutil.copy2(DATA_FILE, DATA_FILE.with_suffix(".bak"))
    except Exception:  # noqa: BLE001
        pass
    os.replace(tmp, DATA_FILE)


def backfill_telegram(state):
    """Purani Telegram history ko web chat list me copy karo."""
    tchats = state.get("telegram_chats") or {}
    owner = state["settings"].get("telegram_owner") or {}
    name = owner.get("name") or owner.get("username") or "owner"
    changed = False
    for cid, msgs in tchats.items():
        if not msgs:
            continue
        web_id = "tg-" + str(cid)
        if any(c["id"] == web_id for c in state["chats"]):
            continue
        chat = {
            "id": web_id,
            "title": "Telegram · " + name,
            "messages": [],
            "updated": time.time(),
            "source": "telegram",
        }
        for m in msgs[-100:]:
            if m.get("role") in ("user", "assistant"):
                chat["messages"].append({
                    "role": m["role"],
                    "content": m.get("content", ""),
                    "ts": m.get("ts", time.time()),
                    "source": "telegram",
                })
        state["chats"].insert(0, chat)
        changed = True
    return changed


def new_chat():
    return {"id": secrets.token_hex(8), "title": "New chat", "messages": [], "updated": time.time()}


def active_chat(state):
    for c in state["chats"]:
        if c["id"] == state["active"]:
            return c
    chat = new_chat()
    state["chats"].append(chat)
    state["active"] = chat["id"]
    return chat


def active_web_chat(state):
    """Web chat only — never append web messages into a Telegram mirror."""
    chat = active_chat(state)
    if chat.get("source") == "telegram":
        chat = new_chat()
        state["chats"].append(chat)
        state["active"] = chat["id"]
    return chat


def flags(state):
    return state.get("settings", {}).get("flags", {})


def risky_ok(state):
    return bool(flags(state).get("allow_risky"))


def email_ok(state):
    if not flags(state).get("allow_email"):
        return False
    if oauth_connected():
        return True
    return bool(os.environ.get("WINGMAN_GMAIL_USER")) and bool(os.environ.get("WINGMAN_GMAIL_PASS"))


# ---------------------------------------------------------------------------
# sandbox helpers
# ---------------------------------------------------------------------------
def safe_path(rel: str) -> Path:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    p = (WORKSPACE / str(rel or ".")).resolve()
    if not str(p).startswith(str(WORKSPACE.resolve())):
        raise ValueError("path outside workspace sandbox")
    return p


def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def http_get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(1_500_000)
        charset = r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, "replace")


def is_dangerous(cmd: str) -> bool:
    low = (cmd or "").lower()
    return any(re.search(p, low) for p in DENY_PATTERNS)


def audit(kind, cmd, res):
    try:
        with open(SSH_AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind, "cmd": cmd, "ok": res.get("ok"), "rc": res.get("returncode")}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def local_shell(command: str, timeout: int = 20):
    proc = subprocess.run(command, shell=True, cwd=str(WORKSPACE), capture_output=True, text=True, timeout=timeout)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout[-6000:], "stderr": proc.stderr[-3000:]}


def ssh_run(state, command: str, timeout: int = 30):
    if not PARAMIKO_OK:
        raise ValueError("paramiko installed nahi hai (pip install paramiko)")
    s = state.get("settings", {})
    host = (s.get("ssh_host") or "").strip()
    if not host:
        raise ValueError("SSH host configured nahi hai (Settings → SSH/VPS)")
    port = int(s.get("ssh_port") or 22)
    user = (s.get("ssh_user") or "root").strip()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {"hostname": host, "port": port, "username": user, "timeout": 15, "banner_timeout": 20, "auth_timeout": 20}
    if s.get("ssh_auth") == "key":
        key_path = (s.get("ssh_key_path") or "").strip()
        if not key_path:
            raise ValueError("SSH key path missing")
        kwargs["key_filename"] = key_path
    else:
        pw = os.environ.get("WINGMAN_SSH_PASSWORD", "")
        if not pw:
            raise ValueError("SSH password missing")
        kwargs["password"] = pw
    kwargs["look_for_keys"] = False
    kwargs["allow_agent"] = False
    client.connect(**kwargs)
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")[-6000:]
        err = stderr.read().decode("utf-8", "replace")[-3000:]
        rc = stdout.channel.recv_exit_status()
        return {"ok": rc == 0, "returncode": rc, "stdout": out, "stderr": err}
    finally:
        client.close()


def save_ssh_password(pw):
    pw = (pw or "").strip()
    if not pw:
        return False
    lines = []
    if WINGMAN_ENV.exists():
        lines = [l for l in WINGMAN_ENV.read_text(encoding="utf-8").splitlines() if not l.startswith("WINGMAN_SSH_PASSWORD=")]
    lines.append("WINGMAN_SSH_PASSWORD=" + pw)
    WINGMAN_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["WINGMAN_SSH_PASSWORD"] = pw
    return True


# ---------------------------------------------------------------------------
# Document knowledge base (lightweight RAG)
# ---------------------------------------------------------------------------
def _docs_index_load():
    if DOCS_INDEX.exists():
        try:
            return json.loads(DOCS_INDEX.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"files": [], "chunks": []}


def _docs_index_save(index):
    DOCS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    tmp = DOCS_INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, DOCS_INDEX)


def _extract_text(name, data):
    lower = (name or "").lower()
    if lower.endswith(".pdf"):
        if not PYPDF_OK:
            raise ValueError("PDF support missing (pip install pypdf)")
        import io

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return data.decode("utf-8", "replace")


def _chunk_text(text, size=900, overlap=150):
    clean = re.sub(r"\s+", " ", text or "").strip()
    chunks = []
    i = 0
    while i < len(clean) and len(chunks) < 500:
        chunks.append(clean[i:i + size])
        i += max(1, size - overlap)
    return chunks


def index_document(name, text):
    index = _docs_index_load()
    index["files"] = [f for f in index.get("files", []) if f.get("name") != name]
    index["chunks"] = [c for c in index.get("chunks", []) if c.get("file") != name]
    chunks = _chunk_text(text)
    for i, chunk in enumerate(chunks):
        index["chunks"].append({"id": f"{name}#{i}", "file": name, "text": chunk})
    index["files"].append({"name": name, "chunks": len(chunks), "chars": len(text or ""), "ts": time.time()})
    _docs_index_save(index)
    return {"name": name, "chunks": len(chunks), "chars": len(text or "")}


def search_docs(query, limit=5):
    index = _docs_index_load()
    tokens = [t for t in re.findall(r"[a-zA-Z0-9\u0900-\u097F]+", (query or "").lower()) if len(t) > 2]
    if not tokens:
        return []
    scored = []
    for chunk in index.get("chunks", []):
        low = chunk.get("text", "").lower()
        score = sum(low.count(t) for t in tokens)
        if score:
            scored.append((score, chunk))
    scored.sort(key=lambda pair: -pair[0])
    return [
        {"file": chunk["file"], "score": score, "snippet": chunk["text"][:700]}
        for score, chunk in scored[:limit]
    ]


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
def _match(items, query, field="title"):
    q = (query or "").strip().lower()
    if not q:
        return None
    for item in items:
        if q in str(item.get(field, "")).lower():
            return item
    return None


def execute_tool(state, name, args, safe_only=False):
    # --- tasks / notes ---
    if name == "add_task":
        title = str(args.get("title") or "").strip()
        due = str(args.get("due") or "").strip()
        if not title:
            return {"ok": False, "error": "title required"}
        task = {"id": secrets.token_hex(6), "title": title, "due": due, "done": False, "ts": time.time()}
        state["tasks"].append(task)
        return {"ok": True, "message": f"Task added: {title}" + (f" (due {due})" if due else "")}
    if name == "list_tasks":
        tasks = [t for t in state["tasks"] if not t.get("done")]
        return {"ok": True, "tasks": tasks, "count": len(tasks)}
    if name == "complete_task":
        task = _match(state["tasks"], args.get("query"))
        if not task:
            return {"ok": False, "error": "task not found"}
        task["done"] = True
        return {"ok": True, "message": f"Task completed: {task['title']}"}
    if name == "delete_task":
        task = _match(state["tasks"], args.get("query"))
        if not task:
            return {"ok": False, "error": "task not found"}
        state["tasks"] = [t for t in state["tasks"] if t["id"] != task["id"]]
        return {"ok": True, "message": f"Task deleted: {task['title']}"}
    if name == "add_note":
        title = str(args.get("title") or "").strip() or "Note"
        content = str(args.get("content") or "").strip()
        note = {"id": secrets.token_hex(6), "title": title, "content": content, "ts": time.time()}
        state["notes"].append(note)
        return {"ok": True, "message": f"Note saved: {title}"}
    if name == "list_notes":
        return {"ok": True, "notes": state["notes"], "count": len(state["notes"])}
    if name == "delete_note":
        note = _match(state["notes"], args.get("query"))
        if not note:
            return {"ok": False, "error": "note not found"}
        state["notes"] = [n for n in state["notes"] if n["id"] != note["id"]]
        return {"ok": True, "message": f"Note deleted: {note['title']}"}
    if name == "get_datetime":
        now = time.localtime()
        return {"ok": True, "date": time.strftime("%Y-%m-%d", now), "time": time.strftime("%H:%M:%S", now), "weekday": time.strftime("%A", now)}

    # --- web ---
    if name == "web_search":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query required"}
        try:
            raw = http_get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
            results = []
            for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', raw, re.S):
                href = html.unescape(m.group(1))
                title = strip_html(m.group(2))
                if "uddg=" in href:
                    href = urllib.parse.unquote(href.split("uddg=", 1)[1].split("&", 1)[0])
                results.append({"title": title, "url": href})
                if len(results) >= 6:
                    break
            if not results:
                return {"ok": False, "error": "no results (site may block scraping)"}
            return {"ok": True, "results": results}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if name == "fetch_url":
        url = str(args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "url must start with http(s)://"}
        try:
            text = strip_html(http_get(url))
            return {"ok": True, "url": url, "text": text[:6000], "truncated": len(text) > 6000}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # --- files (sandbox, read-only safe) ---
    if name == "list_files":
        try:
            rel = str(args.get("path") or ".")
            p = safe_path(rel)
            if not p.exists():
                return {"ok": False, "error": "not found"}
            items = []
            for child in sorted(p.iterdir())[:100]:
                items.append({"name": child.name, "type": "dir" if child.is_dir() else "file", "size": child.stat().st_size if child.is_file() else 0})
            return {"ok": True, "path": rel, "items": items}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if name == "read_file":
        try:
            p = safe_path(args.get("path"))
            if not p.is_file():
                return {"ok": False, "error": "file not found"}
            if p.stat().st_size > 400_000:
                return {"ok": False, "error": "file too large"}
            return {"ok": True, "path": str(args.get("path")), "content": p.read_text(encoding="utf-8", errors="replace")[:12000]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if name == "write_file":
        if safe_only or not risky_ok(state):
            return {"ok": False, "error": "approval required: Settings me 'Allow risky tools' on karo"}
        try:
            p = safe_path(args.get("path"))
            p.parent.mkdir(parents=True, exist_ok=True)
            content = str(args.get("content") or "")
            p.write_text(content, encoding="utf-8")
            return {"ok": True, "message": f"File written: {args.get('path')} ({len(content)} chars)"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if name == "run_python":
        if safe_only or not risky_ok(state):
            return {"ok": False, "error": "approval required: Settings me 'Allow risky tools' on karo"}
        code = str(args.get("code") or "")
        if not code.strip():
            return {"ok": False, "error": "code required"}
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", code],
                cwd=str(WORKSPACE),
                capture_output=True,
                text=True,
                timeout=20,
            )
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:],
                "stderr": proc.stderr[-3000:],
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout (20s)"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if name == "send_email":
        if safe_only or not email_ok(state):
            return {"ok": False, "error": "email not enabled: Settings me Gmail app password + 'Allow email' on karo"}
        to = str(args.get("to") or "").strip()
        subject = str(args.get("subject") or "").strip()
        body = str(args.get("body") or "")
        if not to or not subject:
            return {"ok": False, "error": "to and subject required"}
        try:
            if oauth_connected():
                gmail_api_send(to, subject, body)
                return {"ok": True, "message": f"Email sent to {to} (Gmail OAuth)"}
            user = os.environ["WINGMAN_GMAIL_USER"]
            pwd = os.environ["WINGMAN_GMAIL_PASS"]
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = user
            msg["To"] = to
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=25) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(user, pwd)
                smtp.send_message(msg)
            return {"ok": True, "message": f"Email sent to {to}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    if name == "search_docs":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query required"}
        results = search_docs(query)
        if not results:
            return {"ok": False, "error": "no matching documents (upload docs first)"}
        return {"ok": True, "results": results}

    if name == "run_shell":
        if safe_only or not risky_ok(state):
            return {"ok": False, "error": "approval required: Settings me 'Allow risky tools' on karo"}
        cmd = str(args.get("command") or "")
        if not cmd.strip():
            return {"ok": False, "error": "command required"}
        if is_dangerous(cmd):
            return {"ok": False, "error": "blocked: dangerous command (manual karo)"}
        try:
            res = local_shell(cmd)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout (20s)"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        audit("local", cmd, res)
        return res

    if name == "ssh_exec":
        if safe_only or not flags(state).get("allow_ssh"):
            return {"ok": False, "error": "SSH approval required: Settings me 'Allow SSH commands' on karo"}
        cmd = str(args.get("command") or "")
        if not cmd.strip():
            return {"ok": False, "error": "command required"}
        if is_dangerous(cmd):
            return {"ok": False, "error": "blocked: dangerous command (manual karo)"}
        try:
            res = ssh_run(state, cmd)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        audit("ssh", cmd, res)
        return res

    return {"ok": False, "error": f"unknown tool {name}"}


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------
def call_api(provider, model_key, messages, timeout=150):
    prov = PROVIDERS.get(provider)
    if not prov:
        raise ValueError("unknown provider")
    model = prov["models"].get(model_key) or next(iter(prov["models"].values()))
    key = DEFAULT_KEYS.get(provider, "")
    if not key:
        raise ValueError(f"{provider} API key missing")
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.6,
        "max_tokens": 1400,
    }).encode("utf-8")
    req = urllib.request.Request(
        prov["base"] + "/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
            "HTTP-Referer": "http://localhost:8200",
            "X-Title": "Wingman",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_agent(state, messages, safe_only=False):
    provider = state["settings"].get("provider", "openrouter")
    model = state["settings"].get("model", "nemotron")
    actions = []
    for _ in range(6):
        try:
            data = call_api(provider, model, messages)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            return f"(AI error {exc.code}: {detail})", actions
        except Exception as exc:  # noqa: BLE001
            return f"(AI error: {exc})", actions

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return (msg.get("content") or "").strip(), actions

        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            result = execute_tool(state, name, args, safe_only=safe_only)
            if result.get("message"):
                actions.append(result["message"])
            elif result.get("ok") is False:
                actions.append(f"⚠ {name}: {result.get('error')}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or "",
                "content": json.dumps(result, ensure_ascii=False)[:12000],
            })
    return "(tool loop limit reached)", actions


# ---------------------------------------------------------------------------
# Autonomous multi-agent runs
# ---------------------------------------------------------------------------
AGENT_PROMPTS = {
    "planner": (
        "Tum PLANNER ho — autonomous AI operator ka planning agent. User ka goal dekh ke 3-7 concrete executable steps banao. "
        "Har step ke liye agent chuno: researcher, developer, tester, reviewer. "
        "SIRF valid JSON return karo, koi markdown nahi:\n"
        '{"steps":[{"title":"...","agent":"researcher"},{"title":"...","agent":"developer"}]}\n'
        "Steps chhote, specific aur verifiable hone chahiye."
    ),
    "researcher": (
        "Tum RESEARCHER ho. web_search/fetch_url tools se facts ikatthe karo, sources cite karo, short structured summary do. "
        "Jo verify nahi hua use 'unverified' bolo. Hinglish."
    ),
    "developer": (
        "Tum DEVELOPER ho. Step ko actually execute karo — files, python, shell ya SSH tools use karo. "
        "Jo command/code chalaya wo batao aur exact output do. Bina execute kiye claim mat karo. Hinglish."
    ),
    "tester": (
        "Tum TESTER ho. Result verify karo — tests/commands chalao, output check karo, pass/fail clearly batao. "
        "Fail ho to reason do. Hinglish."
    ),
    "reviewer": (
        "Tum REVIEWER ho. Poora run dekh ke honest final report do: kya hua, kya verify hua, kya pending/blocked hai. "
        "Kuch fabricate mat karo. Hinglish, short."
    ),
}

RUN_CONTROLS = {}


def _extract_json(text):
    match = re.search(r"\{[\s\S]*\}", text or "")
    if not match:
        return None
    raw = match.group(0)
    for candidate in (raw, raw.replace("\n", " ")):
        try:
            return json.loads(candidate)
        except Exception:  # noqa: BLE001
            continue
    return None


def _run_get(rid):
    with LOCK:
        st = load_state()
        return next((r for r in st.get("runs", []) if r["id"] == rid), None)


def _run_update(rid, **fields):
    with LOCK:
        st = load_state()
        for run in st.get("runs", []):
            if run["id"] == rid:
                run.update(fields)
                run["updated"] = time.time()
                save_state(st)
                return run
    return None


def _run_step_update(rid, index, **fields):
    with LOCK:
        st = load_state()
        for run in st.get("runs", []):
            if run["id"] == rid and 0 <= index < len(run.get("steps", [])):
                run["steps"][index].update(fields)
                run["updated"] = time.time()
                save_state(st)
                return run["steps"][index]
    return None


def _plan_run(rid):
    run = _run_get(rid)
    if not run:
        return
    plan_text, _ = run_agent(load_state(), [
        {"role": "system", "content": AGENT_PROMPTS["planner"]},
        {"role": "user", "content": "Goal: " + run["goal"]},
    ], safe_only=False)
    plan = _extract_json(plan_text) or {}
    steps = []
    for i, step in enumerate((plan.get("steps") or [])[:8]):
        if not isinstance(step, dict) or not step.get("title"):
            continue
        agent = step.get("agent") if step.get("agent") in ("researcher", "developer", "tester", "reviewer") else "researcher"
        steps.append({"id": i + 1, "title": str(step["title"])[:200], "agent": agent, "status": "pending", "result": ""})
    if not steps:
        steps = [
            {"id": 1, "title": "Goal research karo aur facts ikatthe karo", "agent": "researcher", "status": "pending", "result": ""},
            {"id": 2, "title": "Deliverable banao / execute karo", "agent": "developer", "status": "pending", "result": ""},
            {"id": 3, "title": "Result verify aur review karo", "agent": "tester", "status": "pending", "result": ""},
        ]
    _run_update(rid, steps=steps, plan_text=(plan_text or "")[:2000])


def run_worker(rid):
    control = RUN_CONTROLS.setdefault(rid, {"pause": threading.Event(), "cancel": threading.Event()})
    started = time.time()
    try:
        _plan_run(rid)
        _run_update(rid, status="running")
        while True:
            if control["cancel"].is_set():
                _run_update(rid, status="cancelled")
                return
            if control["pause"].is_set():
                _run_update(rid, status="paused")
                while control["pause"].is_set():
                    if control["cancel"].is_set():
                        _run_update(rid, status="cancelled")
                        return
                    time.sleep(1)
                _run_update(rid, status="running")
            if time.time() - started > 900:
                _run_update(rid, status="failed", error="timeout (15 min)")
                return

            run = _run_get(rid)
            if not run:
                return
            pending = next((s for s in run.get("steps", []) if s["status"] == "pending"), None)
            if not pending:
                break
            index = run["steps"].index(pending)
            _run_step_update(rid, index, status="running")

            prior = "\n".join(
                f"- {s['title']} ({s['status']}): {(s.get('result') or '')[:300]}"
                for s in run["steps"] if s is not pending
            )
            convo = [
                {"role": "system", "content": AGENT_PROMPTS.get(pending["agent"], AGENT_PROMPTS["researcher"])},
                {"role": "user", "content": (
                    f"Overall goal: {run['goal']}\n\nYour step: {pending['title']}\n\n"
                    f"Progress so far:\n{prior or '(none)'}\n\n"
                    "Execute this step now. Use tools if needed. Report exactly what you did and the result."
                )},
            ]
            st = load_state()
            result, actions = run_agent(st, convo, safe_only=False)
            with LOCK:
                for item in st.get("runs", []):
                    if item["id"] == rid and 0 <= index < len(item["steps"]):
                        item["steps"][index].update({
                            "status": "done",
                            "result": (result or "")[:4000],
                            "actions": actions,
                        })
                        item["updated"] = time.time()
                save_state(st)

        run = _run_get(rid)
        if not run:
            return
        summary = "\n".join(
            f"- {s['title']} ({s['agent']}, {s['status']}): {(s.get('result') or '')[:500]}"
            for s in run.get("steps", [])
        )
        final, _ = run_agent(load_state(), [
            {"role": "system", "content": AGENT_PROMPTS["reviewer"]},
            {"role": "user", "content": f"Goal: {run['goal']}\n\nStep results:\n{summary}\n\nFinal honest report do."},
        ], safe_only=False)
        _run_update(rid, status="done", result=(final or "")[:6000])
    except Exception as exc:  # noqa: BLE001
        _run_update(rid, status="failed", error=str(exc))
    finally:
        RUN_CONTROLS.pop(rid, None)


def start_run(goal):
    rid = secrets.token_hex(6)
    run = {
        "id": rid,
        "goal": goal,
        "status": "queued",
        "created": time.time(),
        "updated": time.time(),
        "steps": [],
        "result": "",
        "error": "",
    }
    with LOCK:
        st = load_state()
        st.setdefault("runs", []).insert(0, run)
        save_state(st)
    RUN_CONTROLS[rid] = {"pause": threading.Event(), "cancel": threading.Event()}
    threading.Thread(target=run_worker, args=(rid,), daemon=True).start()
    return run


def save_gmail(user, password):
    user = (user or "").strip()
    password = (password or "").strip()
    if not user or not password:
        return False
    WINGMAN_ENV.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if WINGMAN_ENV.exists():
        lines = [l for l in WINGMAN_ENV.read_text(encoding="utf-8").splitlines() if not l.startswith(("WINGMAN_GMAIL_USER=", "WINGMAN_GMAIL_PASS="))]
    lines.append("WINGMAN_GMAIL_USER=" + user)
    lines.append("WINGMAN_GMAIL_PASS=" + password)
    WINGMAN_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["WINGMAN_GMAIL_USER"] = user
    os.environ["WINGMAN_GMAIL_PASS"] = password
    return True


# ---------------------------------------------------------------------------
# Gmail OAuth 2.0
# ---------------------------------------------------------------------------
OAUTH_FILE = HERE / "gmail_oauth.json"
REDIRECT_URI = "http://127.0.0.1:8200/oauth/callback"
OAUTH_SCOPES = "openid email https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly"


def oauth_load():
    if OAUTH_FILE.exists():
        try:
            return json.loads(OAUTH_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def oauth_save(data):
    OAUTH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def oauth_client():
    d = oauth_load()
    return (
        d.get("client_id") or os.environ.get("WINGMAN_GMAIL_CLIENT_ID", ""),
        d.get("client_secret") or os.environ.get("WINGMAN_GMAIL_CLIENT_SECRET", ""),
    )


def oauth_connected():
    return bool(oauth_load().get("refresh_token"))


def oauth_auth_url():
    cid, _ = oauth_client()
    if not cid:
        return None
    params = {
        "client_id": cid,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def _oauth_token_request(data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def oauth_profile_email(access_token):
    req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": "Bearer " + access_token},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8")).get("email", "")


def oauth_exchange(code):
    cid, csec = oauth_client()
    if not cid or not csec:
        raise ValueError("client id/secret missing")
    tok = _oauth_token_request({
        "code": code,
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    d = oauth_load()
    d["refresh_token"] = tok.get("refresh_token", d.get("refresh_token", ""))
    d["access_token"] = tok.get("access_token", "")
    d["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    try:
        email = oauth_profile_email(d["access_token"])
        if email:
            d["email"] = email
    except Exception:  # noqa: BLE001
        pass
    oauth_save(d)
    return True


def oauth_access_token():
    d = oauth_load()
    if d.get("access_token") and d.get("expires_at", 0) > time.time() + 60:
        return d["access_token"]
    rt = d.get("refresh_token")
    cid, csec = oauth_client()
    if not rt or not cid or not csec:
        return None
    tok = _oauth_token_request({
        "client_id": cid,
        "client_secret": csec,
        "refresh_token": rt,
        "grant_type": "refresh_token",
    })
    d["access_token"] = tok.get("access_token", "")
    d["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    oauth_save(d)
    return d["access_token"]


def gmail_api_send(to, subject, body):
    token = oauth_access_token()
    if not token:
        raise ValueError("Gmail not connected")
    d = oauth_load()
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if d.get("email"):
        msg["From"] = d["email"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    payload = json.dumps({"raw": raw}).encode("utf-8")
    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=payload,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Telegram bot
# ---------------------------------------------------------------------------
TG_BASE = "https://api.telegram.org/bot{token}/{method}"


def tg_call(token, method, payload=None, timeout=35):
    url = TG_BASE.format(token=token, method=method)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def tg_clean(text):
    s = text or ""
    s = re.sub(r"```([\s\S]*?)```", r"\1", s)
    s = s.replace("**", "").replace("*", "").replace("`", "")
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"^\s*[-•]\s+", "• ", s, flags=re.M)
    return s


def tg_format(text):
    """Markdown → Telegram HTML (bold, code, headings, bullets)."""
    s = html.escape(text or "")
    s = re.sub(r"```([\s\S]*?)```", lambda m: "<pre>" + m.group(1).strip() + "</pre>", s)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"^#{1,6}\s*(.*)$", r"<b>\1</b>", s, flags=re.M)
    s = re.sub(r"^\s*[-*]\s+", "• ", s, flags=re.M)
    s = s.replace("*", "")
    return s


def tg_send(token, chat_id, text):
    text = text or "(empty)"
    formatted = tg_format(text)
    if len(formatted) <= 3900:
        try:
            tg_call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": formatted,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            return
        except Exception:  # noqa: BLE001
            pass
    # fallback: plain text (long messages / HTML parse errors)
    plain = tg_clean(text)
    for i in range(0, len(plain), 3900):
        try:
            tg_call(token, "sendMessage", {"chat_id": chat_id, "text": plain[i:i + 3900], "disable_web_page_preview": True})
        except Exception:  # noqa: BLE001
            pass


def save_telegram_token(token):
    token = (token or "").strip()
    if not token:
        return False
    lines = []
    if WINGMAN_ENV.exists():
        lines = [l for l in WINGMAN_ENV.read_text(encoding="utf-8").splitlines() if not l.startswith("WINGMAN_TELEGRAM_TOKEN=")]
    lines.append("WINGMAN_TELEGRAM_TOKEN=" + token)
    WINGMAN_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["WINGMAN_TELEGRAM_TOKEN"] = token
    try:
        me = tg_call(token, "getMe", None, timeout=15)
        username = (me.get("result") or {}).get("username", "")
        if username:
            os.environ["WINGMAN_TELEGRAM_BOT"] = "@" + username
    except Exception:  # noqa: BLE001
        pass
    return True


def tg_help():
    return (
        "Wingman 🤖 — tumhara personal AI assistant (Telegram).\n\n"
        "Bas normal message bhejo — AI tools ke saath jawab dega.\n"
        "PDF/TXT file bhejo — document knowledge base me index ho jayegi.\n\n"
        "Commands:\n"
        "/whoami — chat ID\n"
        "/tasks — pending tasks\n"
        "/notes — saved notes\n"
        "/clear — chat clear\n"
        "/help — ye message"
    )


def tg_react(token, chat_id, message_id, emoji):
    try:
        tg_call(token, "setMessageReaction", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}],
        }, timeout=10)
    except Exception:  # noqa: BLE001
        pass


def typing_loop(token, chat_id, stop_event):
    while not stop_event.is_set():
        try:
            tg_call(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
        except Exception:  # noqa: BLE001
            pass
        stop_event.wait(4)


def tg_get_file(token, file_id):
    """Download a document sent to the bot (max 20 MB per Telegram Bot API)."""
    data = tg_call(token, "getFile", {"file_id": file_id}, timeout=30)
    file_path = (data.get("result") or {}).get("file_path")
    if not file_path:
        raise ValueError("Telegram file_path missing")
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    req = urllib.request.Request(url, headers={"User-Agent": "Wingman/1.2"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return resp.read()


def handle_tg(token, chat_id, text, sender=None, message_id=None, doc=None):
    key = str(chat_id)
    if message_id:
        tg_react(token, chat_id, message_id, "👀")

    with LOCK:
        state = load_state()
        tchats = state.setdefault("telegram_chats", {})
        hist = tchats.get(key, [])

        # auto-connect: pehla message/start bhejne wala hi owner ban jata hai
        owner = state["settings"].get("telegram_owner")
        if not owner:
            sender = sender or {}
            name = ((sender.get("first_name") or "") + " " + (sender.get("last_name") or "")).strip()
            state["settings"]["telegram_owner"] = {
                "id": chat_id,
                "name": name,
                "username": sender.get("username", ""),
            }
            save_state(state)
            if text in ("/start", "/help"):
                tg_send(token, chat_id, "✅ Connected! Main Wingman hoon — tumhara personal AI assistant.\n\nAb kuch bhi poocho. Commands: /tasks /notes /clear /help")
                return
            # pehla message hi sawaal ho to neeche process hoga
        elif str(owner.get("id")) != str(chat_id):
            save_state(state)
            tg_send(token, chat_id, "🔒 Yeh private bot hai — sirf owner use kar sakta hai.")
            return

        if text in ("/start", "/help"):
            save_state(state)
            tg_send(token, chat_id, tg_help())
            return

        if text == "/whoami":
            save_state(state)
            tg_send(token, chat_id, "Chat ID: " + str(chat_id))
            return

        if text == "/clear":
            tchats[key] = []
            save_state(state)
            tg_send(token, chat_id, "Chat clear ✓")
            return

        if text == "/tasks":
            tasks = [t for t in state["tasks"] if not t.get("done")]
            body = "\n".join("• " + t["title"] + (f" (due {t['due']})" if t.get("due") else "") for t in tasks)
            save_state(state)
            tg_send(token, chat_id, "Pending tasks:\n" + (body or "koi nahi"))
            return

        if text == "/notes":
            notes = state["notes"]
            body = "\n".join("• " + n["title"] for n in notes)
            save_state(state)
            tg_send(token, chat_id, "Notes:\n" + (body or "koi nahi"))
            return

        if doc:
            name = os.path.basename(str(doc.get("file_name") or ("telegram-" + str(doc.get("file_unique_id") or secrets.token_hex(4)))))
            size = int(doc.get("file_size") or 0)
            allowed = (".pdf", ".txt", ".md", ".csv", ".json", ".py", ".html", ".log", ".xml", ".yaml", ".yml")
            save_state(state)
            if not name.lower().endswith(allowed):
                tg_send(token, chat_id, "⚠ Sirf PDF / TXT / MD / CSV / JSON / code files supported hain.")
                return
            if size > 20_000_000:
                tg_send(token, chat_id, "⚠ File 20 MB se badi hai — Telegram limit.")
                return
            stop_typing = threading.Event()
            threading.Thread(target=typing_loop, args=(token, chat_id, stop_typing), daemon=True).start()
            try:
                raw = tg_get_file(token, doc.get("file_id"))
                doc_text = _extract_text(name, raw)
                DOCS_DIR.mkdir(parents=True, exist_ok=True)
                (DOCS_DIR / name).write_bytes(raw)
                info = index_document(name, doc_text)
                msg = (
                    f"📄 Indexed: {name}\n"
                    f"{info['chunks']} chunks · {info['chars']} chars\n\n"
                    f'Ab poocho: "{name} ka summary do"'
                )
            except Exception as exc:  # noqa: BLE001
                msg = "⚠ Index nahi hua: " + str(exc)[:200]
            finally:
                stop_typing.set()
            tg_send(token, chat_id, msg)
            if message_id:
                tg_react(token, chat_id, message_id, "✅")
            return

        now = time.time()
        hist.append({"role": "user", "content": text, "ts": now})
        convo = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in hist[-20:]:
            if m["role"] in ("user", "assistant"):
                convo.append({"role": m["role"], "content": m["content"]})

        stop_typing = threading.Event()
        threading.Thread(target=typing_loop, args=(token, chat_id, stop_typing), daemon=True).start()
        try:
            risky = bool(state["settings"].get("telegram_risky"))
            reply, actions = run_agent(state, convo, safe_only=not risky)
        finally:
            stop_typing.set()

        now2 = time.time()
        hist.append({"role": "assistant", "content": reply, "ts": now2})
        tchats[key] = hist[-40:]

        # mirror the Telegram conversation into the web chat list
        web_id = "tg-" + key
        web_chat = next((c for c in state["chats"] if c["id"] == web_id), None)
        if not web_chat:
            oname = (owner or {}).get("name") or (owner or {}).get("username") or str(chat_id)
            web_chat = {
                "id": web_id,
                "title": "Telegram · " + oname,
                "messages": [],
                "updated": now2,
                "source": "telegram",
            }
            state["chats"].insert(0, web_chat)
        web_chat["messages"].append({"role": "user", "content": text, "ts": now, "source": "telegram"})
        web_chat["messages"].append({"role": "assistant", "content": reply, "ts": now2, "source": "telegram"})
        web_chat["messages"] = web_chat["messages"][-100:]
        web_chat["updated"] = now2
        save_state(state)

    extra = "\n\n" + "\n".join("✓ " + a for a in actions) if actions else ""
    tg_send(token, chat_id, reply + extra)
    if message_id:
        tg_react(token, chat_id, message_id, "✅")


def telegram_worker():
    offset = 0
    try:
        with LOCK:
            offset = int(load_state().get("telegram_offset") or 0)
    except Exception:  # noqa: BLE001
        offset = 0
    while True:
        token = os.environ.get("WINGMAN_TELEGRAM_TOKEN", "")
        enabled = False
        if token:
            try:
                with LOCK:
                    enabled = bool(load_state()["settings"].get("telegram_enabled"))
            except Exception:  # noqa: BLE001
                enabled = False
        if not token or not enabled:
            time.sleep(3)
            continue
        try:
            data = tg_call(token, "getUpdates", {"offset": offset, "timeout": 25}, timeout=35)
            updates = data.get("result", []) or []
            for upd in updates:
                try:
                    offset = max(offset, int(upd.get("update_id", 0)) + 1)
                except Exception:  # noqa: BLE001
                    pass
                msg = upd.get("message") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                text = (msg.get("text") or "").strip()
                doc = msg.get("document")
                if not chat_id or (not text and not doc):
                    continue
                try:
                    handle_tg(token, chat_id, text, msg.get("from"), msg.get("message_id"), doc)
                except Exception:  # noqa: BLE001
                    try:
                        tg_send(token, chat_id, "⚠ Kuch error aaya, dobara try karo.")
                    except Exception:  # noqa: BLE001
                        pass
            if updates:
                with LOCK:
                    st = load_state()
                    st["telegram_offset"] = offset
                    save_state(st)
        except Exception:  # noqa: BLE001
            time.sleep(2)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):
        print("[wingman] " + (fmt % args))

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            # client disconnected mid-response — ignore (no stack trace)
            pass

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("payload too large")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _state_payload(self, state):
        settings = dict(state["settings"])
        oauth = oauth_connected()
        app_pw = bool(os.environ.get("WINGMAN_GMAIL_USER") and os.environ.get("WINGMAN_GMAIL_PASS"))
        settings["gmail_oauth"] = oauth
        settings["gmail_mode"] = "oauth" if oauth else ("apppassword" if app_pw else "none")
        settings["gmail_configured"] = bool(oauth or app_pw)
        settings["gmail_client_set"] = bool(oauth_client()[0])
        settings["gmail_client_id"] = oauth_client()[0]
        settings["gmail_user"] = oauth_load().get("email") or os.environ.get("WINGMAN_GMAIL_USER", settings.get("gmail_user", ""))
        settings["telegram_configured"] = bool(os.environ.get("WINGMAN_TELEGRAM_TOKEN"))
        settings["telegram_bot"] = os.environ.get("WINGMAN_TELEGRAM_BOT", "")
        settings["telegram_risky"] = bool(state["settings"].get("telegram_risky"))
        settings["ssh_host"] = state["settings"].get("ssh_host", "")
        settings["ssh_port"] = state["settings"].get("ssh_port", 22)
        settings["ssh_user"] = state["settings"].get("ssh_user", "root")
        settings["ssh_auth"] = state["settings"].get("ssh_auth", "password")
        settings["ssh_key_path"] = state["settings"].get("ssh_key_path", "")
        settings["ssh_password_set"] = bool(os.environ.get("WINGMAN_SSH_PASSWORD"))
        settings["ssh_configured"] = bool(state["settings"].get("ssh_host"))
        return {
            "chats": state["chats"],
            "active": state["active"],
            "tasks": state["tasks"],
            "notes": state["notes"],
            "settings": settings,
            "providers": {p: list(v["models"].keys()) for p, v in PROVIDERS.items()},
            "has_keys": {p: bool(DEFAULT_KEYS.get(p)) for p in PROVIDERS},
            "workspace": str(WORKSPACE),
        }

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            try:
                with LOCK:
                    st = load_state()
                tg = bool(os.environ.get("WINGMAN_TELEGRAM_TOKEN")) and bool(st["settings"].get("telegram_enabled"))
                ssh = bool(st["settings"].get("ssh_host"))
                chats = len(st.get("chats", []))
            except Exception:  # noqa: BLE001
                tg = ssh = False
                chats = 0
            return self._json(200, {
                "ok": True,
                "version": APP_VERSION,
                "uptime_sec": int(time.time() - START_TS),
                "providers": list(PROVIDERS),
                "telegram_enabled": tg,
                "ssh_configured": ssh,
                "chats": chats,
            })
        if path == "/api/state":
            with LOCK:
                state = load_state()
            return self._json(200, self._state_payload(state))
        if path == "/oauth/callback":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            err = (qs.get("error") or [""])[0]
            code = (qs.get("code") or [""])[0]
            if err:
                return self._oauth_result_page(False, "Google error: " + err)
            if not code:
                return self._oauth_result_page(False, "Authorization code missing")
            try:
                oauth_exchange(code)
                return self._oauth_result_page(True, "Gmail connected successfully.")
            except Exception as exc:  # noqa: BLE001
                return self._oauth_result_page(False, str(exc))
        if path == "/api/gmail/auth-url":
            url = oauth_auth_url()
            if not url:
                return self._json(400, {"error": "Pehle Client ID/Secret save karo"})
            return self._json(200, {"url": url})
        if path == "/api/docs":
            index = _docs_index_load()
            return self._json(200, {"files": index.get("files", [])})

        if path == "/api/runs":
            with LOCK:
                st = load_state()
            runs = []
            for run in st.get("runs", [])[:20]:
                steps = run.get("steps", [])
                runs.append({
                    "id": run["id"],
                    "goal": run["goal"],
                    "status": run["status"],
                    "updated": run.get("updated"),
                    "error": run.get("error", ""),
                    "done": sum(1 for s in steps if s["status"] == "done"),
                    "total": len(steps),
                    "steps": steps,
                    "result": (run.get("result") or "")[:4000],
                })
            return self._json(200, {"runs": runs})

        if path in ("/", "/index.html"):
            return self._serve_index()
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_json()
        except Exception as exc:  # noqa: BLE001
            return self._json(400, {"error": f"bad JSON: {exc}"})

        with LOCK:
            state = load_state()

            if path == "/api/settings":
                if body.get("provider") in PROVIDERS:
                    state["settings"]["provider"] = body["provider"]
                    models = list(PROVIDERS[body["provider"]]["models"].keys())
                    state["settings"]["model"] = body.get("model") if body.get("model") in models else models[0]
                if isinstance(body.get("flags"), dict):
                    flags = state["settings"].setdefault("flags", {})
                    for k in ("allow_risky", "allow_email", "allow_ssh"):
                        if isinstance(body["flags"].get(k), bool):
                            flags[k] = body["flags"][k]
                if isinstance(body.get("telegram_risky"), bool):
                    state["settings"]["telegram_risky"] = body["telegram_risky"]
                if body.get("gmail_user") is not None or body.get("gmail_pass"):
                    save_gmail(body.get("gmail_user"), body.get("gmail_pass"))
                    state["settings"]["gmail_user"] = os.environ.get("WINGMAN_GMAIL_USER", "")
                if body.get("telegram_token"):
                    save_telegram_token(body.get("telegram_token"))
                    state["settings"]["telegram_enabled"] = True  # auto-enable
                if isinstance(body.get("telegram_enabled"), bool):
                    state["settings"]["telegram_enabled"] = body["telegram_enabled"]
                save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/chat/new":
                chat = new_chat()
                state["chats"].insert(0, chat)
                state["active"] = chat["id"]
                save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/chat/select":
                cid = body.get("id")
                if any(c["id"] == cid for c in state["chats"]):
                    state["active"] = cid
                    save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/chat/delete":
                cid = body.get("id")
                state["chats"] = [c for c in state["chats"] if c["id"] != cid]
                if not state["chats"]:
                    chat = new_chat()
                    state["chats"].append(chat)
                    state["active"] = chat["id"]
                elif state["active"] == cid:
                    state["active"] = state["chats"][0]["id"]
                save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/chat/clear":
                cid = body.get("id")
                for c in state["chats"]:
                    if c["id"] == cid:
                        c["messages"] = []
                        c["updated"] = time.time()
                        break
                save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/clear-all":
                state["chats"] = [new_chat()]
                state["active"] = state["chats"][0]["id"]
                save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/reset":
                state["chats"] = [new_chat()]
                state["active"] = state["chats"][0]["id"]
                state["tasks"] = []
                state["notes"] = []
                state["telegram_chats"] = {}
                save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/chat":
                text = (body.get("text") or "").strip()
                if not text:
                    return self._json(400, {"error": "empty message"})
                chat = active_web_chat(state)
                chat["messages"].append({"role": "user", "content": text, "ts": time.time()})
                if chat["title"] in ("", "New chat"):
                    chat["title"] = text[:42]
                convo = [{"role": "system", "content": SYSTEM_PROMPT}]
                for m in chat["messages"]:
                    if m["role"] in ("user", "assistant"):
                        convo.append({"role": m["role"], "content": m["content"]})
                reply, actions = run_agent(state, convo)
                chat["messages"].append({"role": "assistant", "content": reply, "ts": time.time()})
                chat["updated"] = time.time()
                save_state(state)
                payload = self._state_payload(state)
                payload["actions"] = actions
                return self._json(200, payload)

            if path == "/api/tasks":
                action = body.get("action")
                if action == "add":
                    title = (body.get("title") or "").strip()
                    if title:
                        state["tasks"].append({"id": secrets.token_hex(6), "title": title, "due": (body.get("due") or "").strip(), "done": False, "ts": time.time()})
                elif action == "complete":
                    for t in state["tasks"]:
                        if t["id"] == body.get("id"):
                            t["done"] = not t.get("done")
                elif action == "delete":
                    state["tasks"] = [t for t in state["tasks"] if t["id"] != body.get("id")]
                save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/notes":
                action = body.get("action")
                if action == "add":
                    title = (body.get("title") or "").strip()
                    content = (body.get("content") or "").strip()
                    if title or content:
                        state["notes"].append({"id": secrets.token_hex(6), "title": title or "Note", "content": content, "ts": time.time()})
                elif action == "delete":
                    state["notes"] = [n for n in state["notes"] if n["id"] != body.get("id")]
                save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/gmail/config":
                d = oauth_load()
                d["client_id"] = (body.get("client_id") or "").strip()
                d["client_secret"] = (body.get("client_secret") or "").strip()
                oauth_save(d)
                return self._json(200, {"ok": True, "client_set": bool(d["client_id"])})

            if path == "/api/gmail/disconnect":
                d = oauth_load()
                for k in ("refresh_token", "access_token", "expires_at", "email"):
                    d.pop(k, None)
                oauth_save(d)
                return self._json(200, {"ok": True})

            if path == "/api/telegram/new-code":
                state["settings"]["telegram_verify_code"] = str(secrets.randbelow(900000) + 100000)
                save_state(state)
                return self._json(200, {"ok": True, "code": state["settings"]["telegram_verify_code"]})

            if path == "/api/ssh/config":
                s = state["settings"]
                if body.get("host") is not None:
                    s["ssh_host"] = str(body.get("host") or "").strip()
                if body.get("port"):
                    try:
                        s["ssh_port"] = int(body["port"])
                    except Exception:  # noqa: BLE001
                        pass
                if body.get("user") is not None:
                    s["ssh_user"] = str(body.get("user") or "").strip() or "root"
                if body.get("auth") in ("password", "key"):
                    s["ssh_auth"] = body["auth"]
                if body.get("key_path") is not None:
                    s["ssh_key_path"] = str(body.get("key_path") or "").strip()
                if body.get("password"):
                    save_ssh_password(body["password"])
                save_state(state)
                return self._json(200, self._state_payload(state))

            if path == "/api/ssh/test":
                try:
                    res = ssh_run(state, "uname -a && whoami && hostname", timeout=20)
                    return self._json(200, {"ok": True, "result": res})
                except Exception as exc:  # noqa: BLE001
                    return self._json(400, {"ok": False, "error": str(exc)})

            if path == "/api/runs":
                goal = (body.get("goal") or "").strip()
                if not goal:
                    return self._json(400, {"error": "goal required"})
                run = start_run(goal)
                return self._json(200, {"ok": True, "id": run["id"]})

            if path == "/api/runs/action":
                rid = body.get("id")
                action = body.get("action")
                ctl = RUN_CONTROLS.get(rid)
                if not ctl:
                    return self._json(404, {"error": "run not active"})
                if action == "pause":
                    ctl["pause"].set()
                elif action == "resume":
                    ctl["pause"].clear()
                elif action == "cancel":
                    ctl["cancel"].set()
                    ctl["pause"].clear()
                return self._json(200, {"ok": True})

            if path == "/api/docs/upload":
                name = os.path.basename(str(body.get("name") or "").strip())
                raw = body.get("data") or ""
                if not name or not raw:
                    return self._json(400, {"error": "name and data required"})
                try:
                    data = base64.b64decode(raw, validate=True)
                except Exception:  # noqa: BLE001
                    return self._json(400, {"error": "invalid base64 data"})
                if len(data) > 8_000_000:
                    return self._json(400, {"error": "file too large (max 8MB)"})
                try:
                    text = _extract_text(name, data)
                except Exception as exc:  # noqa: BLE001
                    return self._json(400, {"error": str(exc)})
                DOCS_DIR.mkdir(parents=True, exist_ok=True)
                (DOCS_DIR / name).write_bytes(data)
                info = index_document(name, text)
                return self._json(200, {"ok": True, "file": info})

            if path == "/api/docs/delete":
                name = os.path.basename(str(body.get("name") or "").strip())
                index = _docs_index_load()
                index["files"] = [f for f in index.get("files", []) if f.get("name") != name]
                index["chunks"] = [c for c in index.get("chunks", []) if c.get("file") != name]
                _docs_index_save(index)
                try:
                    (DOCS_DIR / name).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
                return self._json(200, {"ok": True})

        return self._json(404, {"error": "not found"})

    def _oauth_result_page(self, ok, msg):
        color = "#5eead4" if ok else "#fca5a5"
        title = "✓ Connected" if ok else "⚠ Failed"
        page = (
            '<!doctype html><html><head><meta charset="utf-8"><title>Gmail — Wingman</title>'
            "<style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
            "background:#242427;color:#ececf1;font-family:Segoe UI,sans-serif}"
            ".card{background:#2e2e33;padding:38px 34px;border-radius:24px;text-align:center;"
            "box-shadow:0 24px 60px rgba(0,0,0,.6);max-width:430px}"
            "h1{font-size:20px;color:" + color + ";margin:0 0 10px}"
            "p{color:#a0a0ab;line-height:1.6;margin:6px 0}</style></head>"
            '<body><div class="card"><h1>' + title + "</h1><p>" + html.escape(str(msg)) +
            "</p><p>Ye tab band karke Wingman panel me wapas jao.</p></div></body></html>"
        )
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_index(self):
        idx = STATIC / "index.html"
        if not idx.exists():
            return self._json(404, {"error": "index.html missing"})
        data = idx.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("WINGMAN_HOST", "127.0.0.1")
    port = int(os.environ.get("WINGMAN_PORT", "8200"))
    server = ThreadingHTTPServer((host, port), Handler)

    # purani Telegram history ko web chat list me copy karo (one-time)
    try:
        with LOCK:
            st = load_state()
            if backfill_telegram(st):
                save_state(st)
    except Exception:  # noqa: BLE001
        pass

    threading.Thread(target=telegram_worker, daemon=True).start()
    print("=" * 62)
    print("  WINGMAN — personal AI operator  v" + APP_VERSION)
    print(f"  http://{host}:{port}/")
    if host not in ("127.0.0.1", "localhost"):
        print("  WARNING: LAN exposed (no auth) — trusted networks only!")
    print(f"  workspace: {WORKSPACE}")
    print(f"  gmail configured: {'yes' if os.environ.get('WINGMAN_GMAIL_USER') else 'no'}")
    print("=" * 62)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
