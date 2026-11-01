"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const GB = 1024 ** 3;
const fmt = b => !b || b <= 0 ? "?" : b >= GB ? (b / GB).toFixed(1) + " GB" : b >= 1048576 ? (b / 1048576).toFixed(0) + " MB" : b >= 1024 ? (b / 1024).toFixed(0) + " KB" : b + " B";
const fmtN = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n);

let S = null;            // latest app state
let tab = "home";
let attachment = null;
let filesDir = null;
let lastMsgVersion = -1;

async function api(action, body = {}) {
  const r = await fetch("/api/" + action, { method: "POST", headers: { "Content-Type": "application/json", "X-LF-Token": LF_TOKEN }, body: JSON.stringify(body) });
  const j = await r.json();
  if (j.error) toast(j.error);
  if (j.message) toast(j.message);
  return j;
}
async function get(path) {
  const r = await fetch(path, { headers: { "X-LF-Token": LF_TOKEN } });
  return r.json();
}
let toastTimer;
function toast(t) {
  const el = $("#toast"); el.textContent = t; el.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.add("hidden"), 3500);
}

// ---------- state loop ----------
async function poll() {
  let v = -1;
  for (;;) {
    try {
      S = await get("/api/state?v=" + v);
      v = S.version;
      render();
      if (S.generating || S.runningCode || lastMsgVersion < 0 || tab === "chat" || sideOpen) await syncMessages();
    } catch (e) {
      $("#nav-status").innerHTML = '<span class="pill bad">Disconnected</span>';
      await new Promise(r => setTimeout(r, 1500));
    }
  }
}

function setTab(t) {
  tab = t;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  document.querySelectorAll(".page").forEach(p => p.classList.toggle("active", p.id === "tab-" + t));
  if (t === "files" && filesDir === null) openDir(S?.home || "");
  if (t === "chat") { if (sideOpen) dockChat(false); syncMessages(); setTimeout(() => $("#input").focus(), 50); }
  if (t === "browser") startBrowser();
  render();
}
document.querySelectorAll(".tab").forEach(b => b.onclick = () => setTab(b.dataset.tab));

function render() {
  if (!S) return;
  const st = S.loading ? '<span class="pill warn">Loading model…</span>' : S.generating ? '<span class="pill warn">Thinking…</span>' :
    S.runningCode ? '<span class="pill warn">Running code…</span>' : S.loadedModel ? '<span class="pill ok">Ready</span>' : '<span class="pill">No model loaded</span>';
  $("#nav-status").innerHTML = st;
  $("#nav-model").textContent = S.loadedModel ? S.loadedModel.replace(/\.gguf$/i, "") : "";
  $("#chat-model").innerHTML = S.loadedModel ? `${esc(S.loadedModel.replace(/\.gguf$/i, ""))} · ${esc((S.backend || "").toUpperCase())}` : "No model loaded — pick one on the Home tab";
  $("#auto-approve").checked = !!S.settings.autoApprove;
  $("#send").classList.toggle("hidden", S.generating);
  $("#stop").classList.toggle("hidden", !S.generating);
  if (S.preview && $("#modal").dataset.preview !== S.preview) showPreview(S.preview);
  if (window._bf !== undefined && S.browserFocus > window._bf && tab !== "browser") toast("Ran in the browser. Open the Browser tab to see it.");
  window._bf = S.browserFocus;
  if (tab === "home") renderHome();
  if (tab === "models") renderModels();
  if (tab === "browser") renderBrowser();
}

// ---------- HOME ----------
function renderHome() {
  const el = $("#tab-home");
  const pc = S.pc, s = S.settings;
  const focused = el.contains(document.activeElement) && document.activeElement.matches("input,select");
  if (focused) return;           // don't wipe a field the user is typing in
  const gpu = pc && pc.bestGpu;
  const runners = Object.entries(S.tools).map(([k, v]) => `<span class="pill ok" title="${esc(v)}">${esc(k)}</span>`).join(" ");
  const models = S.models.map(m => `
    <div class="item ${S.loadedPath === m.path ? "selected" : ""}">
      <div class="grow"><div class="title">${esc(m.name.replace(/\.gguf$/i, ""))}</div>
        <div class="muted small">${fmt(m.size)} · ${m.source === "found" ? "found on your PC" : "downloaded"}</div></div>
      ${S.loadedPath === m.path ? '<button class="small" data-unload>Unload</button>' : `<button class="small primary" data-load="${esc(m.path)}" ${S.loading ? "disabled" : ""}>Load & use</button>`}
    </div>`).join("");
  el.innerHTML = `
    <h1>LocalForge</h1>
    <p class="sub">Your private, uncensored AI. Runs 100% on this PC — nothing you type leaves your computer.</p>
    ${S.backends.length ? "" : '<div class="banner">The llama.cpp engine folder is missing. Keep the <b>engines</b> folder next to LocalForge.exe (re-extract the zip).</div>'}
    ${S.status ? `<div class="card" style="margin-bottom:16px">${esc(S.status)}</div>` : ""}
    <div class="grid">
      <div class="card stack">
        <div><h2>Model</h2>
          ${S.loadedModel ? `<div><span class="pill ok">Loaded</span> <b>${esc(S.loadedModel)}</b><div class="muted small">${esc((S.backend || "").toUpperCase())} engine ${S.gpuInfo ? "· " + esc(S.gpuInfo) : ""}</div></div>` :
            S.models.length ? '<div class="muted">Pick a model below to start.</div>' : '<div class="muted">No models yet. Open the <a href="#" data-go="models">Models</a> tab and click <b>Find the best models for my PC</b>.</div>'}
        </div>
        <div class="list">${models}</div>
        <div class="row wrap"><button class="small" data-go="models">Get more models</button><button class="small ghost" data-addmodel>Add a .gguf file…</button>
          ${S.loadedModel ? '<button class="small primary" data-go="chat">Open chat →</button>' : ""}</div>
      </div>
      <div class="card">
        <h2>Your PC</h2>
        ${pc ? `<div class="kv">
          <div>System</div><div>${esc(pc.os)}</div>
          <div>CPU</div><div>${esc(pc.cpu)} (${pc.cores} cores)</div>
          <div>Memory</div><div>${fmt(pc.totalRam)} (${fmt(pc.availRam)} free)</div>
          <div>Graphics</div><div>${gpu ? `${esc(gpu.name)} · ${fmt(gpu.vram)} VRAM` : "No dedicated graphics card found (CPU mode)"}</div>
          <div>Free disk</div><div>${fmt(pc.freeDisk)}</div>
          <div>Model budget</div><div>up to ~${fmt(pc.modelBudget)} model files run well</div>
          <div>Engines</div><div>${S.backends.map(b => `<span class="pill">${esc(b.toUpperCase())}</span>`).join(" ") || '<span class="pill bad">missing</span>'}</div>
        </div>` : '<div class="muted">Checking your hardware…</div>'}
      </div>
      <div class="card">
        <h2>Settings</h2>
        <div class="fields">
          <div class="field"><label>Context size (tokens)</label><select data-set="nCtx">${[2048, 4096, 8192, 16384, 32768, 65536].map(v => `<option ${v == s.nCtx ? "selected" : ""}>${v}</option>`).join("")}</select></div>
          <div class="field"><label>Max reply length</label><select data-set="maxTokens">${[512, 1024, 2048, 4096, 8192].map(v => `<option ${v == s.maxTokens ? "selected" : ""}>${v}</option>`).join("")}</select></div>
          <div class="field"><label>Creativity (temperature)</label><input type="number" step="0.1" min="0" max="2" data-set="temperature" value="${s.temperature}"></div>
          <div class="field"><label>Engine</label><select data-set="backend">${["auto", "cuda", "vulkan", "cpu"].map(v => `<option ${v == s.backend ? "selected" : ""}>${v}</option>`).join("")}</select></div>
          <div class="field"><label>GPU layers (-1 = auto)</label><input type="number" min="-1" max="999" data-set="gpuLayers" value="${s.gpuLayers}"></div>
          <div class="field"><label>CPU threads (0 = auto)</label><input type="number" min="0" max="256" data-set="threads" value="${s.threads}"></div>
        </div>
        <div style="margin-top:12px" class="stack">
          <label class="toggle"><input type="checkbox" data-set="think" ${s.think ? "checked" : ""}> Think harder: let reasoning models (Qwen 3.x, Gemma 4) think before answering. Smarter on hard problems, but much slower</label>
          <label class="toggle"><input type="checkbox" data-set="autoApprove" ${s.autoApprove ? "checked" : ""}> Let the AI write files and run code without asking (deletes always ask)</label>
          <div class="field"><label>Hugging Face token (only for gated models) ${s.hfToken ? '<span class="pill ok">saved</span>' : ""}</label>
            <input type="password" placeholder="hf_…" data-set="hfToken"></div>
          <div class="muted small">Engine, context, GPU layers and threads apply the next time you load a model.</div>
        </div>
      </div>
      <div class="card stack">
        <div><h2>The AI can run code</h2><div class="row wrap">${runners}</div>
          <div class="muted small" style="margin-top:6px">Python works out of the box. Install Node, Java, Go, Rust, a C compiler etc. and they appear here automatically.</div>
          <div class="muted small">Code runs in <a href="#" data-open="${esc(S.workDir)}">${esc(S.workDir)}</a></div></div>
        <div><h2>File search</h2><div class="muted">${S.index.ready ? `${S.index.count.toLocaleString()} files indexed so the AI can find things on your PC.` : "Indexing your files…"} ${S.index.building ? "(updating…)" : ""}</div>
          <button class="small" data-reindex style="margin-top:6px">Re-index now</button></div>
        <div><button class="small danger" data-quit>Quit LocalForge</button></div>
      </div>
    </div>`;
}

document.addEventListener("click", async e => {
  const t = e.target.closest("[data-go],[data-load],[data-unload],[data-addmodel],[data-reindex],[data-quit],[data-open],[data-dl],[data-cancel],[data-repo],[data-delmodel],[data-suggest]");
  if (!t) return;
  e.preventDefault();
  const d = t.dataset;
  if (d.go) setTab(d.go);
  else if (d.load) { await api("load", { path: d.load }); }
  else if ("unload" in d) api("unload");
  else if ("addmodel" in d) {
    const r = await api("add-model", {});
    if (r && !r.error && !S.models.length) toast("If no file dialog opened, copy the .gguf into: " + S.modelsDir);
  }
  else if ("reindex" in d) api("reindex");
  else if ("quit" in d) { if (confirm("Quit LocalForge?")) { await api("quit"); document.body.innerHTML = "<p style='padding:40px'>LocalForge has closed. You can close this window.</p>"; } }
  else if (d.open) api("open-path", { path: d.open });
  else if (d.dl) api("download", { file: JSON.parse(d.dl) });
  else if (d.cancel) api("cancel-download", { name: d.cancel });
  else if (d.repo !== undefined) api("repo", { repo: d.repo || null });
  else if (d.delmodel) { if (confirm("Delete this model?")) api("delete-model", { path: d.delmodel }); }
  else if (d.suggest) { $("#input").value = d.suggest; send(); }
});
document.addEventListener("change", e => {
  const k = e.target.dataset.set;
  if (!k) return;
  let v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
  if (k === "hfToken") e.target.value = "";
  api("settings", { [k]: v });
});

// ---------- MODELS ----------
function fileRow(f, extra = "") {
  const dl = S.downloads.find(d => d.name === f.fileName);
  const have = S.models.some(m => m.name === f.fileName);
  let right;
  if (have) right = '<span class="pill ok">Downloaded</span>';
  else if (dl && dl.state === "running") right = `<button class="small" data-cancel="${esc(dl.name)}">Cancel</button>`;
  else right = `<button class="small primary" data-dl='${esc(JSON.stringify(f))}'>${dl ? "Resume" : "Download"} ${fmt(f.size)}</button>`;
  const prog = dl ? `<div class="bar"><div style="width:${dl.total ? (100 * dl.done / dl.total).toFixed(1) : 0}%"></div></div>
    <div class="muted small">${dl.state === "error" ? '<span style="color:var(--bad)">' + esc(dl.error) + "</span>" : `${fmt(dl.done)} / ${fmt(dl.total)} · ${fmt(dl.speed)}/s`}</div>` : "";
  return `<div class="item"><div class="grow"><div class="title">${esc(f.fileName)}</div>${extra}${prog}</div>${right}</div>`;
}

function renderModels() {
  const el = $("#tab-models");
  if (el.contains(document.activeElement) && document.activeElement.id === "search") {
    // keep the search box; only refresh results
  }
  const r = S.recs, s = S.search, repo = S.repo;
  const recs = r.items.map(p => fileRow(p.file, `<div class="small">${esc(p.reason)}</div><div class="muted small">${esc(p.file.repo)} · ${fmtN(p.downloads)} downloads</div>`)).join("");
  const results = repo.repo ? `
      <div class="row"><button class="small ghost" data-repo="">← Back to results</button><b>${esc(repo.repo)}</b></div>
      ${repo.busy ? '<div class="muted">Loading files…</div>' : ""}${repo.error ? `<div class="muted">${esc(repo.error)}</div>` : ""}
      <div class="muted small">Q4_K_M is the best balance. Q5/Q6/Q8 are a bit smarter but bigger. Avoid Q2/Q3. Your budget: ~${fmt(S.pc?.modelBudget)}.</div>
      <div class="list">${repo.files.map(f => fileRow(f, `<div class="muted small">${esc(f.quant)}${f.parts.length > 1 ? ` · ${f.parts.length} parts` : ""}${S.pc && f.size > S.pc.modelBudget ? ' · <span style="color:var(--warn)">bigger than your PC handles well</span>' : ""}</div>`)).join("")}</div>`
    : `${s.busy ? '<div class="muted">Searching…</div>' : ""}${s.error ? `<div class="muted">${esc(s.error)}</div>` : ""}
      <div class="list">${s.results.map(m => `<div class="item clickable" data-repo="${esc(m.id)}"><div class="grow"><div class="title">${esc(m.id)}</div>
        <div class="muted small">${m.params ? m.params + "B · " : ""}${fmtN(m.downloads)} downloads · ${fmtN(m.likes)} likes</div></div><span class="muted">›</span></div>`).join("")}</div>`;
  const dls = S.downloads.filter(d => !S.recs.items.some(p => p.file.fileName === d.name)).map(d => `
      <div class="item"><div class="grow"><div class="title">${esc(d.name)}</div><div class="bar"><div style="width:${d.total ? (100 * d.done / d.total).toFixed(1) : 0}%"></div></div>
      <div class="muted small">${d.state === "error" ? esc(d.error) : `${fmt(d.done)} / ${fmt(d.total)} · ${fmt(d.speed)}/s`}</div></div>
      <button class="small" data-cancel="${esc(d.name)}">${d.state === "running" ? "Cancel" : "Remove"}</button></div>`).join("");
  const local = S.models.map(m => `<div class="item"><div class="grow"><div class="title">${esc(m.name)}</div><div class="muted small">${fmt(m.size)} · ${esc(m.path)}</div></div>
      ${S.loadedPath === m.path ? '<span class="pill ok">Loaded</span>' : `<button class="small primary" data-load="${esc(m.path)}">Load</button>`}
      <button class="small ghost" data-delmodel="${esc(m.path)}" title="${m.source === "found" ? "Remove from list" : "Delete file"}">🗑</button></div>`).join("");
  const searchVal = $("#search")?.value ?? s.query;
  const hadFocus = document.activeElement?.id === "search";
  el.innerHTML = `
    <h1>Models</h1>
    <p class="sub">Only uncensored / abliterated models are shown. Files come from Hugging Face and are saved in ${esc(S.modelsDir)}.</p>
    <div class="grid">
      <div class="card stack">
        <div class="row"><h2 style="margin:0">Best for your PC</h2><div class="spacer"></div>
          <button class="primary small" id="scan" ${r.busy ? "disabled" : ""}>${r.busy ? "Scanning…" : "Find the best models for my PC"}</button></div>
        ${r.summary ? `<div class="muted">${esc(r.summary)}</div>` : '<div class="muted">Checks your graphics card, memory and disk, looks for models already on your PC, and picks the smartest uncensored models that will run fast.</div>'}
        <div class="list">${recs}</div>
      </div>
      <div class="card stack">
        <h2>Search Hugging Face</h2>
        <form class="row" id="search-form"><input id="search" style="flex:1" placeholder="e.g. qwen3 14b, gemma 3, mistral small, coder" value="${esc(searchVal)}"><button class="primary">Search</button></form>
        ${results}
      </div>
      ${dls ? `<div class="card stack"><h2>Downloads</h2><div class="list">${dls}</div></div>` : ""}
      <div class="card stack"><div class="row"><h2 style="margin:0">On this PC</h2><div class="spacer"></div><button class="small ghost" data-addmodel>Add a .gguf file…</button></div>
        <div class="list">${local || '<div class="muted">No models yet.</div>'}</div></div>
    </div>`;
  if (hadFocus) { const i = $("#search"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
  $("#scan").onclick = () => api("scan");
  $("#search-form").onsubmit = e => { e.preventDefault(); api("search", { query: $("#search").value }); };
}

// ---------- CHAT ----------
function md(src) {
  // Minimal, safe markdown: code fences, inline code, bold/italic, headings, lists, links.
  const out = [];
  const parts = src.split(/(```[^\n]*\n[\s\S]*?(?:```|$))/g);
  for (const part of parts) {
    const m = part.match(/^```([^\n]*)\n([\s\S]*?)(?:```)?$/);
    if (m && part.startsWith("```")) {
      const lang = m[1].trim(), code = m[2].replace(/\n$/, "");
      const runnable = /^(python|py|python3|js|javascript|node|ts|typescript|html|sql|sqlite|powershell|ps1|pwsh|bat|cmd|batch|sh|bash|shell|c|cpp|c\+\+|java|go|golang|rust|rs|ruby|rb|php|lua|perl|kotlin|kt)$/i.test(lang);
      out.push(`<div class="codeblock"><div class="cb-head"><span>${esc(lang || "code")}</span><div class="spacer"></div>
        <button class="small ghost" data-copy>Copy</button>${runnable ? `<button class="small" data-run="${esc(lang)}">▶ Run</button>` : ""}</div><pre><code>${esc(code)}</code></pre></div>`);
      continue;
    }
    let h = esc(part)
      .replace(/&lt;(write|run|type|js)[\s\S]*?(&lt;\/\1&gt;|$)/g, "")       // tool tags are shown as cards instead
      .replace(/&lt;(find|list|read|mkdir|delete|open|search|browse|page|extract|click|press|scroll)(\s[^&]*?)?\/?&gt;/g, m => `<span class="pill">${m}</span>`)
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    const lines = h.split("\n");
    let html = "", list = null, table = [];
    const flushTable = () => {
      if (!table.length) return;
      const rows = table.map(r => r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim()));
      const sep = rows.length > 1 && rows[1].every(c => /^:?-{2,}:?$/.test(c));
      const head = sep ? rows[0] : null, body = sep ? rows.slice(2) : rows;
      html += `<div class="tablewrap"><table>${head ? `<thead><tr>${head.map(c => `<th>${c}</th>`).join("")}</tr></thead>` : ""}<tbody>${body.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      table = [];
    };
    for (const line of lines) {
      if (/^\s*\|.*\|\s*$/.test(line)) { if (list) { html += `</${list}>`; list = null; } table.push(line); continue; }
      flushTable();
      const li = line.match(/^\s*([-*•]|\d+[.)])\s+(.*)$/);
      if (li) {
        const type = /\d/.test(li[1]) ? "ol" : "ul";
        if (list !== type) { if (list) html += `</${list}>`; html += `<${type}>`; list = type; }
        html += `<li>${li[2]}</li>`;
        continue;
      }
      if (list) { html += `</${list}>`; list = null; }
      const hd = line.match(/^(#{1,4})\s+(.*)$/);
      if (hd) html += `<h${hd[1].length > 2 ? 4 : 3}>${hd[2]}</h${hd[1].length > 2 ? 4 : 3}>`;
      else if (line.trim()) html += `<p>${line}</p>`;
    }
    flushTable();
    if (list) html += `</${list}>`;
    out.push(html);
  }
  return out.join("");
}

const rendered = new Map();   // id -> signature
function msgSig(m) { return m.text.length + "|" + m.text.slice(-20) + "|" + (m.stats || "") + "|" + m.actions.map(a => a.status + (a.result || "")).join(","); }

function renderMsg(m, isLast) {
  const who = { user: "You", assistant: "LocalForge", tool: "🔧 Tool results", run: "▶ Code output", note: "" }[m.role];
  let body;
  if (m.role === "assistant") {
    const thinkingOnly = m.text.replace(/⁣/g, "").trim() === "" && S.generating && isLast;
    body = thinkingOnly ? '<span class="thinking">Thinking…</span>' : md(m.text.replace(/⁣/g, "").replace(/<think>[\s\S]*?(<\/think>|$)/gi, ""));
    if (S.generating && isLast && !thinkingOnly) body += '<span class="cursor"></span>';
  } else if (m.role === "tool") {
    const first = m.text.split("\n")[0];
    const web = /^(Browser|Web search|Clicked|Typed|Pressed|Scrolled|Script result)/.test(first);
    body = `<details><summary>${web ? "🌐 " : ""}${esc(first.slice(0, 140))}</summary>${esc(m.text)}</details>${web ? '<button class="small ghost" data-go="browser" style="margin-top:6px">View in Browser →</button>' : ""}`;
  } else if (m.role === "user") body = md(m.text);
  else body = esc(m.text) + (m.role === "run" && /^[✓✗⏱] (javascript|html) \(browser\)/.test(m.text) ? '<div><button class="small ghost" data-go="browser" style="margin-top:6px">View in Browser →</button></div>' : "");
  const actions = (m.actions || []).map((a, i) => {
    const act = a.action;
    const preview = act.kind === "write" || act.kind === "run" ? `<pre class="mono">${esc(act.content)}</pre>` : "";
    const btns = a.status === "pending" ? `<div class="row"><button class="small primary" data-approve="${m.id}:${i}">${act.kind === "run" ? "▶ Run it" : act.kind === "delete" ? "Delete" : "Approve"}</button><button class="small ghost" data-reject="${m.id}:${i}">Reject</button></div>` :
      `<div class="small ${a.status === "done" ? "" : "muted"}">${a.status === "done" ? "✓ " + esc(a.result || "Done") : "✗ " + esc(a.result || "Rejected")}</div>`;
    return `<div class="action ${a.status}"><b>${esc(act.label)}</b>${preview}${btns}</div>`;
  }).join("");
  return `${who ? `<div class="who">${who}</div>` : ""}<div class="bubble">${body}${actions}</div>${m.stats ? `<div class="stats">${esc(m.stats)}</div>` : ""}`;
}

async function syncMessages() {
  const r = await get("/api/messages");
  lastMsgVersion = r.version;
  const box = $("#messages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  const msgs = r.messages;
  if (!msgs.length) {
    rendered.clear();
    if (sideOpen) {
      box.innerHTML = `<div class="empty"><h2>What should I do on this page?</h2><p>I can read it, pull its data into a table, click, type and fill in forms. You'll see each step happen on the page.</p>
      <div class="suggest">${["Summarise this page", "Extract the data on this page as a table", "Find the cheapest item here", "Go to the next page and collect the results"].map(s => `<button class="small" data-suggest="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;
      return;
    }
    box.innerHTML = `<div class="empty"><h2>What do you want to do?</h2><p>Everything runs on this PC. The AI can search and read your files, write code and run it, and answer anything — no filters.</p>
      <div class="suggest">${["Find my biggest videos", "Write a Python script that renames photos by date and test it", "What's in my Downloads folder?", "Explain how transformers work, in depth"].map(s => `<button class="small" data-suggest="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;
    return;
  }
  if (box.querySelector(".empty")) box.innerHTML = "";
  const ids = new Set(msgs.map(m => m.id));
  for (const [id] of rendered) if (!ids.has(id)) { rendered.delete(id); box.querySelector(`[data-id="${id}"]`)?.remove(); }
  msgs.forEach((m, i) => {
    const isLast = i === msgs.length - 1;
    const sig = msgSig(m) + (isLast && S?.generating ? "g" : "");
    let el = box.querySelector(`[data-id="${m.id}"]`);
    if (!el) { el = document.createElement("div"); el.dataset.id = m.id; box.appendChild(el); }
    el.className = "msg " + m.role;
    if (rendered.get(m.id) !== sig) { el.innerHTML = renderMsg(m, isLast); rendered.set(m.id, sig); }
  });
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

$("#messages").addEventListener("click", e => {
  const ap = e.target.closest("[data-approve]"), rj = e.target.closest("[data-reject]"), run = e.target.closest("[data-run]"), cp = e.target.closest("[data-copy]");
  if (ap) { const [m, i] = ap.dataset.approve.split(":"); api("approve", { msg: +m, idx: +i }); }
  if (rj) { const [m, i] = rj.dataset.reject.split(":"); api("reject", { msg: +m, idx: +i }); }
  if (run) { const code = run.closest(".codeblock").querySelector("code").textContent; api("run-snippet", { lang: run.dataset.run, code }); }
  if (cp) { navigator.clipboard.writeText(cp.closest(".codeblock").querySelector("code").textContent); toast("Copied"); }
});

function send() {
  const text = $("#input").value.trim();
  if (!text) return;
  if (S && !S.loadedModel && !S.models.length) { toast("Download a model on the Models tab first."); setTab("models"); return; }
  api("send", { text, attachment, onPage: sideOpen });
  $("#input").value = ""; autosize();
  attachment = null; renderAttachment();
  if (tab !== "chat" && !sideOpen) setTab("chat");
}
function autosize() { const i = $("#input"); i.style.height = "auto"; i.style.height = Math.min(240, i.scrollHeight) + "px"; }
$("#input").addEventListener("input", autosize);
$("#input").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!S?.generating) send(); } });
$("#send").onclick = send;
$("#stop").onclick = () => api("stop");
$("#clear-chat").onclick = () => { if (confirm("Start a new chat? The current one will be cleared.")) api("clear"); };
$("#auto-approve").onchange = e => api("settings", { autoApprove: e.target.checked });
$("#attach").onclick = async () => {
  const r = await api("pick-file");
  if (r.path) { attachment = r.path; renderAttachment(); }
  else if (r.path === null) {
    const p = prompt("Full path of the file to attach (or attach it from the Files tab):");
    if (p) { attachment = p; renderAttachment(); }
  }
};
function renderAttachment() {
  const el = $("#attachment");
  el.classList.toggle("hidden", !attachment);
  if (attachment) { el.innerHTML = `📎 ${esc(attachment.split(/[\\/]/).pop())} <button class="small ghost" id="rm-att">✕</button>`; $("#rm-att").onclick = () => { attachment = null; renderAttachment(); }; }
}

// ---------- preview modal ----------
function showPreview(path) {
  const m = $("#modal");
  m.dataset.preview = path;
  $("#modal-title").textContent = path.split(/[\\/]/).pop();
  $("#modal-actions").innerHTML = `<button class="small ghost" data-open="${esc(path)}">Open in browser</button>`;
  $("#modal-body").innerHTML = `<iframe sandbox="allow-scripts allow-forms allow-modals" src="/preview?t=${encodeURIComponent(LF_TOKEN)}&path=${encodeURIComponent(path)}"></iframe>`;
  m.classList.remove("hidden");
}
function closeModal() {
  const m = $("#modal");
  if (m.dataset.preview) api("close-preview");
  m.dataset.preview = ""; m.classList.add("hidden"); $("#modal-body").innerHTML = "";
}
$("#modal-close").onclick = closeModal;

// ---------- FILES ----------
async function openDir(dir) {
  const r = await get("/api/files?dir=" + encodeURIComponent(dir));
  filesDir = r.dir;
  const el = $("#tab-files");
  const sep = filesDir.includes("\\") ? "\\" : "/";
  let crumbs = "";
  if (filesDir !== "::drives") {
    const parts = filesDir.split(/[\\/]/).filter(Boolean);
    let acc = filesDir.startsWith("/") ? "" : "";
    crumbs = parts.map((p, i) => {
      acc = i === 0 ? (filesDir.startsWith("/") ? "/" + p : p + sep) : acc + (acc.endsWith(sep) ? "" : sep) + p;
      return `<button class="small ghost" data-dir="${esc(acc)}">${esc(p)}</button>`;
    }).join('<span class="muted">›</span>');
  }
  const rows = r.entries.filter(e => !e.hidden).map(e => `
    <div class="item clickable" data-${e.dir ? "dir" : "file"}="${esc(e.path)}">
      <span>${e.dir ? "📁" : fileIcon(e.name)}</span>
      <div class="grow"><div class="title">${esc(e.name)}</div><div class="muted small">${e.dir ? "" : fmt(e.size) + " · "}${e.mtime ? new Date(e.mtime * 1000).toLocaleString() : ""}</div></div>
      ${e.dir ? "" : `<button class="small ghost" data-ask="${esc(e.path)}" title="Ask the AI about this file">🤖 Ask AI</button>`}
      <button class="small ghost" data-sys="${esc(e.path)}" title="Open with Windows">↗</button>
      <button class="small ghost" data-ren="${esc(e.path)}" title="Rename">✎</button>
      <button class="small ghost" data-del="${esc(e.path)}" title="Delete">🗑</button>
    </div>`).join("");
  el.innerHTML = `
    <h1>Files</h1>
    <div class="row wrap" style="margin:10px 0 14px">
      <button class="small" data-dir="::drives">💽 Drives</button>
      <button class="small" data-dir="${esc(S?.home || "")}">🏠 Home</button>
      ${r.parent ? `<button class="small" data-dir="${esc(r.parent)}">⬆ Up</button>` : ""}
      <div class="crumbs">${crumbs}</div><div class="spacer"></div>
      ${filesDir !== "::drives" ? '<button class="small" id="new-file">New file</button><button class="small" id="new-folder">New folder</button>' : ""}
    </div>
    ${r.error ? `<div class="banner">${esc(r.error)}</div>` : ""}
    <div class="list">${rows || '<div class="muted">Empty folder</div>'}</div>`;
  const nf = $("#new-file"), nd = $("#new-folder");
  if (nf) nf.onclick = async () => { const n = prompt("New file name:"); if (n) { await api("file-op", { op: "create", path: filesDir, name: n }); openDir(filesDir); } };
  if (nd) nd.onclick = async () => { const n = prompt("New folder name:"); if (n) { await api("file-op", { op: "mkdir", path: filesDir, name: n }); openDir(filesDir); } };
}
function fileIcon(n) {
  const x = n.split(".").pop().toLowerCase();
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "heic"].includes(x)) return "🖼";
  if (["mp4", "mkv", "mov", "avi", "webm"].includes(x)) return "🎞";
  if (["mp3", "wav", "flac", "m4a", "ogg"].includes(x)) return "🎵";
  if (["zip", "rar", "7z", "gz"].includes(x)) return "🗜";
  if (["exe", "msi"].includes(x)) return "⚙";
  if (["py", "js", "ts", "html", "css", "json", "c", "cpp", "java", "go", "rs", "cs"].includes(x)) return "📜";
  if (x === "gguf") return "🧠";
  return "📄";
}
$("#tab-files").addEventListener("click", async e => {
  const t = e.target.closest("[data-dir],[data-file],[data-ask],[data-sys],[data-ren],[data-del]");
  if (!t) return;
  e.stopPropagation();
  const d = t.dataset;
  if (d.ask) { attachment = d.ask; renderAttachment(); if (!$("#input").value) $("#input").value = "Explain this file and suggest improvements."; setTab("chat"); return; }
  if (d.sys) { api("open-path", { path: d.sys }); return; }
  if (d.ren) { const n = prompt("New name:", d.ren.split(/[\\/]/).pop()); if (n) { await api("file-op", { op: "rename", path: d.ren, name: n }); openDir(filesDir); } return; }
  if (d.del) { if (confirm("Delete " + d.del + "?")) { await api("file-op", { op: "delete", path: d.del }); openDir(filesDir); } return; }
  if (d.dir !== undefined) { openDir(d.dir); return; }
  if (d.file) openEditor(d.file);
});
async function openEditor(path) {
  const r = await get("/api/file?path=" + encodeURIComponent(path));
  if (r.error) return toast(r.error);
  const m = $("#modal");
  m.dataset.preview = "";
  $("#modal-title").textContent = path;
  $("#modal-actions").innerHTML = `${r.readOnly ? "" : '<button class="small primary" id="save-file">Save</button>'}
    <button class="small ghost" data-open="${esc(path)}">Open with Windows</button>`;
  $("#modal-body").innerHTML = `<textarea id="editor" spellcheck="false" ${r.readOnly ? "readonly" : ""}></textarea>`;
  $("#editor").value = r.text;
  m.classList.remove("hidden");
  const sv = $("#save-file");
  if (sv) sv.onclick = () => api("file-op", { op: "save", path, text: $("#editor").value });
}

// ---------- BROWSER ----------
let brSeq = 0, brLoop = false, brStarted = false;
async function startBrowser() {
  if (!brStarted) { brStarted = true; api("browser/start"); }
  if (brLoop) return;
  brLoop = true;
  while (tab === "browser") {
    try {
      const r = await fetch(`/api/browser/frame?since=${brSeq}`, { headers: { "X-LF-Token": LF_TOKEN } });
      const seq = +(r.headers.get("X-Frame-Seq") || brSeq);
      if (r.status === 200) {
        const url = URL.createObjectURL(await r.blob());
        const img = $("#br-img"), old = img.src;
        img.src = url; if (old.startsWith("blob:")) setTimeout(() => URL.revokeObjectURL(old), 1000);
        $("#br-empty").classList.add("hidden");
      } else await new Promise(res => setTimeout(res, 400));
      brSeq = seq;
    } catch (e) { await new Promise(res => setTimeout(res, 800)); }
  }
  brLoop = false;
}
let brSize = "";
function fitBrowser() {
  const v = $("#br-view"); if (!v || tab !== "browser") return;
  const w = Math.max(640, Math.min(1920, Math.floor(v.clientWidth))), h = Math.max(400, Math.min(1400, Math.floor(v.clientHeight)));
  const key = w + "x" + h;
  if (key !== brSize && v.clientWidth > 0) { brSize = key; api("browser/resize", { width: w, height: h }); }
}
window.addEventListener("resize", () => { clearTimeout(window._fitT); window._fitT = setTimeout(fitBrowser, 250); });
function renderBrowser() {
  fitBrowser();
  const doing = $("#br-doing");
  if (doing) { const show = S.generating && sideOpen; doing.classList.toggle("hidden", !show);
    if (show) doing.textContent = "🤖 " + (S.status && !/ready|loaded/i.test(S.status) ? S.status : "Thinking…"); }
  const b = S.browser;
  const urlBox = $("#br-url");
  if (document.activeElement !== urlBox && b.url && b.url !== "about:blank") urlBox.value = b.url;
  $("#br-title").textContent = b.title || "";
  $("#br-loading").classList.toggle("hidden", !b.loading);
  $("#br-auto").checked = !!S.settings.browserAutoApprove;
  const ban = $("#br-banner");
  const msg = !b.available ? "No Edge or Chrome was found. Install Microsoft Edge or Google Chrome to use the browser." : b.error;
  ban.classList.toggle("hidden", !msg); ban.textContent = msg || "";
  if (!b.running && brStarted && b.available && !b.error) { $("#br-empty").textContent = "Browser stopped. Type an address to start it again."; }
}
function brPoint(e) {
  const img = $("#br-img"), r = img.getBoundingClientRect(), b = S.browser;
  return { x: (e.clientX - r.left) * b.width / r.width, y: (e.clientY - r.top) * b.height / r.height };
}
$("#br-img").addEventListener("mousedown", e => e.preventDefault());
$("#br-img").addEventListener("click", e => { $("#br-view").focus(); const p = brPoint(e); api("browser/click", p); });
$("#br-img").addEventListener("dblclick", e => { const p = brPoint(e); api("browser/click", { ...p, count: 2 }); });
let wheelAcc = 0, wheelTimer;
$("#br-view").addEventListener("wheel", e => {
  e.preventDefault(); wheelAcc += e.deltaY; const p = brPoint(e);
  clearTimeout(wheelTimer); wheelTimer = setTimeout(() => { api("browser/scroll", { dy: wheelAcc, x: p.x, y: p.y }); wheelAcc = 0; }, 40);
}, { passive: false });
const BR_KEYS = { Enter: "enter", Tab: "tab", Escape: "escape", Backspace: "backspace", Delete: "delete", ArrowUp: "arrowup", ArrowDown: "arrowdown",
  ArrowLeft: "arrowleft", ArrowRight: "arrowright", PageUp: "pageup", PageDown: "pagedown", Home: "home", End: "end" };
let typeBuf = "", typeTimer;
$("#br-view").addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;      // let Ctrl+V reach the paste handler
  if (BR_KEYS[e.key]) { e.preventDefault(); flushType(); api("browser/key", { key: BR_KEYS[e.key] }); return; }
  if (e.key.length === 1) { e.preventDefault(); typeBuf += e.key; clearTimeout(typeTimer); typeTimer = setTimeout(flushType, 60); }
});
function flushType() { if (typeBuf) { api("browser/type", { text: typeBuf }); typeBuf = ""; } }
$("#br-view").addEventListener("paste", e => { const t = e.clipboardData.getData("text"); if (t) { e.preventDefault(); api("browser/type", { text: t }); } });
$("#br-form").onsubmit = e => { e.preventDefault(); const u = $("#br-url").value.trim(); if (u) { api("browser/navigate", { url: u }); $("#br-view").focus(); } };
$("#br-back").onclick = () => api("browser/back");
$("#br-fwd").onclick = () => api("browser/forward");
$("#br-reload").onclick = () => api("browser/reload");
$("#br-auto").onchange = e => api("settings", { browserAutoApprove: e.target.checked });
// ---------- AI side panel in the Browser tab (the same chat, docked next to the live page) ----------
let sideOpen = false;
function dockChat(on) {
  sideOpen = on;
  const target = on ? $("#br-side") : $("#tab-chat");
  ["#chat-head", "#messages", "#composer"].forEach(sel => target.appendChild($(sel)));
  $("#tab-browser").classList.toggle("side-open", on);
  brSize = ""; setTimeout(fitBrowser, 60);
  lastMsgVersion = -1; syncMessages().then(() => { const m = $("#messages"); m.scrollTop = m.scrollHeight; });
}
try { const w = localStorage.getItem("lf-side-w"); if (w) document.documentElement.style.setProperty("--side-w", w); } catch (e) {}
$("#br-ask").onclick = () => {
  if (tab !== "browser") setTab("browser");
  dockChat(true);
  const inp = $("#input");
  if (!inp.value.trim()) inp.value = "";
  inp.placeholder = "Ask about this page, or tell the AI what to do here (it clicks and types while you watch)";
  autosize(); inp.focus();
};
$("#side-close").onclick = () => dockChat(false);
$("#side-full").onclick = () => { dockChat(false); setTab("chat"); };
$("#br-split").onmousedown = e => {
  e.preventDefault();
  const move = ev => { const w = Math.max(300, Math.min(window.innerWidth * 0.7, window.innerWidth - ev.clientX)) + "px";
    document.documentElement.style.setProperty("--side-w", w); try { localStorage.setItem("lf-side-w", w); } catch (e) {} };
  const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); brSize = ""; fitBrowser(); };
  document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
};

document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#modal").classList.contains("hidden")) closeModal(); });
poll();
