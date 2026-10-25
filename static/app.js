'use strict';

const $ = (id) => document.getElementById(id);

let state = null;
let busy = false;
let lastActions = [];
let lastActiveId = null;
let lastMsgCount = 0;
let lastChatSig = '';

/* ---------------- helpers ---------------- */
function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function md(text) {
  let s = esc(text || '');
  s = s.replace(/```([\s\S]*?)```/g, (m, c) => '<pre><code>' + c.replace(/^\n/, '') + '</code></pre>');
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<i>$2</i>');
  s = s.replace(/^#{1,6}\s*(.*)$/gm, '<h4>$1</h4>');
  s = s.replace(/^\s*\*\s+(.*)$/gm, '• $1');
  s = s.replace(/^\s*[-•]\s+(.*)$/gm, '• $1');
  s = s.replace(/\*/g, ''); // leftover asterisks
  return s;
}

function timeStr(ts) {
  if (!ts) return '';
  try { return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
  catch (e) { return ''; }
}

let toastTimer = null;
function showToast(text) {
  const t = $('toast');
  t.textContent = text;
  t.classList.add('is-show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('is-show'), 3200);
}

async function api(path, body) {
  const r = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ---------------- render ---------------- */
function activeChat() {
  if (!state) return null;
  return state.chats.find((c) => c.id === state.active) || state.chats[0] || null;
}

function renderAll() {
  renderChats();
  renderMessages();
  renderTasks();
  renderNotes();
  renderSettings();
  const chat = activeChat();
  $('chatTitle').textContent = chat ? chat.title : 'New chat';
  const modelName = state && state.settings ? (state.settings.provider + ' · ' + state.settings.model) : '';
  $('modelBadge').textContent = modelName;

  const isTg = !!(chat && chat.source === 'telegram');
  $('input').disabled = isTg;
  $('sendBtn').disabled = isTg;
  $('input').placeholder = isTg
    ? 'Telegram chat — reply karne ke liye Telegram use karo'
    : 'Wingman se kuch bhi poocho…  (Shift+Enter = new line)';
  if (isTg) $('modelBadge').textContent = 'telegram sync';
  lastChatSig = chatSignature(state);
}

function renderChats() {
  const list = $('chatList');
  list.innerHTML = '';
  for (const chat of state.chats) {
    const row = document.createElement('div');
    row.className = 'chatItem' + (chat.id === state.active ? ' is-active' : '');
    const title = document.createElement('span');
    title.className = 'chatItem__title';
    title.textContent = chat.title || 'New chat';
    const del = document.createElement('button');
    del.className = 'chatItem__del';
    del.textContent = '✕';
    del.title = 'Delete chat';
    del.addEventListener('click', async (e) => {
      e.stopPropagation();
      state = await api('/api/chat/delete', { id: chat.id });
      renderAll();
    });
    row.addEventListener('click', async () => {
      state = await api('/api/chat/select', { id: chat.id });
      renderAll();
    });
    if (chat.source === 'telegram') {
      const badge = document.createElement('span');
      badge.className = 'tagTg';
      badge.textContent = 'TG';
      row.appendChild(badge);
    }
    row.appendChild(title);
    row.appendChild(del);
    list.appendChild(row);
  }
}

function messageEl(role, content, ts, actions) {
  const row = document.createElement('div');
  row.className = 'msg msg--' + (role === 'user' ? 'user' : 'ai');

  const ava = document.createElement('div');
  ava.className = 'msg__ava';
  ava.textContent = role === 'user' ? 'Y' : 'W';

  const body = document.createElement('div');
  body.className = 'msg__body';

  const meta = document.createElement('div');
  meta.className = 'msg__meta';
  const nm = document.createElement('span');
  nm.className = 'msg__name';
  nm.textContent = role === 'user' ? 'You' : 'Wingman';
  const tm = document.createElement('span');
  tm.textContent = timeStr(ts);
  meta.appendChild(nm);
  meta.appendChild(tm);

  const contentEl = document.createElement('div');
  contentEl.className = 'msg__content';
  contentEl.innerHTML = md(content);

  body.appendChild(meta);
  body.appendChild(contentEl);

  if (actions && actions.length) {
    for (const a of actions) {
      const chip = document.createElement('div');
      chip.className = 'actionChip';
      chip.textContent = '✓ ' + a;
      body.appendChild(chip);
    }
  }

  row.appendChild(ava);
  row.appendChild(body);
  return row;
}

function renderMessages() {
  const box = $('messages');
  const chat = activeChat();
  box.innerHTML = '';

  if (!chat || chat.messages.length === 0) {
    box.appendChild(emptyStateEl());
    lastActiveId = chat ? chat.id : null;
    lastMsgCount = 0;
    return;
  }

  chat.messages.forEach((m, idx) => {
    const isLast = idx === chat.messages.length - 1;
    const actions = isLast && m.role === 'assistant' ? lastActions : null;
    box.appendChild(messageEl(m.role, m.content, m.ts, actions));
  });
  scrollBottom();
  lastActiveId = chat.id;
  lastMsgCount = chat.messages.length;
}

function chatSignature(s) {
  if (!s) return '';
  return (s.chats || []).map((c) => c.id + ':' + c.messages.length + ':' + c.title).join('|') + '#' + s.active;
}

function emptyStateEl() {
  const div = document.createElement('div');
  div.className = 'empty';
  div.innerHTML =
    '<div class="empty__logo">W</div>' +
    '<h2>Hi, main Wingman hoon 👋</h2>' +
    '<p>Tasks, reminders, notes — sab kuch chat se. Hinglish me baat karo.</p>' +
    '<div class="suggestions">' +
    '<button class="sugg" data-text="Kal shaam 5 baje meeting ka reminder lagao">Kal 5 baje meeting ka reminder</button>' +
    '<button class="sugg" data-text="Mera ek note save karo: project ideas — AI chatroom, wingman app">Ek note save karo</button>' +
    '<button class="sugg" data-text="Mere pending tasks batao">Mere pending tasks batao</button>' +
    '<button class="sugg" data-text="Aaj ka date aur time batao">Aaj ka date/time batao</button>' +
    '</div>';
  div.querySelectorAll('.sugg').forEach((b) => {
    b.addEventListener('click', () => send(b.dataset.text));
  });
  return div;
}

function renderTasks() {
  const list = $('taskList');
  list.innerHTML = '';
  if (!state.tasks.length) {
    list.innerHTML = '<div class="list__empty">Koi task nahi.<br>Chat me bolo: "kal 5 baje meeting ka reminder lagao"</div>';
    return;
  }
  const sorted = [...state.tasks].sort((a, b) => (a.done === b.done ? b.ts - a.ts : a.done ? 1 : -1));
  for (const t of sorted) {
    const el = document.createElement('div');
    el.className = 'item' + (t.done ? ' is-done' : '');
    const row = document.createElement('div');
    row.className = 'item__row';

    const check = document.createElement('button');
    check.className = 'check';
    check.textContent = '✓';
    check.title = t.done ? 'Mark pending' : 'Mark done';
    check.addEventListener('click', async () => {
      state = await api('/api/tasks', { action: 'complete', id: t.id });
      renderAll();
    });

    const title = document.createElement('div');
    title.className = 'item__title';
    title.textContent = t.title;

    row.appendChild(check);
    row.appendChild(title);
    el.appendChild(row);

    if (t.due) {
      const due = document.createElement('div');
      due.className = 'item__due';
      due.textContent = '⏰ ' + t.due;
      el.appendChild(due);
    }

    const del = document.createElement('button');
    del.className = 'item__del';
    del.textContent = '✕';
    del.addEventListener('click', async () => {
      state = await api('/api/tasks', { action: 'delete', id: t.id });
      renderAll();
    });
    el.appendChild(del);

    list.appendChild(el);
  }
}

function renderNotes() {
  const list = $('noteList');
  list.innerHTML = '';
  if (!state.notes.length) {
    list.innerHTML = '<div class="list__empty">Koi note nahi.<br>Chat me bolo: "ek note save karo…"</div>';
    return;
  }
  const sorted = [...state.notes].sort((a, b) => b.ts - a.ts);
  for (const n of sorted) {
    const el = document.createElement('div');
    el.className = 'item';
    const title = document.createElement('div');
    title.className = 'item__title';
    title.textContent = n.title;
    el.appendChild(title);
    if (n.content) {
      const body = document.createElement('div');
      body.className = 'item__body';
      body.textContent = n.content;
      el.appendChild(body);
    }
    const del = document.createElement('button');
    del.className = 'item__del';
    del.textContent = '✕';
    del.addEventListener('click', async () => {
      state = await api('/api/notes', { action: 'delete', id: n.id });
      renderAll();
    });
    el.appendChild(del);
    list.appendChild(el);
  }
}

function renderSettings() {
  const p = $('providerSel');
  const m = $('modelSel');
  const providers = state.providers || {};
  const provider = state.settings.provider;
  const model = state.settings.model;

  if (p.options.length !== Object.keys(providers).length) {
    p.innerHTML = '';
    for (const name of Object.keys(providers)) {
      const o = document.createElement('option');
      o.value = name;
      o.textContent = name;
      p.appendChild(o);
    }
  }
  p.value = provider;

  m.innerHTML = '';
  for (const key of providers[provider] || []) {
    const o = document.createElement('option');
    o.value = key;
    o.textContent = key;
    m.appendChild(o);
  }
  m.value = model;

  const has = state.has_keys && state.has_keys[provider];
  $('keyHint').textContent = has ? '✓ key configured' : '⚠ key missing for ' + provider;
  $('keyHint').style.color = has ? '#5eead4' : '#fca5a5';

  const s = state.settings || {};
  const f = s.flags || {};
  $('allowRisky').checked = !!f.allow_risky;
  $('allowEmail').checked = !!f.allow_email;
  const gmode = s.gmail_mode || 'none';
  $('gmailStatus').textContent =
    gmode === 'oauth'
      ? '✓ Connected via Google OAuth · ' + (s.gmail_user || '')
      : gmode === 'apppassword'
        ? '✓ Connected via App Password · ' + (s.gmail_user || '')
        : '⚠ Gmail not connected — email tool off';
  $('gmailStatus').className = 'gmailStatus ' + (gmode === 'none' ? 'is-off' : 'is-on');
  if (!$('gmailUser').value) $('gmailUser').value = s.gmail_user || '';
  if (!$('gmailClientId').value && s.gmail_client_id) $('gmailClientId').value = s.gmail_client_id;
  $('workspacePath').textContent = state.workspace || '';

  $('tgEnabled').checked = !!s.telegram_enabled;
  $('tgStatus').textContent = s.telegram_configured
    ? '✓ Bot token saved ' + (s.telegram_bot || '')
    : '⚠ Bot token not set';
  $('tgStatus').style.color = s.telegram_configured ? '#5eead4' : '#fca5a5';

  const own = s.telegram_owner;
  if (own && own.id) {
    const who = own.username ? '@' + own.username : (own.name || 'owner');
    $('tgOwnerStatus').textContent = '✓ Connected · ' + who + ' (ID ' + own.id + ')';
    $('tgOwnerStatus').className = 'gmailStatus is-on';
  } else {
    $('tgOwnerStatus').textContent = s.telegram_configured
      ? '⚠ Not connected — Telegram pe bot ko /start bhejo'
      : '⚠ Pehle bot token save karo';
    $('tgOwnerStatus').className = 'gmailStatus is-off';
  }

  if (!$('sshHost').value) $('sshHost').value = s.ssh_host || '';
  if (!$('sshPort').value) $('sshPort').value = s.ssh_port || 22;
  if (!$('sshUser').value) $('sshUser').value = s.ssh_user || 'root';
  if (!$('sshKeyPath').value) $('sshKeyPath').value = s.ssh_key_path || '';
  if (!document.activeElement || document.activeElement.id !== 'sshAuth') {
    $('sshAuth').value = s.ssh_auth || 'password';
  }
  $('allowSsh').checked = !!(s.flags && s.flags.allow_ssh);
  $('telegramRisky').checked = !!s.telegram_risky;
  $('sshStatus').textContent = s.ssh_configured
    ? '✓ Configured · ' + (s.ssh_user || 'root') + '@' + s.ssh_host + ':' + (s.ssh_port || 22)
    : '⚠ Not configured';
  $('sshStatus').className = 'gmailStatus ' + (s.ssh_configured ? 'is-on' : 'is-off');
  toggleSshAuth();
}

function toggleSshAuth() {
  const isKey = $('sshAuth').value === 'key';
  $('sshKeyWrap').hidden = !isKey;
  $('sshPassWrap').hidden = isKey;
}

function scrollBottom() {
  const box = $('messages');
  box.scrollTop = box.scrollHeight;
}

/* ---------------- chat ---------------- */
function resizeInput() {
  const el = $('input');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

function showTyping() {
  const box = $('messages');
  const row = document.createElement('div');
  row.className = 'msg msg--ai';
  row.id = 'typingRow';
  row.innerHTML = '<div class="msg__ava">W</div><div class="msg__body"><div class="msg__meta"><span class="msg__name">Wingman</span></div><div class="typing"><i></i><i></i><i></i></div></div>';
  box.appendChild(row);
  scrollBottom();
}

function removeTyping() {
  const t = $('typingRow');
  if (t) t.remove();
}

async function send(text) {
  text = (text || '').trim();
  if (!text || busy) return;
  const current = activeChat();
  if (current && current.source === 'telegram') {
    showToast('Ye Telegram chat hai — reply Telegram se karo');
    return;
  }
  busy = true;
  $('sendBtn').disabled = true;
  lastActions = [];

  const box = $('messages');
  if ($('emptyState')) box.innerHTML = '';
  box.appendChild(messageEl('user', text, Date.now() / 1000, null));
  scrollBottom();
  showTyping();

  try {
    state = await api('/api/chat', { text });
    lastActions = state.actions || [];
    renderAll();
    if (lastActions.length) showToast(lastActions.join(' · '));
  } catch (e) {
    removeTyping();
    showToast('Error: ' + e.message);
  } finally {
    busy = false;
    const c = activeChat();
    $('sendBtn').disabled = !!(c && c.source === 'telegram');
    $('input').focus();
  }
}

/* ---------------- events ---------------- */
$('sendBtn').addEventListener('click', () => {
  const v = $('input').value;
  $('input').value = '';
  resizeInput();
  send(v);
});
$('input').addEventListener('input', resizeInput);
$('input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const v = $('input').value;
    $('input').value = '';
    resizeInput();
    send(v);
  }
});

$('newChatBtn').addEventListener('click', async () => {
  state = await api('/api/chat/new', {});
  lastActions = [];
  renderAll();
  $('input').focus();
  closeOverlays();
});

$('clearBtn').addEventListener('click', async () => {
  const chat = activeChat();
  if (!chat) return;
  if (!confirm('Ye chat delete karein?')) return;
  state = await api('/api/chat/delete', { id: chat.id });
  lastActions = [];
  renderAll();
});

$('clearChatBtn').addEventListener('click', async () => {
  const chat = activeChat();
  if (!chat) return;
  if (!chat.messages.length) { showToast('Chat already empty'); return; }
  if (!confirm('Is chat ke messages clear karein?')) return;
  try {
    state = await api('/api/chat/clear', { id: chat.id });
    lastActions = [];
    renderAll();
    showToast('Chat cleared 🧹');
  } catch (e) { showToast('Error: ' + e.message); }
});

$('clearAllChats').addEventListener('click', async () => {
  if (!confirm('Saare chats clear karein? (tasks/notes safe rahenge)')) return;
  try {
    state = await api('/api/clear-all', {});
    lastActions = [];
    renderAll();
    showToast('All chats cleared 🧹');
  } catch (e) { showToast('Error: ' + e.message); }
});

$('resetAll').addEventListener('click', async () => {
  if (!confirm('Sab kuch reset karein? Chats + tasks + notes delete ho jayenge.')) return;
  try {
    state = await api('/api/reset', {});
    lastActions = [];
    renderAll();
    showToast('Everything reset');
  } catch (e) { showToast('Error: ' + e.message); }
});

$('providerSel').addEventListener('change', async () => {
  state = await api('/api/settings', { provider: $('providerSel').value });
  renderAll();
});
$('modelSel').addEventListener('change', async () => {
  state = await api('/api/settings', { provider: $('providerSel').value, model: $('modelSel').value });
  renderAll();
});

$('taskForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const title = $('taskInput').value.trim();
  if (!title) return;
  state = await api('/api/tasks', { action: 'add', title });
  $('taskInput').value = '';
  renderAll();
});

$('noteForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const title = $('noteTitle').value.trim();
  const content = $('noteContent').value.trim();
  if (!title && !content) return;
  state = await api('/api/notes', { action: 'add', title, content });
  $('noteTitle').value = '';
  $('noteContent').value = '';
  renderAll();
});

/* settings modal */
$('settingsBtn').addEventListener('click', () => {
  $('settingsModal').hidden = false;
  renderSettings();
});
$('modalClose').addEventListener('click', () => { $('settingsModal').hidden = true; });
$('settingsModal').addEventListener('click', (e) => {
  if (e.target === $('settingsModal')) $('settingsModal').hidden = true;
});
$('saveSettings').addEventListener('click', async () => {
  const payload = {
    flags: {
      allow_risky: $('allowRisky').checked,
      allow_email: $('allowEmail').checked,
    },
    gmail_user: $('gmailUser').value.trim(),
  };
  const pass = $('gmailPass').value.trim();
  if (pass) payload.gmail_pass = pass;
  const tgToken = $('tgToken').value.trim();
  if (tgToken) payload.telegram_token = tgToken;
  payload.telegram_enabled = $('tgEnabled').checked;
  try {
    state = await api('/api/settings', payload);
    $('gmailPass').value = '';
    $('tgToken').value = '';
    $('settingsModal').hidden = true;
    renderAll();
    showToast('Settings saved ✓');
  } catch (e) {
    showToast('Save failed: ' + e.message);
  }
});

/* ssh */
$('sshAuth').addEventListener('change', toggleSshAuth);

$('saveSsh').addEventListener('click', async () => {
  const payload = {
    host: $('sshHost').value.trim(),
    port: $('sshPort').value.trim(),
    user: $('sshUser').value.trim(),
    auth: $('sshAuth').value,
    key_path: $('sshKeyPath').value.trim(),
  };
  const pw = $('sshPass').value.trim();
  if (pw) payload.password = pw;
  try {
    state = await api('/api/ssh/config', payload);
    state = await api('/api/settings', {
      flags: {
        allow_risky: $('allowRisky').checked,
        allow_email: $('allowEmail').checked,
        allow_ssh: $('allowSsh').checked,
      },
      telegram_risky: $('telegramRisky').checked,
    });
    $('sshPass').value = '';
    $('sshStatus').textContent = 'Testing connection…';
    $('sshStatus').className = 'gmailStatus';
    const t = await fetch('/api/ssh/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const tj = await t.json();
    if (tj.ok) {
      showToast('SSH connected ✓');
      $('sshStatus').textContent = '✓ Connected · ' + (payload.user || 'root') + '@' + payload.host + ':' + (payload.port || 22);
      $('sshStatus').className = 'gmailStatus is-on';
    } else {
      showToast('SSH test failed: ' + (tj.error || 'unknown'));
      $('sshStatus').textContent = '⚠ Test failed: ' + (tj.error || '');
      $('sshStatus').className = 'gmailStatus is-off';
    }
    state = await api('/api/state');
    renderAll();
  } catch (e) {
    showToast('Save failed: ' + e.message);
  }
});

/* gmail oauth */
$('connectGmail').addEventListener('click', async () => {
  const cid = $('gmailClientId').value.trim();
  const csec = $('gmailClientSecret').value.trim();
  if (!cid || !csec) { showToast('Client ID aur Client Secret daalo'); return; }
  try {
    await api('/api/gmail/config', { client_id: cid, client_secret: csec });
    const r = await api('/api/gmail/auth-url');
    window.open(r.url, '_blank');
    showToast('Google sign-in complete karo, phir panel auto-refresh hoga');
  } catch (e) {
    showToast('Auth error: ' + e.message);
  }
});

$('disconnectGmail').addEventListener('click', async () => {
  try {
    await api('/api/gmail/disconnect', {});
    state = await api('/api/state');
    renderAll();
    showToast('Gmail disconnected');
  } catch (e) {
    showToast('Error: ' + e.message);
  }
});

window.addEventListener('focus', async () => {
  try {
    state = await api('/api/state');
    renderAll();
  } catch (e) { /* ignore */ }
});

/* live Telegram mirror refresh — sirf changed hone pe, no blink */
setInterval(async () => {
  if (busy || document.hidden) return;
  try {
    const s2 = await api('/api/state');
    const sig = chatSignature(s2);
    if (sig === lastChatSig) return;

    const active = (s2.chats || []).find((c) => c.id === s2.active) || null;
    const count = active ? active.messages.length : 0;

    state = s2;
    lastChatSig = sig;
    renderChats();

    if (!active) { renderMessages(); return; }

    if (active.id === lastActiveId) {
      if (count > lastMsgCount && active.source === 'telegram') {
        // append only the new messages (no full re-render)
        const box = $('messages');
        if ($('chatEmpty')) box.innerHTML = '';
        for (let i = lastMsgCount; i < count; i += 1) {
          const m = active.messages[i];
          box.appendChild(messageEl(m.role, m.content, m.ts, null));
        }
        scrollBottom();
        lastMsgCount = count;
        $('chatTitle').textContent = active.title;
        $('modelBadge').textContent = 'telegram sync';
      } else if (count !== lastMsgCount) {
        renderMessages();
      }
    } else {
      renderMessages();
    }
  } catch (e) { /* ignore */ }
}, 5000);

/* live refresh while settings open (Telegram verification) */
setInterval(async () => {
  if ($('settingsModal').hidden || document.hidden) return;
  try {
    const s2 = await api('/api/state');
    state = s2;
    const s = s2.settings || {};
    const own = s.telegram_owner;
    if (own && own.id) {
      const who = own.username ? '@' + own.username : (own.name || 'owner');
      $('tgOwnerStatus').textContent = '✓ Connected · ' + who + ' (ID ' + own.id + ')';
      $('tgOwnerStatus').className = 'gmailStatus is-on';
    }
    $('sshStatus').textContent = s.ssh_configured
      ? '✓ Configured · ' + (s.ssh_user || 'root') + '@' + s.ssh_host + ':' + (s.ssh_port || 22)
      : '⚠ Not configured';
    $('sshStatus').className = 'gmailStatus ' + (s.ssh_configured ? 'is-on' : 'is-off');
  } catch (e) { /* ignore */ }
}, 6000);

/* tabs */
function showPane(name) {
  $('tabTasks').classList.toggle('is-active', name === 'tasks');
  $('tabNotes').classList.toggle('is-active', name === 'notes');
  $('tabRuns').classList.toggle('is-active', name === 'runs');
  $('tabFiles').classList.toggle('is-active', name === 'files');
  $('tasksPane').hidden = name !== 'tasks';
  $('notesPane').hidden = name !== 'notes';
  $('runsPane').hidden = name !== 'runs';
  $('filesPane').hidden = name !== 'files';
  if (name === 'runs') loadRuns();
  if (name === 'files') loadDocs();
}
$('tabTasks').addEventListener('click', () => showPane('tasks'));
$('tabNotes').addEventListener('click', () => showPane('notes'));
$('tabRuns').addEventListener('click', () => showPane('runs'));
$('tabFiles').addEventListener('click', () => showPane('files'));

/* ---------- autonomous runs ---------- */
function runStatusClass(status) {
  const allowed = ['queued', 'running', 'paused', 'done', 'failed', 'cancelled'];
  return 'run__status run__status--' + (allowed.includes(status) ? status : 'queued');
}

function runControlBtn(label, id, action) {
  const b = document.createElement('button');
  b.textContent = label;
  b.addEventListener('click', async () => {
    try {
      await fetch('/api/runs/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, action }),
      });
      loadRuns();
    } catch (e) { showToast('Error: ' + e.message); }
  });
  return b;
}

function renderRuns(runs) {
  const list = $('runList');
  list.innerHTML = '';
  if (!runs.length) {
    list.innerHTML = '<div class="list__empty">Koi run nahi.<br>Goal likho aur <b>Run</b> dabao — AI plan banake execute karega.</div>';
    return;
  }
  for (const run of runs) {
    const box = document.createElement('div');
    box.className = 'run';

    const head = document.createElement('div');
    head.className = 'run__head';
    const goal = document.createElement('div');
    goal.className = 'run__goal';
    goal.textContent = run.goal;
    const badge = document.createElement('span');
    badge.className = runStatusClass(run.status);
    badge.textContent = run.status;
    head.appendChild(goal);
    head.appendChild(badge);
    box.appendChild(head);

    const meta = document.createElement('div');
    meta.className = 'run__meta';
    meta.textContent = (run.done || 0) + '/' + (run.total || 0) + ' steps' + (run.error ? ' · ' + run.error : '');
    box.appendChild(meta);

    const controls = document.createElement('div');
    controls.className = 'run__controls';
    if (run.status === 'running') controls.appendChild(runControlBtn('Pause', run.id, 'pause'));
    if (run.status === 'paused') controls.appendChild(runControlBtn('Resume', run.id, 'resume'));
    if (['queued', 'running', 'paused'].includes(run.status)) controls.appendChild(runControlBtn('Cancel', run.id, 'cancel'));
    if (controls.children.length) box.appendChild(controls);

    if (run.steps && run.steps.length) {
      const steps = document.createElement('div');
      steps.className = 'run__steps';
      for (const step of run.steps) {
        const row = document.createElement('div');
        row.className = 'step step--' + step.status;
        const title = document.createElement('div');
        title.className = 'step__title';
        const dot = document.createElement('span');
        dot.className = 'step__dot';
        const text = document.createElement('span');
        text.textContent = step.title;
        const agent = document.createElement('span');
        agent.className = 'step__agent';
        agent.textContent = step.agent;
        title.append(dot, text, agent);
        row.appendChild(title);
        if (step.result) {
          const res = document.createElement('div');
          res.className = 'step__result';
          res.textContent = step.result.slice(0, 400);
          row.appendChild(res);
        }
        steps.appendChild(row);
      }
      box.appendChild(steps);
    }

    if (run.result) {
      const res = document.createElement('div');
      res.className = 'step__result';
      res.style.maxHeight = '120px';
      res.textContent = run.result.slice(0, 700);
      box.appendChild(res);
    }
    list.appendChild(box);
  }
}

async function loadRuns() {
  try {
    const r = await fetch('/api/runs');
    const data = await r.json();
    renderRuns(data.runs || []);
  } catch (e) { /* ignore */ }
}

$('runForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const goal = $('runGoal').value.trim();
  if (!goal) return;
  $('runGoal').value = '';
  try {
    const r = await fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal }),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    showToast('Run started 🚀');
    loadRuns();
  } catch (e) { showToast('Run failed: ' + e.message); }
});

setInterval(() => {
  if ($('runsPane').hidden || document.hidden) return;
  loadRuns();
}, 4000);

/* ---------- document knowledge base ---------- */
async function loadDocs() {
  try {
    const r = await fetch('/api/docs');
    const data = await r.json();
    renderDocs(data.files || []);
  } catch (e) { /* ignore */ }
}

function renderDocs(files) {
  const list = $('docList');
  list.innerHTML = '';
  if (!files.length) {
    list.innerHTML = '<div class="list__empty">Koi document nahi.<br>PDF/TXT upload karo, phir AI se poocho: <b>"is document ka summary do"</b>.</div>';
    return;
  }
  for (const f of files) {
    const el = document.createElement('div');
    el.className = 'item';
    const title = document.createElement('div');
    title.className = 'item__title';
    title.textContent = f.name;
    const meta = document.createElement('div');
    meta.className = 'item__body';
    const chars = f.chars || 0;
    meta.textContent = f.chunks + ' chunks · ' + (chars >= 1024 ? Math.round(chars / 1024) + ' KB text' : chars + ' chars');
    const del = document.createElement('button');
    del.className = 'item__del';
    del.textContent = '✕';
    del.addEventListener('click', async () => {
      await fetch('/api/docs/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: f.name }),
      });
      loadDocs();
    });
    el.appendChild(title);
    el.appendChild(meta);
    el.appendChild(del);
    list.appendChild(el);
  }
}

$('docUploadBtn').addEventListener('click', async () => {
  const input = $('docFile');
  const file = input.files && input.files[0];
  if (!file) { showToast('Pehle file select karo'); return; }
  if (file.size > 8 * 1024 * 1024) { showToast('File 8MB se badi hai'); return; }
  try {
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    const b64 = btoa(binary);
    const r = await fetch('/api/docs/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: file.name, data: b64 }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || 'upload failed');
    input.value = '';
    showToast('Uploaded: ' + file.name + ' · ' + j.file.chunks + ' chunks');
    loadDocs();
  } catch (e) {
    showToast('Upload failed: ' + e.message);
  }
});

/* overlays (mobile) */
function closeOverlays() {
  $('sidebar').classList.remove('is-open');
  $('panel').classList.remove('is-open');
  $('backdrop').classList.remove('is-show');
}
$('menuBtn').addEventListener('click', () => {
  $('sidebar').classList.toggle('is-open');
  $('panel').classList.remove('is-open');
  $('backdrop').classList.toggle('is-show', $('sidebar').classList.contains('is-open'));
});
$('panelBtn').addEventListener('click', () => {
  $('panel').classList.toggle('is-open');
  $('sidebar').classList.remove('is-open');
  $('backdrop').classList.toggle('is-show', $('panel').classList.contains('is-open'));
});
$('backdrop').addEventListener('click', closeOverlays);

/* voice */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
let recording = false;
if (SR) {
  rec = new SR();
  rec.lang = 'hi-IN';
  rec.interimResults = false;
  rec.continuous = false;
  rec.onresult = (e) => {
    const t = e.results[0][0].transcript;
    $('input').value = ($('input').value ? $('input').value + ' ' : '') + t;
    resizeInput();
    $('input').focus();
  };
  rec.onend = () => { recording = false; $('micBtn').classList.remove('is-rec'); };
  rec.onerror = () => { recording = false; $('micBtn').classList.remove('is-rec'); };
}
$('micBtn').addEventListener('click', () => {
  if (!rec) { showToast('Voice is browser me supported nahi hai'); return; }
  if (recording) { rec.stop(); return; }
  recording = true;
  $('micBtn').classList.add('is-rec');
  try { rec.start(); } catch (e) { recording = false; $('micBtn').classList.remove('is-rec'); }
});

/* ---------------- init ---------------- */
(async function init() {
  try {
    state = await api('/api/state');
    renderAll();
    resizeInput();
    $('input').focus();
    if (new URLSearchParams(location.search).get('open') === 'settings') {
      $('settingsModal').hidden = false;
      renderSettings();
    }
  } catch (e) {
    showToast('Server se connect nahi hua: ' + e.message);
  }
})();
