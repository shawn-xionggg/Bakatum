// ---------- state ----------
let lastWindow = null;
let pendingImport = null;      // {course_name, assignments} awaiting review-save
let sessionTimer = null;       // interval handle
let activeSession = null;

const $ = (id) => document.getElementById(id);
const statusEl = $("status");

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

const today = new Date();
const inTwoWeeks = new Date(today.getTime() + 14 * 864e5);
const iso = (d) => d.toISOString().slice(0, 10);
$("start_date").value = iso(today);
$("end_date").value = iso(inTwoWeeks);

// ---------- API keys panel ----------
async function refreshKeyStatus() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    const set = (id, on) => {
      const el = $(id);
      el.textContent = on ? "· set" : "· not set";
      el.classList.toggle("ok", on);
    };
    set("anthropic-status", cfg.anthropic_key);
    set("steel-status", cfg.steel_key);
    $("keys-summary").textContent =
      cfg.anthropic_key && cfg.steel_key ? "(both set)" : "(some missing)";
  } catch { /* ignore */ }
}
refreshKeyStatus();

$("save-keys").addEventListener("click", async () => {
  const body = {
    anthropic_api_key: $("anthropic-key").value.trim(),
    steel_api_key: $("steel-key").value.trim(),
  };
  if (!body.anthropic_api_key && !body.steel_api_key) {
    $("keys-status").textContent = "Nothing to save.";
    return;
  }
  $("keys-status").textContent = "Saving…";
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.json()).error || "Save failed");
    $("keys-status").textContent = "Saved.";
    $("anthropic-key").value = "";
    $("steel-key").value = "";
    refreshKeyStatus();
  } catch (e) {
    $("keys-status").textContent = e.message;
  }
});

// ---------- upload → preview → extract → review ----------
$("file").addEventListener("change", (e) => {
  const name = e.target.files[0]?.name;
  $("dropzone-text").textContent = name || "Drop a syllabus or click to browse";
  $("dropzone-text").classList.toggle("has-file", !!name);
});

$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("file").files[0];
  if (!file) { setStatus("Choose a file first.", true); return; }
  setStatus("Extracting text…");
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/preview", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Preview failed");
    $("preview-text").value = data.text;
    $("preview-panel").classList.remove("hidden");
    setStatus("Review the text, then extract.");
  } catch (err) {
    setStatus(err.message, true);
  }
});

$("extract-btn").addEventListener("click", async () => {
  const btn = $("extract-btn");
  btn.disabled = true;
  setStatus("Sending to Claude — extracting assignments…");
  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: $("preview-text").value,
        start_date: $("start_date").value,
        end_date: $("end_date").value,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Extraction failed");
    lastWindow = data.window;
    renderReview(
      data.course_name || "Review extracted assignments",
      `Window: ${data.window.start} → ${data.window.end} · edit anything before saving · check "skip" to drop an item`,
      data.assignments
    );
    pendingImport = { course_name: data.course_name || "", assignments: data.assignments };
    setStatus(`Found ${data.assignments.length} assignments — review below, then save.`);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

function renderReview(title, note, assignments) {
  $("course-name").textContent = title;
  $("window-note").textContent = note;
  const list = $("review-list");
  list.innerHTML = "";
  const tpl = $("review-tpl");

  for (const a of assignments) {
    const node = tpl.content.cloneNode(true);
    const item = node.querySelector(".review-item");
    item.dataset.raw = JSON.stringify(a);
    item.querySelector(".r-title").value = a.title || "";
    item.querySelector(".r-due").value = a.due_date || "";
    item.querySelector(".r-type").value = a.type || "other";
    item.querySelector(".r-importance").value = a.importance || "medium";
    item.querySelector(".r-hours").value = a.estimated_hours ?? "";
    item.querySelector(".r-desc").textContent =
      [a.description, a.grade_weight].filter(Boolean).join(" — ");
    if (a.recommended) item.classList.add("pick");
    list.appendChild(node);
  }
  $("review").classList.remove("hidden");
  $("save-status").textContent = "";
}

$("save-tasks").addEventListener("click", async () => {
  const items = [...document.querySelectorAll(".review-item")];
  const assignments = [];
  for (const item of items) {
    if (item.querySelector(".r-skip input").checked) continue;
    const a = JSON.parse(item.dataset.raw);
    a.title = item.querySelector(".r-title").value.trim() || a.title;
    a.due_date = item.querySelector(".r-due").value || null;
    a.type = item.querySelector(".r-type").value;
    a.importance = item.querySelector(".r-importance").value;
    const h = parseFloat(item.querySelector(".r-hours").value);
    a.estimated_hours = isNaN(h) ? a.estimated_hours : h;
    assignments.push(a);
  }
  if (!assignments.length) { $("save-status").textContent = "Nothing selected."; return; }
  $("save-status").textContent = "Saving…";
  try {
    const res = await fetch("/api/tasks/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course_name: pendingImport?.course_name || "", assignments }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Save failed");
    $("save-status").textContent = `Saved ${data.imported} tasks.`;
    loadDashboard();
  } catch (e) {
    $("save-status").textContent = e.message;
  }
});

// ---------- dashboard ----------
async function loadDashboard() {
  const course = $("filter-course").value;
  const status = $("filter-status").value;
  const params = new URLSearchParams();
  if (course) params.set("course", course);
  if (status) params.set("status", status);
  if (lastWindow) { params.set("start", lastWindow.start); params.set("end", lastWindow.end); }

  const data = await (await fetch(`/api/tasks?${params}`)).json();
  renderGrapes(data.progress);
  renderCourseFilter(data.courses);
  renderTasks(data.tasks);
  loadActiveSession();
}

function renderGrapes(p) {
  const el = $("grapes");
  el.innerHTML = "";
  for (let i = 0; i < p.total; i++) {
    const g = document.createElement("span");
    g.textContent = "🍇";
    g.className = i < p.done ? "grape done" : "grape";
    el.appendChild(g);
  }
  $("progress-count").textContent = `${p.done}/${p.total} tasks done`;
}

function renderCourseFilter(courses) {
  const sel = $("filter-course");
  const cur = sel.value;
  sel.innerHTML = '<option value="">All courses</option>';
  for (const c of courses) {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = c;
    sel.appendChild(o);
  }
  sel.value = cur;
}

function renderTasks(tasks) {
  const list = $("task-list");
  list.innerHTML = "";
  $("empty-dashboard").classList.toggle("hidden", tasks.length > 0);
  const tpl = $("task-tpl");

  for (const t of tasks) {
    const node = tpl.content.cloneNode(true);
    const card = node.querySelector(".assignment");
    card.querySelector(".title").textContent = `${t.course ? t.course + " — " : ""}${t.title}`;

    const urgency = card.querySelector(".urgency");
    urgency.textContent = t.urgency;
    urgency.classList.add(t.urgency);
    const importance = card.querySelector(".importance");
    importance.textContent = `${t.importance} importance`;
    importance.classList.add(t.importance);
    card.querySelector(".type").textContent = t.type;

    card.querySelector(".due").textContent =
      (t.due_date ? `Due ${t.due_date}` : "No firm date") +
      (t.due_text && t.due_text !== t.due_date ? ` · “${t.due_text}”` : "");
    card.querySelector(".desc").textContent = t.description || "";
    card.querySelector(".meta").textContent = [
      t.grade_weight,
      t.remaining_minutes ? `~${t.remaining_minutes} min left` : null,
    ].filter(Boolean).join(" · ");

    const subs = card.querySelector(".subtasks");
    (t.subtasks || []).forEach((s, i) => {
      const li = document.createElement("li");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!s.done;
      cb.addEventListener("change", async () => {
        s.done = cb.checked;
        await patchTask(t.id, { subtasks: t.subtasks });
      });
      li.append(cb, " " + (s.text || s));
      if (s.done) li.classList.add("done");
      subs.appendChild(li);
    });
    if (!subs.children.length) subs.remove();

    const linksEl = card.querySelector(".links");
    const allLinks = [t.link, ...(t.links || [])].filter(Boolean);
    for (const u of allLinks) {
      const a = document.createElement("a");
      a.href = u; a.target = "_blank"; a.rel = "noopener";
      a.textContent = u.length > 60 ? u.slice(0, 57) + "…" : u;
      linksEl.appendChild(a);
    }

    const doneCheck = card.querySelector(".done-check");
    doneCheck.checked = t.status === "done";
    doneCheck.addEventListener("change", () =>
      patchTask(t.id, { status: doneCheck.checked ? "done" : "todo" }).then(loadDashboard));
    const subCheck = card.querySelector(".submitted-check");
    subCheck.checked = t.submitted;
    subCheck.addEventListener("change", () =>
      patchTask(t.id, { submitted: subCheck.checked }).then(loadDashboard));

    if (t.status === "done") card.classList.add("task-done");
    const urgent = ["overdue", "critical", "high"].includes(t.urgency);
    if (t.in_window === false && !urgent) card.classList.add("outside-window");

    card.querySelector(".research-btn").addEventListener("click", (e) =>
      runResearch(e.target, card, t)
    );
    list.appendChild(node);
  }
}

async function patchTask(id, fields) {
  await fetch(`/api/tasks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
}

$("filter-course").addEventListener("change", loadDashboard);
$("filter-status").addEventListener("change", loadDashboard);

// ---------- Waterloo Learn sync (cookie-based, no Steel) ----------
$("learn-sync-btn").addEventListener("click", async () => {
  const btn = $("learn-sync-btn");
  const st = $("learn-status");
  btn.disabled = true;
  try {
    let status = await (await fetch("/api/learn/status")).json();

    if (!status.authenticated) {
      st.textContent = "A browser window will open — sign in to WatIAM + Duo…";
      await fetch("/api/learn/login", { method: "POST" });
      // poll until the user finishes signing in (5 min window)
      const deadline = Date.now() + 300_000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2000));
        status = await (await fetch("/api/learn/status")).json();
        if (status.authenticated) break;
        if (!status.running) {
          throw new Error(status.error || "Login window closed before sign-in completed.");
        }
      }
      if (!status.authenticated) throw new Error("Login timed out.");
    }

    st.textContent = "Signed in — pulling courses, dropboxes, and quizzes from Learn…";
    const res = await fetch("/api/learn/sync", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Sync failed");

    renderReview(
      "Waterloo Learn",
      `${data.items.length} dropbox folders & quizzes across ${data.courses.length} courses — uncheck "skip" on what to import`,
      data.items
    );
    pendingImport = { course_name: "", assignments: data.items };
    st.textContent = `Found ${data.items.length} items — review below.`;
    document.getElementById("review").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    st.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
});

// ---------- recommend & sessions ----------
$("recommend-btn").addEventListener("click", async () => {
  HARDWARE_MINUTES();
  const data = await (await fetch(`/api/recommend?minutes=${$("minutes").value}`)).json();
  const box = $("recommendation");
  if (!data.task) {
    box.classList.remove("hidden");
    $("rec-title").textContent = "Nothing to do";
    $("rec-reason").textContent = data.reason;
    $("start-btn").classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  $("start-btn").classList.remove("hidden");
  $("rec-title").textContent = data.task.title;
  $("rec-reason").textContent = data.reason;
  $("start-btn").dataset.taskId = data.task.id;
  $("start-btn").dataset.link = data.task.link || "";
});

$("start-btn").addEventListener("click", async (e) => {
  const taskId = e.target.dataset.taskId;
  const res = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: Number(taskId), minutes: Number($("minutes").value) }),
  });
  const data = await res.json();
  if (!res.ok) { alert(data.error); return; }
  if (e.target.dataset.link) window.open(e.target.dataset.link, "_blank", "noopener");
  loadDashboard();
});

async function loadActiveSession() {
  const data = await (await fetch("/api/session")).json();
  activeSession = data.session;
  const box = $("active-session");
  if (!activeSession) {
    box.classList.add("hidden");
    clearInterval(sessionTimer);
    return;
  }
  box.classList.remove("hidden");
  $("session-title").textContent = activeSession.task?.title || `Task #${activeSession.task_id}`;
  tickTimer();
  clearInterval(sessionTimer);
  sessionTimer = setInterval(tickTimer, 1000);
}

function tickTimer() {
  if (!activeSession) return;
  const started = new Date(activeSession.started_at + "Z");
  const elapsed = Math.max(0, Math.floor((Date.now() - started.getTime()) / 1000));
  const mm = Math.floor(elapsed / 60), ss = String(elapsed % 60).padStart(2, "0");
  $("session-timer").textContent = `${mm}:${ss}`;
  $("session-planned").textContent = `${activeSession.planned_minutes + activeSession.extended_minutes} min`;
}

$("more-time-btn").addEventListener("click", async () => {
  if (!activeSession) return;
  const data = await (await fetch(`/api/sessions/${activeSession.id}/extend`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ minutes: 15 }),
  })).json();
  if (data.session) { activeSession = data.session; tickTimer(); }
});

$("done-btn").addEventListener("click", async () => {
  if (!activeSession) return;
  await fetch(`/api/sessions/${activeSession.id}/finish`, { method: "POST" });
  await patchTask(activeSession.task_id, { status: "done" });
  activeSession = null;
  loadDashboard();
});

$("skip-btn").addEventListener("click", async () => {
  await fetch("/api/hardware", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "skip" }),
  });
  activeSession = null;
  loadDashboard();
  $("recommend-btn").click();
});

async function HARDWARE_MINUTES() {
  await fetch("/api/hardware", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "set_minutes", value: Number($("minutes").value) }),
  });
}
$("minutes").addEventListener("change", HARDWARE_MINUTES);

// ---------- research (Steel agent) ----------
async function runResearch(btn, scopeEl, assignment) {
  const panel = scopeEl.querySelector(".research-panel") || scopeEl;
  const status = panel.querySelector(".research-status");
  const live = panel.querySelector(".live-view");
  const body = panel.querySelector(".research-body");

  try {
    if (!status || !live || !body) throw new Error("Research panel markup missing.");
    panel.classList.remove("hidden");
    btn.disabled = true;
    status.textContent = "Starting research…";
    live.innerHTML = "";
    body.innerHTML = "";

    const res = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        assignment,
        require_login: false,
        window: lastWindow,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Research failed to start");

    if (data.viewer_url) {
      status.textContent = "Live Steel session:";
      const bar = document.createElement("div");
      bar.className = "live-bar";
      const reload = document.createElement("button");
      reload.type = "button";
      reload.className = "reload-btn";
      reload.textContent = "Reload live view";
      const frame = document.createElement("iframe");
      frame.src = data.viewer_url;
      frame.className = "steel-frame";
      frame.allow = "autoplay; fullscreen";
      reload.addEventListener("click", () => { frame.src = data.viewer_url; });
      bar.appendChild(reload);
      live.append(bar, frame);
    } else {
      status.textContent = "Researching… (no STEEL_API_KEY — no live view)";
    }

    const result = await pollJob(data.job_id, (d) => {
      if (d.status === "awaiting_login") showLoginGate();
      else if (d.log?.length) status.textContent = `Working… ${d.log[d.log.length - 1]}`;
    });
    status.textContent = "";
    renderResearch(body, result, assignment);
  } catch (err) {
    if (status) status.textContent = `Error: ${err.message}`;
    btn.disabled = false;
  }

  function showLoginGate() {
    if (scopeEl.querySelector(".resume-btn")) return;
    status.textContent = "Sign in to Waterloo Learn in the browser above, then:";
    const resume = document.createElement("button");
    resume.type = "button";
    resume.className = "resume-btn";
    resume.textContent = "I'm signed in — continue";
    resume.addEventListener("click", async () => {
      resume.disabled = true;
      resume.textContent = "Resuming…";
      await fetch(`/api/research/${data.job_id}/resume`, { method: "POST" });
      resume.remove();
      status.textContent = "Researching — live Steel browser session:";
    });
    live.appendChild(resume);
  }
}

function pollJob(jobId, onStatus) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const res = await fetch(`/api/research/${jobId}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Job lookup failed");
        if (onStatus) onStatus(data);
        if (data.status === "done") return resolve(data);
        if (data.status === "error") return reject(new Error(data.error || "Research failed"));
        setTimeout(tick, 2000);
      } catch (e) {
        reject(e);
      }
    };
    tick();
  });
}

function renderResearch(body, data, assignment) {
  const summary = document.createElement("p");
  summary.className = "summary";
  summary.textContent = data.summary;
  body.appendChild(summary);

  if (data.steps?.length) {
    const h = document.createElement("p");
    h.className = "steps-heading";
    h.textContent = "Plan:";
    const ul = document.createElement("ul");
    ul.className = "steps";
    for (const s of data.steps) {
      const li = document.createElement("li");
      li.textContent = s;
      ul.appendChild(li);
    }
    body.append(h, ul);
  }

  if (data.assignments_found?.length) {
    const h = document.createElement("p");
    h.className = "steps-heading";
    h.textContent = "Found on Waterloo Learn:";
    const ul = document.createElement("ul");
    ul.className = "found-list";
    for (const f of data.assignments_found) {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = f.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = f.title;
      const meta = document.createElement("p");
      meta.className = "why";
      meta.textContent = [f.course, f.due ? `Due: ${f.due}` : null, f.in_window ? "in window" : null]
        .filter(Boolean).join(" · ");
      li.append(link, meta);
      ul.appendChild(li);
    }
    body.append(h, ul);
  }

  const ul = document.createElement("ul");
  for (const r of data.resources || []) {
    const li = document.createElement("li");
    const link = document.createElement("a");
    link.href = r.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = r.title;
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = r.kind;
    const why = document.createElement("p");
    why.className = "why";
    why.textContent = r.why;
    li.append(link, kind, why);
    ul.appendChild(li);
  }
  body.appendChild(ul);

  // Attach discovered links to the task so "Start session" can open them.
  const urls = [
    ...(data.assignments_found || []).map((f) => f.url),
    ...(data.resources || []).map((r) => r.url),
  ].filter(Boolean);
  if (assignment.id && urls.length) {
    patchTask(assignment.id, { link: urls[0], links: urls.slice(0, 8) });
  }

  if (urls.length) {
    const openAll = document.createElement("button");
    openAll.type = "button";
    openAll.className = "open-all-btn";
    openAll.textContent = `Open all ${urls.length} links in my browser`;
    openAll.addEventListener("click", () => {
      for (const u of urls.slice(0, 8)) window.open(u, "_blank", "noopener");
    });
    body.appendChild(openAll);
  }
}

// ---------- boot ----------
loadDashboard();
