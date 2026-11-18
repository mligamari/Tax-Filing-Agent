// The demo screen. Everything from the spreadsheet is put on the page as text (never as
// HTML), and every call goes to this computer's own demo server with its per-run key.
"use strict";

const KEY = document.querySelector('meta[name="demo-key"]').content;
const $ = (id) => document.getElementById(id);
const state = { info: null, proposal: null, options: null, choices: {}, original: {}, box12: [] };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function api(path, body, raw) {
  const opts = { method: body === undefined ? "GET" : "POST", headers: { "X-Demo-Key": KEY } };
  if (raw) { opts.body = body; opts.headers["Content-Type"] = "application/octet-stream"; }
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ error: `The demo server answered ${res.status}.` }));
  if (!res.ok) throw Object.assign(new Error(data.error || `The demo server answered ${res.status}.`), { audit: data.audit });
  return data;
}

function setStep(n) {
  document.querySelectorAll("#stepper li").forEach((li) => {
    const s = Number(li.dataset.step);
    li.classList.toggle("active", s === n);
    li.classList.toggle("done", s < n);
  });
  for (let i = 2; i <= 4; i++) $(`step${i}`).classList.toggle("hidden", i > Math.max(n, 1));
}

function status(id, text, isError) {
  const s = $(id);
  s.textContent = text || "";
  s.classList.toggle("err", Boolean(isError));
}

// --- step 1 -----------------------------------------------------------------------

async function init() {
  try {
    state.options = await api("/api/options");
  } catch (e) {
    $("loaded").textContent = e.message;
    $("loaded").classList.remove("hidden");
    return;
  }
  const box = $("samples");
  for (const s of state.options.samples) {
    box.append(el("button", { class: "sample", type: "button", "data-key": s.key, onclick: () => openSample(s.key) },
      el("strong", {}, s.title), el("span", {}, s.detail)));
  }
  for (const o of state.options.outputs) {
    $("outputs").append(el("label", { class: "output" },
      el("input", { type: "radio", name: "output", value: o.key, onchange: outputChanged }),
      el("span", {}, el("strong", {}, o.title, o.provisional ? el("span", { class: "tag" }, "Provisional") : null),
        el("small", {}, o.agency + (o.key === "ui"
          ? ". Built on SSA's generic format while Virginia's own spec is requested. Needs a Quarterly Wages sheet."
          : ". The yearly file of every employee's W-2, in SSA's EFW2 format with Virginia's state record.")))));
  }
  for (const y of state.options.years) $("year").append(el("option", { value: y }, y));

  const drop = $("drop");
  $("file").addEventListener("change", (e) => e.target.files[0] && openFile(e.target.files[0]));
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && openFile(e.dataTransfer.files[0]));
  $("propose").addEventListener("click", propose);
  $("build").addEventListener("click", build);
  $("show-optional").addEventListener("change", renderMapping);
  document.querySelectorAll('input[name="ai"]').forEach((r) => r.addEventListener("change", modeChanged));
}

function markSample(key) {
  document.querySelectorAll(".sample").forEach((b) => b.classList.toggle("chosen", b.dataset.key === key));
}

async function openSample(key) {
  markSample(key);
  await loaded(() => api("/api/sample", { sample: key }));
}

async function openFile(file) {
  markSample(null);
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    showLoaded("Please choose an Excel workbook (.xlsx).", true);
    return;
  }
  await loaded(async () => api(`/api/upload?name=${encodeURIComponent(file.name)}`, await file.arrayBuffer(), true));
}

function showLoaded(text, isError) {
  const p = $("loaded");
  p.textContent = text;
  p.className = "loaded" + (isError ? " callout bad" : "");
}

async function loaded(load) {
  showLoaded("Reading the workbook on this computer...");
  try {
    state.info = await load();
  } catch (e) {
    showLoaded(e.message, true);
    return;
  }
  const info = state.info;
  const cols = info.sheets.reduce((n, s) => n + s.columns.length, 0);
  showLoaded(`Loaded ${info.name}: ${info.sheets.length} sheet(s), ${cols} named columns.`);
  state.proposal = null;
  $("mapping").classList.add("hidden");
  $("step4").classList.add("hidden");
  status("propose-status", "");
  $("payload").textContent = info.payload.user;
  $("payload-summary").textContent =
    `What the AI would see: ${info.payload.characters.toLocaleString()} characters of headers and value shapes, ` +
    `no cell values (checked against ${info.payload.cells_checked.toLocaleString()} cells)`;
  const claude = info.claude;
  const claudeInput = document.querySelector('input[name="ai"][value="claude"]');
  claudeInput.disabled = !claude.available;
  $("claude-mode").classList.toggle("disabled", !claude.available);
  $("claude-note").textContent = claude.available
    ? "Sends only column headers and value shapes to Anthropic's API. You see the exact message first."
    : `Not available here: ${claude.reason} The offline rules work without it.`;
  if (!claude.available) document.querySelector('input[name="ai"][value="none"]').checked = true;
  $("year").value = info.default_year;
  modeChanged();
  setStep(2);
  $("step2").scrollIntoView({ behavior: "smooth", block: "start" });
}

// --- step 2 -----------------------------------------------------------------------

function aiMode() { return document.querySelector('input[name="ai"]:checked').value; }

function modeChanged() {
  const claude = aiMode() === "claude";
  $("propose").textContent = claude ? "Send to Claude and propose a mapping" : "Propose a mapping";
  $("payload-box").open = claude;
}

async function propose() {
  const ai = aiMode();
  $("propose").disabled = true;
  status("propose-status", ai === "claude" ? "Asking Claude (headers and shapes only)..." : "Matching headers...");
  try {
    state.proposal = await api("/api/propose", { session: state.info.session, ai });
  } catch (e) {
    status("propose-status", e.message, true);
    if (e.audit && e.audit.length) {
      renderAudit(e.audit);
      $("mapping").classList.remove("hidden");
      $("map-table").classList.add("hidden");
      ["missing", "box12", "notes"].forEach((id) => $(id).replaceChildren());
      $("missing").classList.add("hidden");
      state.proposal = null;
      refreshBuild();
    }
    return;
  } finally {
    $("propose").disabled = false;
  }
  const p = state.proposal;
  state.choices = {};
  for (const [field, c] of Object.entries(p.columns)) if (c.column) state.choices[field] = c.column;
  state.original = { ...state.choices };
  state.box12 = p.box12;
  status("propose-status", p.provider === "none" ? "Proposed by the offline rules." : "Proposed by Claude. Review it below.");
  $("map-table").classList.remove("hidden");
  renderMapping();
  renderAudit(p.audit);
  $("mapping").classList.remove("hidden");
  setStep(3);
  refreshBuild();
}

function sheetOf() {
  const id = state.proposal && state.proposal.sheet;
  return state.info.sheets.find((s) => s.id === id) || state.info.sheets[0];
}

function columnById(id) {
  for (const s of state.info.sheets) for (const c of s.columns) if (c.id === id) return c;
  return null;
}

function missingRequired() {
  return state.info.fields.filter((f) => f.required && !state.choices[f.key]).map((f) => f.key);
}

function renderMapping() {
  const p = state.proposal;
  const sheet = sheetOf();
  const body = $("map-body");
  body.replaceChildren();
  const showOptional = $("show-optional").checked;
  const used = new Set(Object.values(state.choices));
  for (const f of state.info.fields) {
    const chosen = state.choices[f.key] || "";
    if (!f.required && !chosen && !state.original[f.key] && !showOptional) continue;
    const proposed = p.columns[f.key];
    const select = el("select", { "aria-label": `Column for ${f.description}`, onchange: (e) => {
      if (e.target.value) state.choices[f.key] = e.target.value; else delete state.choices[f.key];
      renderMapping(); refreshBuild();
    } }, el("option", { value: "" }, f.required ? "Choose a column..." : "(not in this spreadsheet)"));
    for (const c of sheet.columns) {
      const label = `${c.letter}: ${c.header}` + (used.has(c.id) && c.id !== chosen ? "  (used)" : "");
      select.append(el("option", { value: c.id, selected: c.id === chosen }, label));
    }
    const col = columnById(chosen);
    let conf = null;
    if (chosen && chosen !== state.original[f.key]) conf = el("span", { class: "conf high" }, "you chose");
    else if (proposed && proposed.check && proposed.check !== "ok") conf = el("span", { class: "conf check", title: proposed.check }, "check this");
    else if (proposed) conf = el("span", { class: `conf ${proposed.confidence}`, title: proposed.reason || "" }, proposed.confidence);
    body.append(el("tr", { class: [!chosen && f.required ? "missing" : "", chosen && chosen !== state.original[f.key] ? "changed" : ""].join(" ") },
      el("td", { class: "f" }, f.key, f.required ? el("span", { class: "req", title: "required" }, "*") : null,
        el("small", {}, f.description)),
      el("td", {}, select),
      el("td", {}, el("span", { class: "shape" }, col ? col.shapes.slice(0, 2).join("  ") : "")),
      el("td", {}, conf)));
  }
  const missing = missingRequired();
  const m = $("missing");
  m.classList.toggle("hidden", missing.length === 0);
  m.textContent = missing.length
    ? `${missing.length} required field(s) need a column: ${missing.join(", ")}. The rules found more than one ` +
      "candidate or none, so they left the choice to you. Pick one in the highlighted rows."
    : "";
  const b = $("box12");
  b.replaceChildren();
  if (state.box12.length) {
    b.append(el("strong", {}, "Box 12 code and amount pairs: "),
      state.box12.map((pair, i) => {
        const c = columnById(pair.code), a = columnById(pair.amount);
        return `${i ? "; " : ""}${c ? c.header : "?"} + ${a ? a.header : "?"}`;
      }).join(""));
  }
  const notes = $("notes");
  notes.replaceChildren(...[...p.notes, ...p.warnings.map((w) => "Check: " + w), ...p.errors.map((e) => "Problem: " + e)]
    .map((n) => el("li", {}, n)));
}

function renderAudit(entries) {
  const box = $("audit");
  box.replaceChildren();
  const sent = entries.filter((e) => e.event === "request_sent");
  $("audit-summary").textContent = sent.length
    ? `Audit log: ${sent.length} request sent to ${new URL(sent[0].url).host}, exactly as below`
    : "Audit log: nothing left this computer";
  for (const e of entries) {
    if (e.event === "no_request") {
      box.append(el("p", { class: "muted" }, `${e.time}: no request. ${e.note}`));
    } else if (e.event === "request_sent") {
      box.append(el("p", {}, el("strong", {}, `${e.time}: POST ${e.url}`), ` (${e.body_bytes.toLocaleString()} bytes, sha256 ${e.body_sha256.slice(0, 16)}...)`),
        el("pre", {}, Object.entries(e.headers).map(([k, v]) => `${k}: ${v}`).join("\n")),
        el("pre", {}, prettyJson(e.body)));
    } else if (e.event === "response_received") {
      box.append(el("p", {}, el("strong", {}, `${e.time}: answer received`)), el("pre", {}, prettyJson(e.text)));
    } else if (e.event === "request_blocked") {
      box.append(el("p", { class: "callout bad" }, `${e.time}: request blocked before sending: ${e.reason}`));
    }
  }
}

function prettyJson(text) {
  try { return JSON.stringify(JSON.parse(text), null, 1); } catch { return text; }
}

// --- step 3 -----------------------------------------------------------------------

function outputChanged() {
  const out = document.querySelector('input[name="output"]:checked');
  $("quarter-label").classList.toggle("hidden", !out || out.value !== "ui");
  refreshBuild();
}

function refreshBuild() {
  const out = document.querySelector('input[name="output"]:checked');
  const missing = state.proposal ? missingRequired() : ["mapping"];
  $("build").disabled = !out || missing.length > 0;
  status("build-status", !state.proposal ? "" : missing.length ? "Finish the column mapping first."
    : !out ? "Choose a file type." : "");
}

async function build() {
  const out = document.querySelector('input[name="output"]:checked').value;
  $("build").disabled = true;
  status("build-status", "Reading every row, writing the file, then re-reading it to check...");
  let r;
  try {
    r = await api("/api/build", {
      session: state.info.session, output: out, year: Number($("year").value), quarter: Number($("quarter").value),
      sheet: sheetOf().id, columns: state.choices, box12: state.box12,
    });
  } catch (e) {
    status("build-status", e.message, true);
    $("build").disabled = false;
    return;
  }
  $("build").disabled = false;
  status("build-status", "");
  renderResult(r);
  setStep(4);
  $("step4").scrollIntoView({ behavior: "smooth", block: "start" });
}

// --- step 4 -----------------------------------------------------------------------

const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10"/></svg>';
const CROSS = '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
const TOTALS = { total_box1_wages: "Box 1 wages", total_box3_ss_wages: "Social Security wages", state_wages: "Virginia wages",
  state_tax: "Virginia tax withheld", ui_total_wages: "UI total wages", ui_taxable_wages: "UI taxable wages" };

function issueList(title, items, max = 12) {
  if (!items.length) return null;
  return el("details", { class: "sent" }, el("summary", {}, `${title} (${items.length})`),
    el("ul", { class: "issues" }, items.slice(0, max).map((i) => el("li", {}, i)),
      items.length > max ? el("li", { class: "muted" }, `...and ${items.length - max} more`) : null));
}

function renderResult(r) {
  const box = $("result");
  box.replaceChildren();
  const ok = r.check && r.check.ok && r.download;
  const verdict = el("div", { class: `verdict ${ok ? "ok" : "bad"}` }, el("div", { class: "mark" }),
    el("div", {}, el("strong", {}, ok ? "Round-trip check passed" : "No file made"),
      el("span", {}, ok
        ? `${r.file_name} was written, then read back field by field and compared with the spreadsheet: ` +
          `${r.check.employees_compared} employees, ${r.check.fields_compared.toLocaleString()} fields, no differences.`
        : r.check ? "The file was written but did not match the spreadsheet when read back, so it is not offered."
          : "The spreadsheet has problems that stop the file from being written. They are listed below.")));
  verdict.querySelector(".mark").innerHTML = ok ? CHECK : CROSS;   // fixed icons, no data
  box.append(verdict);

  if (r.provisional) {
    box.append(el("div", { class: "callout warn" }, "Provisional format: built on SSA's generic state record, not the " +
      "Virginia Employment Commission's own specification, which has been requested. Do not upload this file."));
  }
  if (r.check) {
    const stats = el("div", { class: "stats" },
      el("div", { class: "stat" }, el("b", {}, r.employees), el("span", {}, "employees")),
      el("div", { class: "stat" }, el("b", {}, Object.values(r.check.records).reduce((a, b) => a + b, 0)),
        el("span", {}, "records: " + Object.entries(r.check.records).map(([k, v]) => `${k} ${v}`).join(", "))),
      Object.entries(TOTALS).filter(([k]) => k in r.check.totals).slice(0, 3).map(([k, label]) =>
        el("div", { class: "stat" }, el("b", {}, "$" + r.check.totals[k]), el("span", {}, label))));
    box.append(stats);
  }
  if (r.employed_12th) {
    box.append(el("p", { class: "muted" }, `Employees on the payroll on the 12th of each month in Q${r.quarter}: ` +
      `${r.employed_12th.join(", ")}.`));
  }
  if (ok) {
    box.append(el("div", { class: "actions" },
      el("a", { class: "primary", href: `/api/download?session=${state.info.session}&file=${r.download}&key=${encodeURIComponent(KEY)}`,
        download: r.file_name }, `Download ${r.file_name}`),
      el("span", { class: "status" }, `${r.bytes.toLocaleString()} bytes, ${r.year} tax year. The file holds full SSNs, as the state requires.`)));
    box.append(el("h3", {}, "A look inside the file (SSNs masked here)"),
      el("p", { class: "muted" }, r.output === "ui"
        ? "Each line is one fixed-width record. RA is the submitter, RE the employer, each RS one employee's wages for the quarter, RF the end of the file."
        : "Each line is one fixed-width record. RA is the submitter, RE the employer, RW and RS one employee's federal and state wages, RT and RF the totals."),
      el("pre", {}, r.preview.map((p) => p.text).join("\n")));
  }
  const errors = [...r.input_errors, ...(r.check ? r.check.errors : [])];
  if (errors.length) box.append(el("h3", {}, "Problems"), el("ul", { class: "issues" }, errors.slice(0, 20).map((e) => el("li", {}, e))));
  const warnings = [...r.input_warnings.filter((w) => !w.includes("PROVISIONAL")), ...(r.check ? r.check.warnings : [])];
  const w = issueList("Notes from the writer and checker", warnings);
  if (w) box.append(w);
}

init();
