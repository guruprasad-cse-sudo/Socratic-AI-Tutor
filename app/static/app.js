const $ = (s) => document.querySelector(s);
let state = null;
let questionShownAt = Date.now();
let pastedChars = 0;
let docDirty = false;

const STAGE_NAMES = {};

function esc(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Tiny, safe markdown: escape first, then **bold**, `code`, line breaks.
function md(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
}

// Projects this browser started: [{id, token, name, brief, stage_index, completed}].
// The token is the only way to open a project, so students see just their own work.
function myProjects() {
  try { return JSON.parse(localStorageGet("myProjects") || "[]"); } catch { return []; }
}
function rememberProject(s, token) {
  const list = myProjects();
  const old = list.find((p) => p.id === s.id);
  const entry = { id: s.id, token: token || old?.token, name: s.student_name, brief: (s.brief || "").slice(0, 120),
    stage_index: (s.stages || []).filter((st) => st.status === "done").length, completed: s.completed };
  localStorageSet("myProjects", JSON.stringify([entry, ...list.filter((p) => p.id !== s.id)]));
}
function tokenFor(id) {
  return myProjects().find((p) => String(p.id) === String(id))?.token || "";
}

async function api(path, opts = {}) {
  const m = path.match(/^\/api\/projects\/(\d+)/);
  const headers = { "Content-Type": "application/json" };
  if (m) headers["X-Project-Token"] = tokenFor(m[1]);
  const res = await fetch(path, { ...opts, headers });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : res.statusText);
  return body;
}

// ---------------------------------------------------------------- start screen

async function showStart() {
  // Back to the start screen: start a new project or pick another one.
  localStorageSet("projectId", "");
  state = null;
  $("#workspace").hidden = true;
  $("#start").hidden = false;
  $("#projects-btn").hidden = true;
  $("#current-project").hidden = true;
  $("#start-form").reset();
  await loadProjectList();
  $("#student-name").focus();
}

async function loadProjectList() {
  const projects = myProjects().filter((p) => p.token);
  $("#resume").hidden = !projects.length;
  $("#project-list").innerHTML = projects
    .map((p) => `<li><button data-id="${p.id}"><strong>${esc(p.name || "")}</strong>
      <span class="muted small"> · ${p.completed ? "design complete" : "stage " + (p.stage_index + 1) + " of 6"}</span><br>
      <span class="small">${esc(p.brief)}</span></button></li>`)
    .join("");
  $("#project-list").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => openProject(b.dataset.id)));
  return projects;
}

$("#projects-btn").addEventListener("click", showStart);

async function initStart() {
  try {
    const h = await api("/api/health");
    const p = $("#provider");
    p.textContent = h.llm_provider === "mock" ? "mock mode (no API key)" : "Claude API";
    p.classList.toggle("mock", h.llm_provider === "mock");
    p.title = "API key: " + h.api_key;
    if (h.key_problem) {
      p.textContent = "API key problem";
      p.classList.add("mock");
      p.title = h.key_problem;
      alert("API key problem: " + h.key_problem);
    }
  } catch {}
  const projects = await loadProjectList();
  const wantsNew = new URLSearchParams(location.search).has("new");
  const saved = localStorageGet("projectId");
  if (!wantsNew && saved && projects.some((p) => String(p.id) === saved)) openProject(saved);
}

$("#start-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#start-btn");
  btn.disabled = true;
  btn.textContent = "Preparing your first question…";
  try {
    const s = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ student_name: $("#student-name").value, brief: $("#brief").value }),
    });
    rememberProject(s, s.access_token);
    showWorkspace(s);
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Start designing";
  }
});

async function openProject(id) {
  try {
    showWorkspace(await api(`/api/projects/${id}`));
  } catch (err) {
    alert(err.message);
  }
}

function showWorkspace(s) {
  localStorageSet("projectId", String(s.id));
  rememberProject(s);
  $("#start").hidden = true;
  $("#workspace").hidden = false;
  $("#projects-btn").hidden = false;
  $("#current-project").hidden = false;
  $("#current-project").textContent = `${s.student_name}: ${s.brief.slice(0, 40)}${s.brief.length > 40 ? "…" : ""}`;
  $("#current-project").title = s.brief;
  render(s);
  $("#answer").focus();
}

// ---------------------------------------------------------------- rendering

function render(s) {
  state = s;
  s.stages.forEach((st) => (STAGE_NAMES[st.id] = st.name));
  renderMessages();
  renderRoadmap();
  renderDocs();
  renderUnlocked();
  renderLadder();
  const done = s.completed && s.transcript.at(-1)?.role === "student";
  $("#answer").disabled = done;
  $("#send-btn").disabled = done;
  if (done) $("#answer").placeholder = "Design phase complete. Show your instructor your documents!";
  questionShownAt = Date.now();
  pastedChars = 0;
}

function tagFor(item) {
  if (item.role === "tutor") {
    const m = item.mode || "";
    if (m.startsWith("HINT_")) return `<span class="tag hint">hint ${m.slice(5)}</span>`;
    if (m === "EXPLAIN") return `<span class="tag hint">explanation</span>`;
    if (m === "RELEASE") return `<span class="tag release">stage complete</span>`;
    if (m === "COMPLETE") return `<span class="tag complete">design complete</span>`;
    return "";
  }
  if (!item.verdict) return "";
  // Friendly labels for students; instructors see the raw verdicts.
  const label = { pass: "✓ solid", partial: "almost", misconception: "rethink this", evasive: "let's try together" }[item.verdict];
  return `<span class="tag ${item.verdict}">${label}</span>`;
}

function renderMessages() {
  const box = $("#messages");
  box.innerHTML = state.transcript
    .map((m) => {
      const tag = tagFor(m);
      return `<div class="msg ${m.role}">${tag ? `<div class="meta">${tag}</div>` : ""}${md(m.text)}</div>`;
    })
    .join("");
  box.scrollTop = box.scrollHeight;
}

function renderRoadmap() {
  $("#tab-roadmap").innerHTML = state.stages
    .map((st, i) => {
      const concepts = st.concepts
        .map((c) => {
          const dots = [1, 2, 3]
            .map((t) => `<span class="${t <= c.highest_tier_passed ? "done" : t > c.required_tier ? "na" : ""}" title="Tier ${t}"></span>`)
            .join("");
          return `<div class="concept ${c.current ? "current" : ""}">
            <span>${esc(c.name)} ${c.is_core ? '<span class="core">CORE</span>' : ""}${c.needs_review ? ' <span class="revisit">REVISIT</span>' : ""}</span>
            <span class="tiers">${dots}</span></div>`;
        })
        .join("");
      const badge = st.status === "done" ? "✓ done" : st.status === "current" ? "in progress" : "locked";
      return `<div class="stage ${st.status}">
        <h3><span>${i + 1}. ${esc(st.name)}</span><span class="muted small">${badge}</span></h3>
        <div class="goal">${esc(st.goal)}</div>${st.status === "locked" ? "" : concepts}</div>`;
    })
    .join("");
}

function renderDocs() {
  const sel = $("#doc-select");
  const current = sel.value;
  sel.innerHTML = state.documents
    .map((d) => `<option value="${d.stage_id}">${esc(STAGE_NAMES[d.stage_id] || d.stage_id)}</option>`)
    .join("");
  const stageNow = state.stages.find((s) => s.status === "current");
  sel.value = state.documents.some((d) => d.stage_id === current) ? current : stageNow?.id || state.documents.at(-1)?.stage_id;
  if (!docDirty) loadDoc();
}

function loadDoc() {
  const d = state.documents.find((x) => x.stage_id === $("#doc-select").value);
  $("#doc-editor").value = d ? d.content : "";
  docDirty = false;
  $("#doc-status").textContent = "";
}

function renderUnlocked() {
  const items = state.releases
    .slice()
    .reverse()
    .map((r) => `<div class="release"><h4>${r.kind === "checklist" ? "✓ Checklist" : "Template"} · ${esc(STAGE_NAMES[r.stage_id] || r.stage_id)}</h4>
      <pre>${esc(r.content)}</pre></div>`)
    .join("");
  $("#tab-unlocked").innerHTML =
    `<p class="muted small">Templates unlock when a stage starts. Checklists unlock when you pass a stage's gate.</p>` + items;
}

function renderLadder() {
  const rung = state.hint_rung;
  const rungs = Array.from({ length: state.max_hint_rung }, (_, i) => `<span class="rung ${i < rung ? "on" : ""}"></span>`).join("");
  const tier = state.completed ? "" : `Tier ${state.tier} · `;
  $("#ladder").innerHTML = `<span>${tier}Hints used</span>${rungs}`;
}

// ---------------------------------------------------------------- answering

$("#answer").addEventListener("paste", (e) => {
  const t = e.clipboardData?.getData("text") || "";
  pastedChars += t.length;
});

$("#answer").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#answer-form").requestSubmit();
  }
});

$("#answer-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#answer").value.trim();
  if (!text || !state) return;
  const latency = Date.now() - questionShownAt;
  const box = $("#messages");
  box.insertAdjacentHTML("beforeend", `<div class="msg student">${md(text)}</div><div class="msg tutor typing" id="typing">Thinking about your answer…</div>`);
  box.scrollTop = box.scrollHeight;
  $("#answer").value = "";
  $("#send-btn").disabled = true;
  try {
    const s = await api(`/api/projects/${state.id}/answer`, {
      method: "POST",
      body: JSON.stringify({ text, latency_ms: latency, pasted_chars: Math.min(pastedChars, text.length) }),
    });
    render(s);
  } catch (err) {
    $("#typing")?.remove();
    box.lastElementChild?.remove();
    $("#answer").value = text;
    box.insertAdjacentHTML("beforeend", `<div class="msg tutor error">${esc(err.message)}</div>`);
  } finally {
    $("#send-btn").disabled = state.completed && state.transcript.at(-1)?.role === "student";
  }
});

// ---------------------------------------------------------------- documents & tabs

$("#doc-select").addEventListener("change", loadDoc);
$("#doc-editor").addEventListener("input", () => {
  docDirty = true;
  $("#doc-status").textContent = "Unsaved changes";
});
$("#doc-save").addEventListener("click", async () => {
  const stage = $("#doc-select").value;
  try {
    const d = await api(`/api/projects/${state.id}/documents/${stage}`, {
      method: "PUT",
      body: JSON.stringify({ content: $("#doc-editor").value }),
    });
    const local = state.documents.find((x) => x.stage_id === stage);
    if (local) local.content = d.content;
    docDirty = false;
    $("#doc-status").textContent = "Saved. The tutor will see it on your next answer.";
  } catch (err) {
    $("#doc-status").textContent = err.message;
  }
});

document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    ["roadmap", "docs", "unlocked"].forEach((n) => ($(`#tab-${n}`).hidden = n !== t.dataset.tab));
  }));

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }

initStart();
