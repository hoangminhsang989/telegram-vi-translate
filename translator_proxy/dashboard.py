from __future__ import annotations

DASHBOARD_HTML = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telegram PC — Dịch Tiếng Việt</title>
<style>
  :root {
    --bg: #0f1419;
    --card: #171f28;
    --border: #283442;
    --text: #e7eef6;
    --muted: #8ca0b5;
    --accent: #38bdf8;
    --ok: #22c55e;
    --warn: #f59e0b;
    --err: #ef4444;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    background: var(--bg); color: var(--text);
  }
  .wrap { max-width: 1020px; margin: 0 auto; display: grid; gap: 16px; }
  .head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
  h1 { font-size: 20px; margin: 0; }
  .badge {
    padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
    background: #1e293b; border: 1px solid var(--border);
  }
  .badge.ok { color: var(--ok); border-color: rgba(34,197,94,.35); }
  .badge.warn { color: var(--warn); border-color: rgba(245,158,11,.35); }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
  .card {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px; display: flex; flex-direction: column; gap: 10px;
  }
  .card h2 { font-size: 15px; margin: 0 0 4px 0; color: var(--accent); }
  label { font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
  input, select, textarea {
    background: #0d1218; color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 10px; font-size: 13px; font-family: inherit;
  }
  textarea { min-height: 82px; resize: vertical; font-family: Consolas, monospace; font-size: 12px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .check { flex-direction: row; align-items: center; gap: 8px; color: var(--text); font-size: 13px; }
  .check input { width: auto; }
  button {
    background: var(--accent); color: #081018; border: 0; border-radius: 6px;
    padding: 8px 14px; font-weight: 600; cursor: pointer; font-size: 13px;
  }
  button.secondary { background: #263241; color: var(--text); }
  button.danger { background: rgba(239,68,68,.18); color: #fca5a5; border: 1px solid rgba(239,68,68,.35); }
  .muted { color: var(--muted); font-size: 12px; }
  .msg { font-size: 12px; min-height: 16px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
  th { color: var(--muted); font-weight: 600; }
  .mono { font-family: Consolas, monospace; }
  .outbox {
    background: #0d1218; border: 1px solid var(--border); border-radius: 6px;
    padding: 10px; min-height: 54px; white-space: pre-wrap; font-size: 13px;
  }
  .qrbox {
    background: #ffffff; padding: 10px; border-radius: 8px; display: inline-block; width: 196px; height: 196px;
  }
  .qrbox img { width: 100%; height: 100%; display: block; }
  .chatlist {
    max-height: 220px; overflow-y: auto; border: 1px solid var(--border); border-radius: 6px;
    padding: 8px; background: #0d1218; display: grid; gap: 6px;
  }
  .hidden { display: none !important; }
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1>Telegram PC — Auto Translate Tiếng Việt</h1>
    <div class="row">
      <span id="proxyBadge" class="badge ok">Proxy: ...</span>
      <span id="tgBadge" class="badge">Telegram: ...</span>
      <span id="mtBadge" class="badge">Pre-Translate: ...</span>
      <span id="cacheBadge" class="badge">Cache: ...</span>
    </div>
  </div>

  <div class="card">
    <div class="head">
      <h2 style="margin:0">Dịch trước tự động (MTProto Pre-Translate)</h2>
      <span id="mtStatusText" class="muted"></span>
    </div>
    <p class="muted" style="margin:0">
      Lấy sẵn tin nhắn gần đây và tự dịch ngay tin nhắn mới đến theo lô 20 tin/lần để khi cuộn trong Telegram hiện tiếng Việt tức thì.
      Lấy <code>api_id</code> &amp; <code>api_hash</code> miễn phí tại <strong>my.telegram.org</strong> → <em>API development tools</em>.
    </p>
    <div class="row">
      <label style="flex:1; min-width:140px">API ID
        <input id="mt_api_id" type="number" placeholder="12345678">
      </label>
      <label style="flex:2; min-width:220px">API Hash <span id="mtHashHint" class="muted"></span>
        <input id="mt_api_hash" type="password" placeholder="Để trống nếu giữ hash hiện tại">
      </label>
      <label style="flex:1; min-width:120px">Số tin lấy trước/nhóm
        <input id="mt_prefetch_limit" type="number" min="10" max="2000" value="300">
      </label>
      <label style="flex:1; min-width:100px">Số tin/lô AI
        <input id="mt_batch_size" type="number" min="1" max="40" value="20">
      </label>
    </div>
    <div class="row">
      <label class="check"><input id="mt_enabled" type="checkbox"> Bật tự dịch trước nền</label>
      <label class="check"><input id="mt_listen_new" type="checkbox"> Tự dịch tin nhắn mới đến theo thời gian thực</label>
    </div>

    <div id="mtAuthBox" class="row" style="align-items:flex-start">
      <div style="display:grid; gap:8px">
        <div class="row">
          <button onclick="startQrLogin()">Tạo mã QR đăng nhập</button>
          <span class="muted">hoặc SĐT:</span>
          <input id="mt_phone" placeholder="+84901234567" style="width:150px">
          <button class="secondary" onclick="sendPhoneCode()">Gửi OTP</button>
        </div>
        <div class="row">
          <input id="mt_code" placeholder="Mã OTP (nếu dùng SĐT)" style="width:160px">
          <input id="mt_password" type="password" placeholder="Mật khẩu 2FA (nếu có)" style="width:180px">
          <button class="secondary" onclick="completeSignIn()">Xác nhận đăng nhập</button>
        </div>
      </div>
      <div id="qrWrap" class="qrbox hidden"><img id="qrImg" alt="QR Telegram"></div>
    </div>

    <div id="mtChatsBox" class="hidden" style="display:grid; gap:8px">
      <div class="row">
        <button class="secondary" onclick="loadDialogs()">Tải danh sách nhóm từ Telegram</button>
        <button onclick="saveMtprotoAndSync()">Lưu &amp; Dịch trước các nhóm đã chọn</button>
        <button class="danger" onclick="logoutMtproto()">Đăng xuất MTProto</button>
      </div>
      <div id="chatList" class="chatlist"></div>
    </div>
    <div id="mtMsg" class="msg"></div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Cấu hình dịch</h2>
      <label>Bộ dịch (Provider)
        <select id="provider">
          <option value="google_gtx">Google Translate miễn phí (google_gtx)</option>
          <option value="ai">AI (OpenAI-compatible / Gemini)</option>
        </select>
      </label>
      <div class="row">
        <label style="flex:1">Ngôn ngữ đích
          <input id="target_lang" value="vi">
        </label>
        <label style="flex:1">Timeout (giây)
          <input id="timeout_sec" type="number" step="0.5" min="1" value="20">
        </label>
      </div>
      <label class="check"><input id="force_target_vi" type="checkbox"> Ép dịch về ngôn ngữ đích dù UI Telegram để tiếng Anh</label>
      <label class="check"><input id="slang_fixups" type="checkbox"> Chuẩn hóa tiếng lóng nhóm Trung cho Google</label>
      <label class="check"><input id="fallback_to_google" type="checkbox"> Tự fallback sang Google miễn phí nếu AI lỗi/timeout</label>
      <label>AI Base URL
        <input id="ai_base_url" placeholder="https://api.openai.com/v1">
      </label>
      <div class="row">
        <label style="flex:1">AI API Key <span id="keyHint" class="muted"></span>
          <input id="ai_api_key" type="password" placeholder="Để trống nếu giữ key hiện tại">
        </label>
        <label style="flex:1">AI Model
          <input id="ai_model" placeholder="gpt-4o-mini">
        </label>
      </div>
      <label>Từ điển thuật ngữ AI (JSON)
        <textarea id="glossary"></textarea>
      </label>
      <div class="row">
        <button onclick="saveConfig()">Lưu &amp; Áp dụng ngay</button>
        <span id="saveMsg" class="msg"></span>
      </div>
    </div>

    <div class="card">
      <h2>Kiểm tra dịch nhanh</h2>
      <label>Tin nhắn tiếng Trung / Anh mẫu
        <textarea id="testInput">换土区我刚刚成功了，0元购反重力插件还能用吗？https://t.me/test @admin</textarea>
      </label>
      <div class="row">
        <button onclick="runTest()">Dịch thử với cấu hình đang chạy</button>
        <span id="testMeta" class="muted"></span>
      </div>
      <div id="testOut" class="outbox"></div>
    </div>
  </div>

  <div class="card">
    <div class="head">
      <h2 style="margin:0">Cache bản dịch gần đây</h2>
      <div class="row">
        <input id="searchQ" placeholder="Tìm chữ gốc hoặc bản dịch..." oninput="loadCache()">
        <button class="secondary" onclick="loadCache()">Làm mới</button>
        <button class="danger" onclick="clearCache('google_gtx')">Xóa cache Google cũ</button>
        <button class="danger" onclick="clearCache('')">Xóa toàn bộ cache</button>
      </div>
    </div>
    <table>
      <thead><tr><th>Provider</th><th>Gốc</th><th>Bản dịch</th><th></th></tr></thead>
      <tbody id="cacheBody"></tbody>
    </table>
  </div>
</div>

<script>
let cachedChats = [];
let qrPollTimer = null;

function renderChatList(items) {
  cachedChats = items || [];
  const box = document.getElementById('chatList');
  box.innerHTML = '';
  if (!cachedChats.length) {
    box.innerHTML = '<span class="muted">Chưa có nhóm nào — bấm "Tải danh sách nhóm từ Telegram".</span>';
    return;
  }
  for (const ch of cachedChats) {
    const lbl = document.createElement('label');
    lbl.className = 'check';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !!ch.enabled;
    cb.dataset.chatId = String(ch.id);
    cb.onchange = () => { ch.enabled = cb.checked; };
    const span = document.createElement('span');
    span.textContent = `${ch.title} (${ch.id})`;
    lbl.append(cb, span);
    box.appendChild(lbl);
  }
}

async function refreshMtprotoOnly() {
  const r = await fetch('/api/mtproto/status');
  const data = await r.json();
  applyMtprotoUI(data.state, null);
  if (data.state.authorized) {
    setMtMsg('Đăng nhập MTProto thành công! Đang tải danh sách nhóm...', false);
    await loadDialogs();
  } else if (data.state.needs_2fa) {
    if (qrPollTimer) { clearInterval(qrPollTimer); qrPollTimer = null; }
    setMtMsg('Tài khoản bật mật khẩu 2 bước (2FA). Nhập mật khẩu 2FA rồi bấm Xác nhận đăng nhập.', true);
  } else if (data.state.last_error) {
    if (qrPollTimer) { clearInterval(qrPollTimer); qrPollTimer = null; }
    setMtMsg(data.state.last_error, true);
  }
}

function applyMtprotoUI(mt, cfg) {
  const mtEl = document.getElementById('mtBadge');
  const authBox = document.getElementById('mtAuthBox');
  const chatsBox = document.getElementById('mtChatsBox');
  const qrWrap = document.getElementById('qrWrap');
  const statusText = document.getElementById('mtStatusText');

  if (mt.authorized) {
    mtEl.textContent = `Pre-Translate: ${mt.syncing ? 'ĐANG DỊCH...' : (mt.listening ? 'Đang nghe tin mới' : 'Đã kết nối')}`;
    mtEl.className = 'badge ok';
    authBox.classList.add('hidden');
    chatsBox.classList.remove('hidden');
    qrWrap.classList.add('hidden');
    if (qrPollTimer) { clearInterval(qrPollTimer); qrPollTimer = null; }
  } else {
    mtEl.textContent = mt.needs_2fa ? 'Pre-Translate: Cần 2FA' : 'Pre-Translate: Chưa đăng nhập';
    mtEl.className = mt.needs_2fa ? 'badge warn' : 'badge';
    authBox.classList.remove('hidden');
    chatsBox.classList.add('hidden');
    if (mt.qr_image) {
      document.getElementById('qrImg').src = mt.qr_image;
      qrWrap.classList.remove('hidden');
    } else {
      qrWrap.classList.add('hidden');
    }
  }

  const parts = [];
  if (mt.user_label) parts.push(`Tài khoản: ${mt.user_label}`);
  if (mt.current_chat) parts.push(`Đang nạp: ${mt.current_chat}`);
  parts.push(`Đã kiểm tra: ${mt.synced_checked || 0}, dịch mới: ${mt.synced_translated || 0}`);
  if (mt.last_error) parts.push(`Lỗi: ${mt.last_error}`);
  statusText.textContent = parts.join(' · ');

  if (cfg) {
    if (cfg.mtproto_api_id) document.getElementById('mt_api_id').value = cfg.mtproto_api_id;
    document.getElementById('mtHashHint').textContent = cfg.has_mtproto_api_hash ? `(đã lưu ${cfg.mtproto_api_hash_preview})` : '(chưa có)';
    document.getElementById('mt_prefetch_limit').value = cfg.mtproto_prefetch_limit || 300;
    document.getElementById('mt_batch_size').value = cfg.mtproto_batch_size || 20;
    document.getElementById('mt_enabled').checked = !!cfg.mtproto_enabled;
    document.getElementById('mt_listen_new').checked = cfg.mtproto_listen_new !== false;
    if (!cachedChats.length && Array.isArray(cfg.mtproto_chats)) {
      renderChatList(cfg.mtproto_chats);
    }
  }
}

async function refreshStatus() {
  const r = await fetch('/api/status');
  const s = await r.json();
  document.getElementById('proxyBadge').textContent = `Proxy: ${s.config.provider} → ${s.config.target_lang}`;
  document.getElementById('cacheBadge').textContent = `Cache: ${s.cached} mục`;
  const tg = s.telegram || {};
  const tgEl = document.getElementById('tgBadge');
  if (!tg.checked) {
    tgEl.textContent = 'Telegram: chưa kiểm tra';
    tgEl.className = 'badge';
  } else if (!tg.running) {
    tgEl.textContent = 'Telegram: chưa mở';
    tgEl.className = 'badge';
  } else if (tg.patched) {
    tgEl.textContent = `Telegram: ĐÃ PATCH (PID ${tg.pids.join(',')})`;
    tgEl.className = 'badge ok';
  } else {
    tgEl.textContent = `Telegram: CHƯA PATCH (PID ${tg.pids.join(',')})`;
    tgEl.className = 'badge warn';
  }
  const c = s.config;
  document.getElementById('provider').value = c.provider;
  document.getElementById('target_lang').value = c.target_lang;
  document.getElementById('timeout_sec').value = c.timeout_sec;
  document.getElementById('force_target_vi').checked = !!c.force_target_vi;
  document.getElementById('slang_fixups').checked = !!c.slang_fixups;
  document.getElementById('fallback_to_google').checked = !!c.fallback_to_google;
  document.getElementById('ai_base_url').value = c.ai_base_url || '';
  document.getElementById('ai_model').value = c.ai_model || 'gpt-4o-mini';
  document.getElementById('keyHint').textContent = c.has_ai_api_key ? `(đã lưu ${c.ai_api_key_preview})` : '(chưa có)';
  document.getElementById('glossary').value = JSON.stringify(c.glossary || {}, null, 2);
  if (s.mtproto) applyMtprotoUI(s.mtproto, c);
}

function collectMtCredentials() {
  const body = {
    mtproto_api_id: parseInt(document.getElementById('mt_api_id').value || '0', 10) || 0,
    mtproto_prefetch_limit: parseInt(document.getElementById('mt_prefetch_limit').value || '300', 10) || 300,
    mtproto_batch_size: parseInt(document.getElementById('mt_batch_size').value || '20', 10) || 20,
    mtproto_enabled: document.getElementById('mt_enabled').checked,
    mtproto_listen_new: document.getElementById('mt_listen_new').checked
  };
  const hashVal = document.getElementById('mt_api_hash').value.trim();
  if (hashVal) body.mtproto_api_hash = hashVal;
  return body;
}

function setMtMsg(text, isErr) {
  const el = document.getElementById('mtMsg');
  el.textContent = text;
  el.style.color = isErr ? 'var(--err)' : 'var(--ok)';
}

async function startQrLogin() {
  setMtMsg('Đang tạo mã QR...', false);
  try {
    const r = await fetch('/api/mtproto/qr', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(collectMtCredentials())
    });
    const res = await r.json();
    if (!r.ok) { setMtMsg(res.error || 'Lỗi tạo QR', true); return; }
    applyMtprotoUI(res.state, null);
    if (res.state.authorized) {
      setMtMsg('Tài khoản đã đăng nhập sẵn. Đang tải danh sách nhóm...', false);
      await loadDialogs();
      return;
    }
    setMtMsg('Mở Telegram trên điện thoại → Settings → Devices → Link Desktop Device để quét mã.', false);
    if (qrPollTimer) clearInterval(qrPollTimer);
    qrPollTimer = setInterval(async () => {
      await refreshMtprotoOnly();
    }, 2000);
  } catch (e) {
    setMtMsg('Lỗi kết nối khi tạo mã QR: ' + e.message, true);
  }
}

async function sendPhoneCode() {
  const body = collectMtCredentials();
  body.phone = document.getElementById('mt_phone').value.trim();
  const r = await fetch('/api/mtproto/code', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const res = await r.json();
  if (!r.ok) { setMtMsg(res.error || 'Gửi OTP thất bại', true); return; }
  setMtMsg('Đã gửi mã OTP về Telegram của bạn. Nhập mã rồi bấm Xác nhận.', false);
}

async function completeSignIn() {
  const r = await fetch('/api/mtproto/signin', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      code: document.getElementById('mt_code').value.trim(),
      password: document.getElementById('mt_password').value
    })
  });
  const res = await r.json();
  if (!r.ok) { setMtMsg(res.error || 'Đăng nhập thất bại', true); return; }
  applyMtprotoUI(res.state, null);
  if (res.state.authorized) {
    setMtMsg('Đăng nhập MTProto thành công.', false);
    await loadDialogs();
  }
}

async function loadDialogs() {
  setMtMsg('Đang tải danh sách nhóm...', false);
  const r = await fetch('/api/mtproto/dialogs');
  const res = await r.json();
  if (!r.ok) { setMtMsg(res.error || 'Không tải được nhóm', true); return; }
  renderChatList(res.dialogs || []);
  setMtMsg(`Đã tải ${res.dialogs.length} nhóm/kênh. Tick chọn nhóm cần dịch trước rồi bấm Lưu & Dịch trước ngay.`, false);
}

async function saveMtprotoAndSync() {
  const body = collectMtCredentials();
  body.mtproto_enabled = true;
  document.getElementById('mt_enabled').checked = true;
  body.mtproto_chats = cachedChats.map(c => ({id: c.id, title: c.title, enabled: !!c.enabled}));
  const r = await fetch('/api/mtproto/sync', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const res = await r.json();
  if (!r.ok) { setMtMsg(res.error || 'Lỗi đồng bộ', true); return; }
  applyMtprotoUI(res.state, null);
  setMtMsg('Đã bật dịch trước và bắt đầu nạp cache nhóm đã chọn.', false);
  setTimeout(refreshStatus, 2500);
}

async function logoutMtproto() {
  await fetch('/api/mtproto/logout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
  cachedChats = [];
  await refreshStatus();
  setMtMsg('Đã đăng xuất MTProto.', false);
}

async function saveConfig() {
  const msg = document.getElementById('saveMsg');
  let glossary;
  try {
    glossary = JSON.parse(document.getElementById('glossary').value || '{}');
  } catch (e) {
    msg.textContent = 'Lỗi JSON từ điển: ' + e.message;
    msg.style.color = 'var(--err)';
    return;
  }
  const body = {
    provider: document.getElementById('provider').value,
    target_lang: document.getElementById('target_lang').value.trim() || 'vi',
    timeout_sec: parseFloat(document.getElementById('timeout_sec').value) || 20.0,
    force_target_vi: document.getElementById('force_target_vi').checked,
    slang_fixups: document.getElementById('slang_fixups').checked,
    fallback_to_google: document.getElementById('fallback_to_google').checked,
    ai_base_url: document.getElementById('ai_base_url').value.trim(),
    ai_model: document.getElementById('ai_model').value.trim() || 'gpt-4o-mini',
    glossary
  };
  const keyVal = document.getElementById('ai_api_key').value.trim();
  if (keyVal) body.ai_api_key = keyVal;

  const r = await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  const res = await r.json();
  if (r.ok) {
    document.getElementById('ai_api_key').value = '';
    msg.textContent = 'Đã lưu và áp dụng ngay.';
    msg.style.color = 'var(--ok)';
    await refreshStatus();
  } else {
    msg.textContent = res.error || 'Lưu thất bại';
    msg.style.color = 'var(--err)';
  }
}

async function runTest() {
  const out = document.getElementById('testOut');
  const meta = document.getElementById('testMeta');
  out.textContent = 'Đang dịch...';
  const r = await fetch('/api/test', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: document.getElementById('testInput').value})
  });
  const res = await r.json();
  if (r.ok) {
    out.textContent = res.translated;
    meta.textContent = `via ${res.used_provider} (${res.elapsed_ms} ms)`;
  } else {
    out.textContent = 'Lỗi: ' + (res.error || r.status);
    meta.textContent = '';
  }
}

async function loadCache() {
  const q = encodeURIComponent(document.getElementById('searchQ').value.trim());
  const r = await fetch(`/api/cache?limit=40&q=${q}`);
  const data = await r.json();
  const tbody = document.getElementById('cacheBody');
  tbody.innerHTML = '';
  for (const row of data.items || []) {
    const tr = document.createElement('tr');
    const tdProv = document.createElement('td');
    tdProv.className = 'mono muted';
    tdProv.textContent = `${row.provider}→${row.target_lang}`;
    const tdSrc = document.createElement('td');
    tdSrc.textContent = row.source_text;
    const tdDst = document.createElement('td');
    tdDst.textContent = row.translated_text;
    const tdAct = document.createElement('td');
    const btn = document.createElement('button');
    btn.className = 'secondary';
    btn.textContent = 'Xóa';
    btn.onclick = async () => {
      await fetch(`/api/cache?key=${encodeURIComponent(row.cache_key)}`, {method: 'DELETE'});
      await loadCache();
      await refreshStatus();
    };
    tdAct.appendChild(btn);
    tr.append(tdProv, tdSrc, tdDst, tdAct);
    tbody.appendChild(tr);
  }
}

async function clearCache(provider) {
  await fetch('/api/cache/clear', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({provider})
  });
  await loadCache();
  await refreshStatus();
}

refreshStatus();
loadCache();
</script>
</body>
</html>
"""
