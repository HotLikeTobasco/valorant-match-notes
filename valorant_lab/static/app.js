"use strict";
const $ = (id) => document.getElementById(id);
let token = "",
  selected = null,
  current = null,
  dirty = false,
  mode = null,
  sessions = [],
  busy = false;
const emptyEvidence = () => ({
  schema_version: 1,
  events: [],
  observations: [],
  map_name: "Unknown",
  note: "",
});
let evidence = emptyEvidence();
const time = (t) =>
  `${Math.floor(t / 60)
    .toString()
    .padStart(2, "0")}:${(t % 60).toFixed(2).padStart(5, "0")}`;
function node(tag, text, className) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (className) n.className = className;
  return n;
}
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  setTimeout(() => ($("toast").hidden = true), 7000);
}
async function api(path, options = {}) {
  const r = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Lab-Token": token,
      ...options.headers,
    },
  });
  if (!r.ok) {
    let d;
    try {
      d = await r.json();
    } catch {
      d = { detail: r.statusText };
    }
    throw Error(
      typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail),
    );
  }
  return r.json();
}
function changed() {
  dirty = true;
  $("save-state").textContent = "UNSAVED REVIEW CHANGES";
  $("save-state").className = "muted unsaved";
}
function jump(t) {
  $("video").currentTime = t;
  $("video").pause();
}
async function selectSession(id) {
  if (dirty && !confirm("Discard unsaved review changes?")) return;
  dirty = false;
  selected = id;
  await refreshDetail(true);
  renderSessions();
}
function renderSessions() {
  const root = $("sessions");
  root.replaceChildren();
  $("session-count").textContent = sessions.length;
  for (const s of sessions) {
    const b = node(
      "button",
      undefined,
      "session" + (s.id === selected ? " selected" : ""),
    );
    b.append(
      node("strong", s.name),
      node(
        "small",
        `${s.state} ${s.state === "processing" ? Math.round(s.progress * 100) + "%" : ""}`,
      ),
    );
    b.onclick = () => selectSession(s.id).catch((e) => toast(e.message));
    root.append(b);
  }
}
function setMetric(id, m) {
  $(id).textContent =
    m?.value == null
      ? "—"
      : m.value.toFixed(1) + (id === "delay" ? " ms" : "%");
  $(id).title = m?.limitation || "";
}
function plot(points = []) {
  const canvas = $("placement"),
    ctx = canvas.getContext("2d"),
    w = canvas.width,
    h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "#263440";
  ctx.lineWidth = 1;
  for (let x = 0; x <= w; x += 40) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }
  for (let y = 10; y < h; y += 40) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }
  ctx.strokeStyle = "#647986";
  ctx.beginPath();
  ctx.moveTo(w / 2, 0);
  ctx.lineTo(w / 2, h);
  ctx.moveTo(0, h / 2);
  ctx.lineTo(w, h / 2);
  ctx.stroke();
  ctx.strokeStyle = "#ecf6f2";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(w / 2 - 9, h / 2);
  ctx.lineTo(w / 2 + 9, h / 2);
  ctx.moveTo(w / 2, h / 2 - 9);
  ctx.lineTo(w / 2, h / 2 + 9);
  ctx.stroke();
  ctx.fillStyle = "#75e2be99";
  for (const p of points) {
    const x = w / 2 + (p.head_x - p.crosshair_x) * w,
      y = h / 2 + (p.head_y - p.crosshair_y) * h;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.fillStyle = "#8e9aa8";
  ctx.font = "11px sans-serif";
  ctx.fillText(
    points.length
      ? `${points.length} target observations`
      : "Awaiting target evidence",
    12,
    h - 12,
  );
}
function renderEvents() {
  const root = $("timeline"),
    filter = $("event-filter").value;
  root.replaceChildren();
  const source = dirty
    ? evidence.events
    : current?.report?.events || evidence.events;
  const items = source
    .filter((e) => filter === "all" || e.kind === filter)
    .sort((a, b) => a.time - b.time);
  if (!items.length) root.append(node("p", "No events in this view.", "empty"));
  for (const e of items.slice(0, 500)) {
    const b = node("button", undefined, "event");
    b.append(node("span", time(e.time), "time"));
    const text = node("span", e.kind.replaceAll("_", " "), "event-text");
    text.append(
      node(
        "small",
        (e.source === "manual" ? "Reviewed" : "Candidate") +
          " · " +
          (e.note || e.source),
      ),
    );
    b.append(text, node("small", e.confidence.toFixed(2)));
    b.onclick = () => jump(e.time);
    root.append(b);
  }
  if (items.length > 500)
    root.append(
      node(
        "p",
        "Showing first 500 events. Filter by type for a narrower view.",
        "hint",
      ),
    );
}
function renderReport() {
  const r = current?.report;
  setMetric("alignment", r?.metrics.alignment_error);
  setMetric("vertical", r?.metrics.vertical_offset);
  setMetric("delay", r?.metrics.first_shot_delay);
  $("engagements").textContent = r ? r.engagements.length : "—";
  $("delay-detail").textContent = r?.metrics.first_shot_delay.samples
    ? `${r.metrics.first_shot_delay.samples} linked shots · estimated`
    : "Needs linked shot annotations";
  $("alignment-detail").textContent = r?.metrics.alignment_error.samples
    ? `${r.metrics.alignment_error.samples} samples · ${r.metrics.alignment_error.basis}`
    : "Needs target observations";
  $("placement-note").textContent = r?.points.length
    ? "Each dot is a supplied head position relative to the crosshair. This is a sample distribution, not a calibrated skill score."
    : "No target observations yet. Add reviewed head positions or configure a validated head detector.";
  plot(r?.points || []);
  renderEvents();
  const coach = $("coaching");
  coach.replaceChildren();
  if (!r?.coaching.length)
    coach.append(
      node(
        "p",
        "No recommendations yet. Coaching needs repeated observations, not a guess from an empty session.",
        "empty",
      ),
    );
  for (const c of r?.coaching || []) {
    const card = node("article", undefined, "focus-card");
    card.append(
      node("h3", c.title),
      node(
        "small",
        `${c.frequency}/${c.denominator} reviewed occurrences · score ${c.confidence.toFixed(2)} · ${c.basis}`,
      ),
      node("p", c.finding),
      node("p", c.practice),
      node(
        "small",
        c.progress?.text || "No comparable previous recommendation yet.",
      ),
    );
    for (const t of c.evidence) {
      const b = node("button", time(t));
      b.onclick = () => jump(t);
      card.append(b);
    }
    coach.append(card);
  }
  $("rounds").replaceChildren();
  for (const round of r?.rounds || []) {
    const b = node(
      "button",
      `Round ${round.number} · ${round.kills}K / ${round.deaths}D`,
      "secondary",
    );
    b.onclick = () => jump(round.start);
    $("rounds").append(b);
  }
  $("metric-limit").textContent = r?.metrics.alignment_error.limitation || "";
}
async function refreshDetail(force = false) {
  if (!selected) return;
  const s = await api("/api/sessions/" + selected);
  const old = current;
  current = s;
  $("session-name").textContent = s.name;
  $("session-meta").textContent =
    `${s.map_name} · ${s.metadata.duration ? time(s.metadata.duration) : "—"} · ${s.state}${s.state === "processing" ? " · " + Math.round(s.progress * 100) + "%" : ""}`;
  $("video-state").textContent =
    s.state === "complete" ? "READY FOR REVIEW" : s.state.toUpperCase();
  $("retry").hidden = s.state === "queued" || s.state === "processing";
  if (s.error) {
    $("notice").textContent = s.error;
    $("notice").className = "notice error";
  }
  const ready = s.state === "complete";
  for (const id of [
    "mark-head",
    "mark-crosshair",
    "export-clip",
    "add-event",
    "save-evidence",
    "download-evidence",
    "frame-back",
    "frame-next",
    "remove-point",
    "remove-event",
  ])
    $(id).disabled = !ready;
  if (ready) {
    $("video-empty").hidden = true;
    if (force || old?.state !== "complete" || old?.id !== s.id) {
      $("video").src = "/api/sessions/" + s.id + "/video";
    }
  } else {
    if (force) {
      $("video").removeAttribute("src");
      $("video").load();
    }
    $("video-empty").hidden = false;
  }
  if (!dirty && (force || old?.updated !== s.updated)) {
    evidence = s.evidence || emptyEvidence();
    $("map-name").value = evidence.map_name;
    $("save-state").textContent = "NO UNSAVED CHANGES";
    $("save-state").className = "muted";
    renderReport();
  }
}
async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const status = await api("/api/status");
    token = status.token;
    $("notice").title = status.recordings_dir;
    if (!current?.error) {
      $("notice").textContent =
        status.watcher_error ||
        `Watching your recording folder · ${status.sample_fps} analysis frames/sec`;
      $("notice").className = "notice" + (status.watcher_error ? " error" : "");
    }
    sessions = await api("/api/sessions");
    renderSessions();
    if (!selected && sessions.length) await selectSession(sessions[0].id);
    else await refreshDetail();
    const trends = await api("/api/trends");
    const root = $("trend-list");
    root.replaceChildren();
    if (!trends.points.length)
      root.append(
        node(
          "p",
          "Comparable sessions with target observations will appear here.",
          "empty",
        ),
      );
    const max = Math.max(1, ...trends.points.map((p) => p.value));
    for (const p of trends.points) {
      const row = node("div", undefined, "trend");
      const label = node("span", p.name);
      label.append(node("small", `${p.map_name} · ${p.samples} samples`));
      const meter = document.createElement("meter");
      meter.min = 0;
      meter.max = max;
      meter.value = p.value;
      meter.setAttribute("aria-label", p.name + " mean alignment error");
      row.append(
        label,
        meter,
        node(
          "span",
          `${p.value.toFixed(1)}%${p.delta === null ? "" : ` (${p.delta >= 0 ? "+" : ""}${p.delta.toFixed(1)})`}`,
        ),
      );
      root.append(row);
    }
  } catch (e) {
    toast(e.message);
  } finally {
    busy = false;
  }
}
$("video").ontimeupdate = () =>
  ($("timestamp").textContent = time($("video").currentTime));
$("event-filter").onchange = renderEvents;
$("map-name").oninput = () => {
  evidence.map_name = $("map-name").value;
  changed();
};
$("event-form").onsubmit = (e) => {
  e.preventDefault();
  if (!current || current.state !== "complete") return;
  const kind = $("event-kind").value;
  evidence.events.push({
    time: $("video").currentTime,
    kind,
    confidence: 1,
    source: "manual",
    note: $("event-note").value,
    target_id: kind === "shot" ? $("target").value : null,
    tag: $("event-tag").value || null,
  });
  $("event-note").value = "";
  changed();
  renderEvents();
};
for (const [id, type] of [
  ["mark-head", "head"],
  ["mark-crosshair", "crosshair"],
])
  $(id).onclick = () => {
    mode = type;
    $("video").pause();
    $("point-layer").hidden = false;
    $("annotation-help").textContent =
      `Click the ${type === "head" ? "enemy head" : "crosshair"} in the paused video. Press Escape to cancel.`;
  };
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    $("point-layer").hidden = true;
    mode = null;
  }
});
$("point-layer").onclick = (e) => {
  const video = $("video"),
    rect = video.getBoundingClientRect(),
    scale = Math.min(
      rect.width / video.videoWidth,
      rect.height / video.videoHeight,
    );
  const width = video.videoWidth * scale,
    height = video.videoHeight * scale;
  const x = (e.clientX - rect.left - (rect.width - width) / 2) / width,
    y = (e.clientY - rect.top - (rect.height - height) / 2) / height;
  if (!Number.isFinite(x) || x < 0 || x > 1 || y < 0 || y > 1) return;
  const t = video.currentTime,
    target = $("target").value.trim();
  if (!target) return toast("Enter a target ID first.");
  const same = evidence.observations.find(
    (o) => o.target_id === target && Math.abs(o.time - t) < 0.001,
  );
  if (mode === "head") {
    if (same) {
      same.head_x = x;
      same.head_y = y;
    } else
      evidence.observations.push({
        time: t,
        head_x: x,
        head_y: y,
        crosshair_x: 0.5,
        crosshair_y: 0.5,
        crosshair_source: "assumed_center",
        target_id: target,
        confidence: 1,
        source: "manual",
      });
  } else {
    if (!same)
      return toast("Mark this target’s head at the current frame first.");
    same.crosshair_x = x;
    same.crosshair_y = y;
    same.crosshair_source = "manual";
  }
  changed();
  $("point-layer").hidden = true;
  mode = null;
  $("annotation-help").textContent =
    `Observation saved locally at ${time(t)}. Save review to calculate metrics.`;
  toast("Point added to review.");
};
$("save-evidence").onclick = async () => {
  try {
    await api(`/api/sessions/${selected}/evidence`, {
      method: "PUT",
      body: JSON.stringify(evidence),
    });
    dirty = false;
    await refreshDetail(true);
    toast("Review saved. Recalculating from cached video analysis.");
  } catch (e) {
    toast(e.message);
  }
};
$("retry").onclick = async () => {
  try {
    if (dirty) return toast("Save or export your review before reanalysing.");
    await api(`/api/sessions/${selected}/retry?reset=true`, { method: "POST" });
    await refreshDetail(true);
  } catch (e) {
    toast(e.message);
  }
};
$("download-evidence").onclick = () => {
  const blob = new Blob([JSON.stringify(evidence, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = node("a");
  a.href = url;
  a.download = "session.evidence.json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$("import-evidence").onchange = async (e) => {
  try {
    if (!current || current.state !== "complete")
      throw Error("Select a completed session first.");
    const file = e.target.files[0];
    if (!file) return;
    if (file.size > 20000000) throw Error("Evidence file is too large.");
    const data = JSON.parse(await file.text());
    if (
      data.schema_version !== 1 ||
      !Array.isArray(data.events) ||
      !Array.isArray(data.observations)
    )
      throw Error(
        "Expected schema_version 1 with events and observations arrays.",
      );
    await api(`/api/sessions/${selected}/evidence`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
    dirty = false;
    await refreshDetail(true);
    toast("Evidence imported and queued for analysis.");
  } catch (error) {
    toast(error.message);
  } finally {
    e.target.value = "";
  }
};
$("export-clip").onclick = async () => {
  try {
    const t = $("video").currentTime,
      d = current.metadata.duration;
    $("export-clip").disabled = true;
    const job = await api(`/api/sessions/${selected}/clips`, {
      method: "POST",
      body: JSON.stringify({
        start: Math.max(0, t - 5),
        end: Math.min(d, t + 5),
      }),
    });
    toast("Exporting clip in the background.");
    const poll = setInterval(async () => {
      try {
        const result = await api("/api/clips/" + job.id);
        if (result.state !== "processing") {
          clearInterval(poll);
          $("export-clip").disabled = false;
          if (result.error) throw Error(result.error);
          $("clip-download").href = result.url;
          $("clip-download").hidden = false;
          toast("Clip ready to download.");
        }
      } catch (e) {
        clearInterval(poll);
        $("export-clip").disabled = false;
        toast(e.message);
      }
    }, 1500);
  } catch (e) {
    $("export-clip").disabled = false;
    toast(e.message);
  }
};
window.addEventListener("beforeunload", (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
plot();
refresh();
setInterval(refresh, 4000);

for (const [id, delta] of [
  ["frame-back", -1],
  ["frame-next", 1],
])
  $(id).onclick = () => {
    const v = $("video");
    v.pause();
    v.currentTime = Math.max(
      0,
      Math.min(
        current.metadata.duration,
        v.currentTime + delta / current.metadata.fps,
      ),
    );
  };
$("remove-point").onclick = () => {
  if (evidence.observations.length) {
    evidence.observations.pop();
    changed();
    toast("Last target observation removed from unsaved review.");
  }
};
$("remove-event").onclick = () => {
  if (evidence.events.length) {
    evidence.events.pop();
    changed();
    renderEvents();
  }
};
