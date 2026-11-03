"use strict";
// RAJ Finance Agent portal — dependency-free.
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const S = { token: null, me: null, clients: [], client: null, tab: "ask", job: null, preview: null, file: null };
try { S.token = sessionStorage.getItem("raj_token"); } catch (e) { /* storage unavailable */ }

async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (S.token) headers.Authorization = "Bearer " + S.token;
  if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const r = await fetch(path, { method: opts.method || (opts.body ? "POST" : "GET"), headers, body: opts.body });
  if (r.status === 401 && path !== "/api/login") { logout(); throw new Error("Session expired"); }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(typeof data === "object" ? (data.detail?.toString?.() || JSON.stringify(data)) : data);
  return data;
}
function logout() { S.token = null; try { sessionStorage.removeItem("raj_token"); } catch (e) {} render(); }
function flash(msg, bad = true) { const el = $("#flash"); if (el) el.innerHTML = msg ? `<div class="${bad ? "err" : "card"}">${esc(msg)}</div>` : ""; }
async function guard(fn) { flash(""); try { await fn(); } catch (e) { flash(e.message); } }

// ------------------------------------------------------------------ shell
async function render() {
  const app = $("#app");
  if (!S.token) {
    app.innerHTML = `<div id="login" class="card"><h2>RAJ Finance Agent</h2>
      <p class="muted">Firm portal · પેઢી પોર્ટલ</p><div id="flash"></div>
      <form id="lf"><label>Email<input name="email" type="email" required autocomplete="username"></label>
      <label>Password<input name="password" type="password" required autocomplete="current-password"></label>
      <div><button class="p">Sign in</button></div></form></div>`;
    $("#lf").onsubmit = (ev) => { ev.preventDefault(); const f = new FormData(ev.target);
      guard(async () => { const r = await api("/api/login", { json: { email: f.get("email"), password: f.get("password") } });
        S.token = r.token; try { sessionStorage.setItem("raj_token", r.token); } catch (e) {} await boot(); }); };
    return;
  }
  const c = S.client, d = c?.device;
  const tabs = [["ask", "Ask the Books"], ["reconcile", "Bank Reconciliation"], ["jobs", "Jobs"], ["device", "Device Health"]];
  if (S.me.user.role === "admin") tabs.push(["admin", "Settings"]);
  app.innerHTML = `<header><h1>RAJ</h1>
    <select id="client">${S.clients.map((x) => `<option value="${x.id}" ${x.id === c?.id ? "selected" : ""}>${esc(x.name)}</option>`).join("") || "<option>No clients</option>"}</select>
    <span class="ctx">${c ? `Client <b>${esc(c.name)}</b> · Company <b>${esc(d?.company_name || "not paired")}</b>${d?.books_from ? ` · Books from ${esc(d.books_from)}` : ""}` : ""}</span>
    <span class="spacer"></span><span class="ctx">${esc(S.me.user.name)} (${esc(S.me.user.role)}) · model: ${esc(S.me.model)}</span>
    <button class="s" id="lo">Sign out</button></header>
    <main><nav>${tabs.map(([k, l]) => `<button data-t="${k}" class="${S.tab === k ? "on" : ""}">${l}</button>`).join("")}</nav>
    <div id="flash"></div><div id="view"></div></main>`;
  $("#lo").onclick = () => guard(async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); logout(); });
  $("#client").onchange = (e) => { S.client = S.clients.find((x) => x.id === e.target.value); S.job = null; S.preview = null; render(); };
  app.querySelectorAll("nav button").forEach((b) => (b.onclick = () => { S.tab = b.dataset.t; if (S.tab !== "jobs") S.job = S.tab === "reconcile" ? S.job : null; render(); }));
  const v = $("#view");
  if (!c && S.tab !== "admin") { v.innerHTML = `<div class="card">No clients assigned yet.${S.me.user.role === "admin" ? " Create one in Settings." : ""}</div>`; return; }
  ({ ask: viewAsk, reconcile: viewReconcile, jobs: viewJobs, device: viewDevice, admin: viewAdmin })[S.tab](v);
}

async function boot() {
  S.me = await api("/api/me");
  S.clients = await api("/api/clients");
  S.client = S.clients.find((x) => x.id === S.client?.id) || S.clients[0] || null;
  render();
}

// ------------------------------------------------------------------ reports
function table(cols, rows) {
  const num = (k) => /amount|debit|credit|total|balance|closing|days|net_|bills|withdrawal|deposit/.test(k);
  return `<div class="tbl"><table><thead><tr>${cols.map((k) => `<th>${esc(k.replace(/_/g, " "))}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${cols.map((k) => `<td class="${num(k) ? "n" : ""}">${esc(r[k])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function report(rep) {
  if (rep.error) return `<div class="err">${esc(rep.error)} ${rep.candidates ? "— " + esc(rep.candidates.join(", ")) : ""}</div>`;
  const m = rep.meta || {};
  return `<div class="card"><h2>${esc(rep.title)}</h2>
    <div class="muted">${esc(m.company)} · ${esc(m.period)} · extracted ${esc(m.extracted_at)} · ${esc(m.source)}
      ${m.tie_out ? ` · tie-out: <b class="${m.tie_out === "balanced" ? "good" : "bad"}">${esc(m.tie_out)}</b>` : ""}</div>
    <div class="totals">${Object.entries(rep.totals || {}).map(([k, v]) => `<div><span class="muted">${esc(k)}</span> <b>₹${esc(v)}</b></div>`).join("")}</div>
    ${(rep.sections || []).map((s) => `<h3>${esc(s.title)} <span class="muted">(${s.rows.length})</span></h3>${table(s.columns, s.rows)}`).join("")}</div>`;
}
function stateBadge(s) {
  const cls = { COMPLETED: "good", FAILED: "bad", REJECTED: "bad", RESULT_UNKNOWN: "bad", WAITING_FOR_APPROVAL: "warn", PARTIALLY_COMPLETED: "warn", WAITING_FOR_DEVICE: "warn" }[s] || "";
  return `<span class="badge ${cls}">${esc(s)}</span>`;
}
function events(job) {
  return `<details><summary>Audit trail (${job.events.length} events)</summary><div class="tbl"><table><tbody>${job.events.map((e) =>
    `<tr><td class="muted">${new Date(e.ts * 1000).toLocaleString()}</td><td>${esc(e.actor)}</td><td>${esc(e.event)}</td><td><code>${esc(JSON.stringify(e.detail)).slice(0, 300)}</code></td></tr>`).join("")}</tbody></table></div></details>`;
}

// ------------------------------------------------------------------ ask
function viewAsk(v) {
  const ex = ["Show receivables older than 90 days", "Trial balance", "Ledger of HDFC Bank", "Payables ageing",
    "What is our GST position?", "Profit and loss this year", "Balance sheet", "Run audit checks (40A(3), 269ST)", "90 દિવસથી વધુ બાકી લેણાં બતાવો"];
  v.innerHTML = `<div class="card"><h2>Ask the books</h2>
    <div class="chips">${ex.map((q) => `<button>${esc(q)}</button>`).join("")}</div>
    <textarea id="q" placeholder="e.g. Show receivables older than 90 days"></textarea>
    <div class="row"><label>Report date<input id="asof" type="date" value="${new Date().toISOString().slice(0, 10)}"></label>
    <button class="p" id="go">Ask</button></div></div><div id="out"></div>`;
  v.querySelectorAll(".chips button").forEach((b) => (b.onclick = () => ($("#q").value = b.textContent)));
  $("#go").onclick = () => guard(async () => {
    const q = $("#q").value.trim(); if (!q) return;
    $("#go").disabled = true; $("#out").innerHTML = `<div class="card muted">Reading Tally through the client connector…</div>`;
    try { const j = await api(`/api/clients/${S.client.id}/ask`, { json: { question: q, as_of: $("#asof").value } }); showReadJob(j, $("#out")); }
    finally { $("#go").disabled = false; }
  });
}
function showReadJob(j, el) {
  el.innerHTML = `<div class="card">${stateBadge(j.state)} ${j.result?.answer ? `<p class="answer">${esc(j.result.answer)}</p>` : ""}
    ${j.result?.error ? `<div class="err">${esc(j.result.error)}</div>` : ""}${events(j)}</div>${j.reports.map(report).join("")}`;
}

// ------------------------------------------------------------------ reconcile
const FIELDS = ["date", "narration", "reference", "debit", "credit", "amount", "balance"];
const DATE_FORMATS = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d/%m/%y", "%d.%m.%Y", "%m/%d/%Y"];
function viewReconcile(v) {
  if (S.job && S.job.type === "reconcile") return showDraftJob(S.job, v);
  const p = S.preview;
  v.innerHTML = `<div class="card"><h2>1. Upload bank statement (CSV / XLSX)</h2>
    <div class="row"><input type="file" id="f" accept=".csv,.xlsx"><label>Bank ledger in Tally<input id="bl" value="${esc(p?.bank_ledger || "")}" placeholder="e.g. HDFC Bank"></label>
    <button class="s" id="pv">Preview</button></div></div><div id="pvout"></div>`;
  $("#pv").onclick = () => guard(async () => {
    const f = $("#f").files[0] || S.file; if (!f) throw new Error("Choose a file");
    S.file = f; const fd = new FormData(); fd.append("file", f);
    S.preview = { ...(await api(`/api/clients/${S.client.id}/statement/preview`, { method: "POST", body: fd })), bank_ledger: $("#bl").value };
    showPreview();
  });
  if (p) showPreview();
}
function showPreview() {
  const p = S.preview, el = $("#pvout");
  const opts = (sel) => `<option value="">—</option>` + p.headers.map((h, i) => `<option value="${i}" ${sel === i ? "selected" : ""}>${esc(h)}</option>`).join("");
  el.innerHTML = `<div class="card"><h2>2. Confirm how to read the file</h2>
    ${p.errors.map((e) => `<div class="err">${esc(e)}</div>`).join("")}
    ${p.needs_confirmation.map((e) => `<div class="warn">• ${esc(e)}</div>`).join("")}
    ${p.warnings.map((e) => `<div class="muted">• ${esc(e)}</div>`).join("")}
    <div class="row">${FIELDS.map((k) => `<label>${k}<select data-k="${k}">${opts(p.mapping[k])}</select></label>`).join("")}
    <label>Date format<select id="df">${DATE_FORMATS.map((f) => `<option ${f === p.date_format ? "selected" : ""}>${f}</option>`).join("")}</select></label></div>
    <p class="muted">${p.row_count} rows · opening ₹${esc(p.opening_balance ?? "n/a")} · closing ₹${esc(p.closing_balance ?? "n/a")}</p>
    ${table(["row_no", "date", "narration", "reference", "withdrawal", "deposit", "balance"], p.rows.slice(0, 15))}
    <div class="row" style="margin-top:10px"><button class="p" id="rc" ${p.errors.length ? "disabled" : ""}>Confirm and reconcile with Tally</button>
    <button class="s" id="rp">Re-check with these settings</button></div></div>`;
  const mapping = () => { const m = {}; el.querySelectorAll("select[data-k]").forEach((s) => { if (s.value !== "") m[s.dataset.k] = +s.value; }); return m; };
  $("#rp").onclick = () => guard(async () => {
    const fd = new FormData(); fd.append("file", S.file); fd.append("mapping", JSON.stringify(mapping())); fd.append("date_format", $("#df").value);
    S.preview = { ...(await api(`/api/clients/${S.client.id}/statement/preview`, { method: "POST", body: fd })), bank_ledger: S.preview.bank_ledger };
    showPreview();
  });
  $("#rc").onclick = () => guard(async () => {
    const bl = $("#bl").value.trim(); if (!bl) throw new Error("Enter the bank ledger name exactly as in Tally");
    const fd = new FormData(); fd.append("file", S.file); fd.append("bank_ledger", bl); fd.append("mapping", JSON.stringify(mapping())); fd.append("date_format", $("#df").value);
    $("#rc").disabled = true;
    S.job = await api(`/api/clients/${S.client.id}/reconcile`, { method: "POST", body: fd }); S.preview = null; render();
  });
}
function showDraftJob(j, v) {
  const d = j.draft;
  const role = S.me.user.role, canApprove = role === "admin" || role === "reviewer";
  if (!d) { v.innerHTML = `<div class="card">${stateBadge(j.state)} ${j.result?.error ? `<div class="err">${esc(j.result.error)}</div>` : ""}<button class="s" id="nw">New reconciliation</button>${events(j)}</div>`; $("#nw").onclick = () => { S.job = null; render(); }; return; }
  const s = d.summary, ledgers = s.ledgers || [];
  const ms = { matched: "good", unmatched: "warn", ambiguous: "bad", duplicate_source: "bad" };
  const ps = { posted: "good", approved: "warn", unknown: "bad", failed: "bad", rejected: "bad", not_posted: "warn" };
  v.innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><h2>Reconciliation · ${esc(d.bank_ledger)} · ${esc(s.period.join(" to "))}</h2>
    <div>${stateBadge(j.state)} <span class="badge">draft v${d.version}</span></div></div>
    <div class="totals">${Object.entries(s.counts).map(([k, n]) => `<div><span class="${ms[k] || ""}">${esc(k)}</span> <b>${n}</b></div>`).join("")}
      <div><span class="muted">Statement opening</span> <b>₹${esc(s.statement_opening)}</b></div>
      <div><span class="muted">Tally balance before period</span> <b>₹${esc(s.tally_balance_before_period)}</b></div>
      <div><span class="muted">Opening difference</span> <b class="${s.opening_difference === "0.00" ? "good" : "bad"}">₹${esc(s.opening_difference)}</b></div></div>
    ${s.tally_only.length ? `<details><summary class="warn">${s.tally_only.length} Tally entries not in statement</summary>${table(["date", "number", "amount", "narration"], s.tally_only)}</details>` : ""}
    <div class="tbl"><table><thead><tr><th></th><th>#</th><th>Date</th><th>Narration</th><th>Withdrawal</th><th>Deposit</th><th>Match</th><th>Proposed ledger</th><th>Voucher</th><th>Status</th></tr></thead><tbody>
    ${d.rows.map((r) => { const src = r.source, vch = r.voucher, editable = r.match_status === "unmatched" && !["posted", "unknown", "posting"].includes(r.post_status);
      return `<tr><td>${vch && editable && !(r.errors || []).length ? `<input type="checkbox" data-row="${r.id}" ${r.post_status === "approved" ? "checked disabled" : ""}>` : ""}</td>
      <td>${src.row_no}</td><td>${esc(src.date)}</td><td>${esc(src.narration)}<div class="muted">${esc(src.reference)}</div></td>
      <td class="n">${src.withdrawal !== "0.00" ? esc(src.withdrawal) : ""}</td><td class="n">${src.deposit !== "0.00" ? esc(src.deposit) : ""}</td>
      <td><span class="${ms[r.match_status] || ""}">${esc(r.match_status)}</span><div class="muted">${esc(src.evidence)}</div></td>
      <td>${editable ? `<select data-led="${r.id}"><option value="">— choose —</option>${ledgers.filter((l) => l !== d.bank_ledger).map((l) => `<option ${vch && vch.entries.some((e) => e.ledger === l) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>` : vch ? esc(vch.entries.map((e) => e.ledger).filter((l) => l !== d.bank_ledger).join(", ")) : ""}
        ${r.suggestion ? `<div class="muted">${esc(r.suggestion.confidence)}: ${esc(r.suggestion.evidence)}</div>` : ""}</td>
      <td>${vch ? `${esc(vch.voucher_type)}<div class="muted">${vch.entries.map((e) => `${esc(e.side.toUpperCase())} ${esc(e.ledger)} ${esc(e.amount)}`).join("<br>")}</div>` : ""}
        ${(r.errors || []).map((e) => `<div class="bad">${esc(e)}</div>`).join("")}</td>
      <td><span class="${ps[r.post_status] || "muted"}">${esc(r.post_status)}</span>${r.tally_id ? `<div class="muted">Tally ${esc(r.tally_id)}</div>` : ""}
        ${r.readback?.detail ? `<div class="muted">${esc(typeof r.readback.detail === "string" ? r.readback.detail : JSON.stringify(r.readback.detail))}</div>` : ""}</td></tr>`; }).join("")}
    </tbody></table></div>
    <div class="row" style="margin-top:10px">
      ${canApprove ? `<button class="p" id="ap">Approve selected (exact vouchers, v${d.version})</button><button class="p" id="po">Post approved to Tally</button><button class="s" id="rs">Reconcile unknown results</button>` : `<span class="muted">A reviewer must approve and post.</span>`}
      <a class="s" href="#" id="ev">Download evidence</a><button class="s" id="nw">New reconciliation</button></div>
    <p class="muted">Approval signs the exact voucher (amount, ledgers, date, company). Any edit creates a new draft version and voids earlier approvals. Posting is verified by reading the voucher back from Tally.</p>
    ${events(j)}</div>`;
  const refresh = (nj) => { S.job = nj; render(); };
  v.querySelectorAll("select[data-led]").forEach((sel) => (sel.onchange = () => guard(async () => refresh(await api(`/api/drafts/${d.id}/rows/${sel.dataset.led}`, { method: "PATCH", json: { ledger: sel.value } })))));
  $("#nw").onclick = () => { S.job = null; render(); };
  $("#ev").onclick = (e) => { e.preventDefault(); guard(async () => { const data = await api(`/api/jobs/${j.id}/evidence`);
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })); a.download = `raj-evidence-${j.id}.json`; a.click(); }); };
  if (!canApprove) return;
  $("#ap").onclick = () => guard(async () => {
    const ids = [...v.querySelectorAll("input[data-row]:checked:not(:disabled)")].map((x) => x.dataset.row);
    if (!ids.length) throw new Error("Select rows to approve");
    refresh(await api(`/api/drafts/${d.id}/approve`, { json: { version: d.version, row_ids: ids } }));
  });
  $("#po").onclick = () => guard(async () => { if (!confirm("Post approved vouchers to Tally now?")) return; $("#po").disabled = true; refresh(await api(`/api/jobs/${j.id}/post`, { method: "POST" })); });
  $("#rs").onclick = () => guard(async () => refresh(await api(`/api/jobs/${j.id}/resolve`, { method: "POST" })));
}

// ------------------------------------------------------------------ jobs
async function viewJobs(v) {
  if (S.job) {
    v.innerHTML = `<button class="s" id="bk">← All jobs</button><div id="jv" style="margin-top:8px"></div>`;
    $("#bk").onclick = () => { S.job = null; render(); };
    S.job.type === "reconcile" ? showDraftJob(S.job, $("#jv")) : showReadJob(S.job, $("#jv"));
    return;
  }
  const jobs = await api(`/api/clients/${S.client.id}/jobs`).catch((e) => (flash(e.message), []));
  v.innerHTML = `<div class="card"><h2>Jobs</h2><div class="tbl"><table><thead><tr><th>When</th><th>Type</th><th>Request</th><th>State</th></tr></thead><tbody>
    ${jobs.map((j) => { const rq = JSON.parse(j.request); return `<tr data-j="${j.id}" style="cursor:pointer"><td>${new Date(j.created_at * 1000).toLocaleString()}</td><td>${esc(j.type)}</td>
      <td>${esc(rq.question || rq.filename || "")}</td><td>${stateBadge(j.state)}</td></tr>`; }).join("") || `<tr><td colspan="4" class="muted">No jobs yet</td></tr>`}
    </tbody></table></div></div>`;
  v.querySelectorAll("tr[data-j]").forEach((tr) => (tr.onclick = () => guard(async () => { S.job = await api(`/api/jobs/${tr.dataset.j}`); render(); })));
}

// ------------------------------------------------------------------ device
async function viewDevice(v) {
  const d = S.client.device, admin = S.me.user.role === "admin";
  v.innerHTML = `<div class="card"><h2>Connector</h2>${d ? `<p>Device <code>${esc(d.id)}</code> · company <b>${esc(d.company_name)}</b></p>
    <div class="status" id="st"><div class="muted">Checking…</div></div>
    <div class="row" style="margin-top:10px"><button class="s" id="ck">Check again</button>
    ${admin ? `<button class="s" id="wr">${d.write_enabled ? "Disable" : "Enable"} posting (server)</button><button class="s bad" id="rv">Revoke connector</button>` : ""}</div>`
    : `<p>No connector paired for this client.</p>`}</div>
    ${admin ? `<div class="card"><h2>Pair a client PC</h2><p class="muted">Creates a single-use code valid for 24 hours. On the client's Tally PC, with the company open in Tally, run:</p>
      <pre>raj-connector pair --server ${esc(location.origin)} --code &lt;CODE&gt;\nraj-connector run</pre><button class="p" id="ec">Create pairing code</button><div id="code"></div></div>` : ""}`;
  const check = () => guard(async () => {
    const h = await api(`/api/devices/${d.id}/health`);
    const ok = (b, t) => `<span class="${b ? "good" : "bad"}">${esc(t)}</span>`;
    $("#st").innerHTML = `<div><b>PC online</b>${ok(h.pc_online, h.pc_online ? "yes" : "no")}</div>
      <div><b>SSH tunnel</b>${ok(h.ssh_connected, h.ssh_connected ? "connected" : "down")}</div>
      <div><b>Gateway / MCP</b>${ok(h.gateway === "ok", h.gateway)}</div>
      <div><b>Tally</b>${ok(h.tally === "running", h.tally)}</div>
      <div><b>Company</b>${ok(h.company === "ready", h.company)}</div>
      <div><b>Posting</b>${ok(h.write_enabled_server && h.write_enabled_local, `server ${h.write_enabled_server ? "on" : "off"} · local ${h.write_enabled_local ? "on" : "off"}`)}</div>
      ${h.unresolved_writes ? `<div><b>Unresolved writes</b><span class="bad">${h.unresolved_writes}</span></div>` : ""}
      ${h.paused ? `<div><b>Paused</b><span class="warn">by local admin</span></div>` : ""}
      ${h.error ? `<div><b>Error</b><span class="bad">${esc(h.error)}</span></div>` : ""}`;
  });
  if (d) { check(); $("#ck").onclick = check; }
  if (admin && d) {
    $("#wr").onclick = () => guard(async () => { await api(`/api/devices/${d.id}/writes`, { json: { enabled: !d.write_enabled } }); await boot(); });
    $("#rv").onclick = () => guard(async () => { if (!confirm("Revoke this connector? It will stop working immediately.")) return; await api(`/api/devices/${d.id}/revoke`, { method: "POST" }); await boot(); });
  }
  if (admin) $("#ec").onclick = () => guard(async () => { const r = await api(`/api/clients/${S.client.id}/enrollment`, { method: "POST" }); $("#code").innerHTML = `<pre>${esc(r.code)}</pre>`; });
}

// ------------------------------------------------------------------ admin
async function viewAdmin(v) {
  const users = await api("/api/users");
  const st = S.me.org.settings;
  v.innerHTML = `<div class="card"><h2>Clients</h2><div class="row"><label>New client name<input id="cn"></label><button class="p" id="cc">Create client</button></div></div>
    <div class="card"><h2>Staff</h2>${table(["email", "name", "role"], users)}
      <h3>Add staff</h3><div class="row"><label>Email<input id="ue" type="email"></label><label>Name<input id="un"></label>
      <label>Temporary password (10+ chars)<input id="up" type="password"></label>
      <label>Role<select id="ur"><option>preparer</option><option>reviewer</option><option>viewer</option><option>admin</option></select></label>
      <label>Clients<select id="uc" multiple size="3">${S.clients.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select></label>
      <button class="p" id="ua">Add</button></div></div>
    <div class="card"><h2>Approval policy</h2><div class="row"><label><span><input type="checkbox" id="mc" ${st.maker_checker ? "checked" : ""}> Maker-checker (preparer cannot approve own drafts)</span></label>
      <label>High-value threshold (₹, admin approval above)<input id="hv" value="${esc(st.high_value_threshold || "")}" placeholder="none"></label><button class="p" id="sv">Save</button></div></div>`;
  $("#cc").onclick = () => guard(async () => { const r = await api("/api/clients", { json: { name: $("#cn").value } }); await boot(); S.client = S.clients.find((c) => c.id === r.id); S.tab = "device"; render(); });
  $("#ua").onclick = () => guard(async () => { await api("/api/users", { json: { email: $("#ue").value, name: $("#un").value, password: $("#up").value, role: $("#ur").value, client_ids: [...$("#uc").selectedOptions].map((o) => o.value) } }); render(); });
  $("#sv").onclick = () => guard(async () => { await api("/api/org/settings", { method: "PATCH", json: { maker_checker: $("#mc").checked, high_value_threshold: $("#hv").value || null } }); await boot(); flash("Saved", false); });
}

(S.token ? boot().catch(() => logout()) : render());
