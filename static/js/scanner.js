/* ==========================================================================
   ScanSync — live attendance scanner
   Webcam → face-api descriptor → POST /api/sessions/<id>/scan/ → result card

   Pipeline per frame (≈10 fps):
     1. detect every face, draw brackets
     2. pick the largest face and check quality (size, yaw, centring)
     3. accumulate STABLE_FRAMES descriptors of the *same* face, average them
     4. skip if that face was answered recently (client cooldown), else send
   The server still owns matching + duplicate prevention; the cooldown only
   avoids hammering it while a student stands in front of the camera.
   ========================================================================== */
(() => {
  "use strict";
  const { $, $$, api, toast, escape } = window.ScanSync;
  const cfg = JSON.parse($("#scanner-config").textContent);

  // ---- Tunables ----------------------------------------------------------
  const FRAME_MS = 100;           // detection cadence
  const STABLE_FRAMES = 3;        // descriptors averaged before a scan
  const SAME_FACE = 0.5;          // descriptor distance treated as "same person" between frames
  const COOLDOWN_MATCH = 0.45;    // distance to a recently answered face
  const COOLDOWN_MS = { checked_in: 9000, duplicate: 7000, not_enrolled: 6000, unknown: 2500, closed: 5000 };
  const MIN_FACE = 0.11;          // face width ÷ frame width
  const MAX_YAW = 0.42;
  const RESULT_MS = 3800;
  const POLL_MS = 10000;

  // ---- Elements ----------------------------------------------------------
  const stage = $("#stage");
  const video = $("#scanner-video");
  const overlay = $("#scanner-overlay");
  const flash = $("#scanner-flash");
  const hud = $("#hud-status");
  const loader = $("#stage-loader");
  const cameraSelect = $("#camera-select");
  const result = $("#result");
  const feed = $("#live-feed");
  const feedEmpty = $("#feed-empty");
  const countdown = $("#countdown");

  // ---- State -------------------------------------------------------------
  let session = cfg.snapshot.session;
  let clockOffset = Date.parse(cfg.snapshot.server_time) - Date.now();
  let paused = false;
  let running = false;
  let inFlight = false;
  let track = [];                 // descriptors of the face currently in front of the camera
  let recent = [];                // [{desc, until, label, color}]
  let resultTimer = null;
  let soundOn = localStorage.getItem("scansync-sound") !== "off";
  const feedIds = new Set();

  // ======================================================================
  // UI helpers
  // ======================================================================
  function setHud(text, tone = "") {
    hud.dataset.tone = tone;
    hud.lastElementChild.textContent = text;
  }

  function setLoader(title, text, fraction, { error = false } = {}) {
    loader.classList.remove("is-done");
    $("#loader-title").textContent = title;
    $("#loader-text").textContent = text;
    $("#loader-spinner").hidden = error;
    $("#loader-retry").classList.toggle("hidden", !error);
    if (fraction != null) $("#loader-bar").style.width = `${Math.round(fraction * 100)}%`;
  }

  function avatarHtml(student) {
    if (!student) return "?";
    return student.photo ? `<img src="${escape(student.photo)}" alt="">` : escape(student.initials);
  }

  function updateCounts(counts) {
    if (!counts) return;
    for (const [key, value] of Object.entries(counts)) {
      $$(`[data-count="${key}"]`).forEach((el) => {
        if (el.textContent !== String(value)) {
          el.textContent = value;
          el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump");
        }
      });
    }
    $("#rate-ring").style.setProperty("--p", counts.rate);
    $("#rate-value").innerHTML = `${counts.rate}%<small>ARRIVED</small>`;
  }

  function feedItem(record, isNew) {
    const s = record.student;
    const li = document.createElement("li");
    li.dataset.id = record.id;
    if (isNew) li.className = "is-new";
    li.innerHTML = `
      <span class="avatar avatar--sm">${avatarHtml(s)}</span>
      <div class="grow" style="min-width:0">
        <div class="person__name">${escape(s.name)}</div>
        <div class="person__meta">${escape(s.student_id)} ·
          <span class="badge badge--${record.status.toLowerCase()}" style="height:20px;font-size:11px"><i class="dot"></i>${escape(record.status_label)}</span>
        </div>
      </div>
      <time datetime="${escape(record.time_in || "")}">${escape(record.time_label)}</time>`;
    return li;
  }

  function renderFeed(records, { markNew = false } = {}) {
    // records arrive newest-first
    const fresh = records.filter((r) => !feedIds.has(r.id));
    if (!fresh.length && feed.children.length) return;
    for (const r of [...fresh].reverse()) {
      feedIds.add(r.id);
      feed.prepend(feedItem(r, markNew));
    }
    while (feed.children.length > 40) feed.lastElementChild.remove();
    feedEmpty.hidden = feed.children.length > 0;
  }

  function showResult(data) {
    const outcome = data.outcome;
    const rec = data.record;
    const s = data.student;
    result.dataset.outcome = outcome;
    result.dataset.status = rec?.status || "";
    $("#result-avatar").innerHTML = avatarHtml(s);
    $("#result-name").textContent = s ? s.name : "Unknown face";
    $("#result-meta").textContent = s ? `${s.student_id} · ${s.section}` : "Not registered in ScanSync";
    $("#result-msg").textContent = data.message || "";
    $("#result-time").textContent = rec?.time_label || new Date(Date.now() + clockOffset).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const badge = $("#result-badge");
    badge.textContent = rec ? (outcome === "duplicate" ? `Already ${rec.status_label}` : rec.status_label)
      : { unknown: "No match", not_enrolled: "Wrong section", closed: "Closed" }[outcome] || "";
    result.classList.add("is-visible");
    clearTimeout(resultTimer);
    resultTimer = setTimeout(() => result.classList.remove("is-visible"), RESULT_MS);

    const tone = toneFor(data);
    stage.dataset.state = tone.state;
    flash.style.setProperty("--flash", tone.flash);
    flash.classList.remove("is-on"); void flash.offsetWidth; flash.classList.add("is-on");
    setTimeout(() => { if (!paused && running) stage.dataset.state = "scanning"; }, 1600);
    beep(tone.sound);
  }

  function toneFor(data) {
    if (data.outcome === "checked_in") {
      return {
        PRESENT: { state: "success", flash: "rgba(16,185,129,.45)", sound: "success", color: "#34d399" },
        LATE: { state: "late", flash: "rgba(245,158,11,.45)", sound: "late", color: "#fbbf24" },
        ABSENT: { state: "error", flash: "rgba(244,63,94,.45)", sound: "error", color: "#fb7185" },
      }[data.record.status];
    }
    if (data.outcome === "duplicate") return { state: "success", flash: "rgba(165,180,252,.4)", sound: "soft", color: "#a5b4fc" };
    if (data.outcome === "not_enrolled") return { state: "late", flash: "rgba(245,158,11,.4)", sound: "error", color: "#fbbf24" };
    return { state: "error", flash: "rgba(244,63,94,.4)", sound: "error", color: "#fb7185" };
  }

  // ---- Sound (WebAudio, no assets) ---------------------------------------
  let audio;
  function beep(kind) {
    if (!soundOn) return;
    try {
      audio ||= new (window.AudioContext || window.webkitAudioContext)();
      const notes = { success: [880, 1320], late: [660, 660], soft: [740], error: [220, 180] }[kind] || [600];
      notes.forEach((freq, i) => {
        const t = audio.currentTime + i * 0.13;
        const osc = audio.createOscillator();
        const gain = audio.createGain();
        osc.type = kind === "error" ? "square" : "sine";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(kind === "error" ? 0.06 : 0.18, t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
        osc.connect(gain).connect(audio.destination);
        osc.start(t); osc.stop(t + 0.2);
      });
    } catch { /* audio unavailable */ }
  }

  function syncToggle(btn, on) {
    btn.querySelector("[data-on]").hidden = !on;
    btn.querySelector("[data-off]").hidden = on;
  }

  // ======================================================================
  // Countdown (Present window → Late window → Absent cut-off)
  // ======================================================================
  const fmtDur = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? `${h}:${String(m).padStart(2, "0")}` : String(m)) + `:${String(sec).padStart(2, "0")}`;
  };

  function tickCountdown() {
    const now = Date.now() + clockOffset;
    const start = Date.parse(session.start_time);
    const grace = Date.parse(session.grace_deadline);
    const absent = session.absent_deadline ? Date.parse(session.absent_deadline) : null;
    let phase, label, value;
    if (!session.is_open) {
      phase = "absent"; label = "Session closed"; value = "—";
    } else if (now < start) {
      phase = "present"; label = "Class starts in"; value = fmtDur(start - now);
    } else if (now <= grace) {
      phase = "present"; label = "Present window closes in"; value = fmtDur(grace - now);
    } else if (absent && now <= absent) {
      phase = "late"; label = "Arrivals now marked Late · Absent in"; value = fmtDur(absent - now);
    } else if (absent) {
      phase = "absent"; label = "Past the cut-off — scans are marked Absent"; value = `+${fmtDur(now - absent)}`;
    } else {
      phase = "late"; label = "Arrivals now marked Late"; value = `+${fmtDur(now - grace)}`;
    }
    countdown.dataset.phase = phase;
    $("#countdown-label").textContent = label;
    $("#countdown-value").textContent = value;
  }

  // ======================================================================
  // Recognition loop
  // ======================================================================
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function cooldownHit(desc) {
    const now = Date.now();
    recent = recent.filter((r) => r.until > now);
    return recent.find((r) => FaceEngine.distance(r.desc, desc) < COOLDOWN_MATCH);
  }

  async function loop() {
    running = true;
    while (running) {
      const t0 = performance.now();
      if (!paused && !document.hidden && video.readyState >= 2) {
        try { await frame(); } catch (err) { console.error(err); }
      }
      await sleep(Math.max(0, FRAME_MS - (performance.now() - t0)));
    }
  }

  async function frame() {
    const faces = await FaceEngine.detectAll(video, { inputSize: 320, scoreThreshold: 0.5 });
    const ctx = FaceEngine.syncCanvas(overlay, video);
    const W = video.videoWidth, H = video.videoHeight;

    if (!faces.length) {
      track = [];
      if (!result.classList.contains("is-visible")) stage.dataset.state = "scanning";
      setHud(inFlight ? "Identifying…" : "Ready — look at the camera", inFlight ? "" : "ok");
      return;
    }

    faces.sort((a, b) => b.detection.box.area - a.detection.box.area);
    const primary = faces[0];
    const q = FaceEngine.analyze(primary, W, H);
    const desc = Array.from(primary.descriptor);

    // Secondary faces: dim brackets only
    for (const f of faces.slice(1)) FaceEngine.drawBox(ctx, f.detection.box, { color: "rgba(255,255,255,.45)" });

    const cached = cooldownHit(desc);
    if (cached) {
      FaceEngine.drawBox(ctx, q.box, { color: cached.color, label: cached.label });
      setHud(faces.length > 1 ? "One student at a time, please" : "Next student, please", faces.length > 1 ? "warn" : "ok");
      track = [];
      return;
    }

    let hint = null;
    if (q.size < MIN_FACE) hint = "Step closer to the camera";
    else if (Math.abs(q.yaw) > MAX_YAW) hint = "Face the camera directly";
    else if (q.center > 0.38) hint = "Move into the frame";

    if (hint) {
      track = [];
      FaceEngine.drawBox(ctx, q.box, { color: "#fbbf24" });
      setHud(hint, "warn");
      return;
    }

    stage.dataset.state = "detecting";
    if (track.length && FaceEngine.distance(FaceEngine.mean(track), desc) > SAME_FACE) track = [];
    track.push(desc);
    const progress = Math.min(track.length / STABLE_FRAMES, 1);
    FaceEngine.drawBox(ctx, q.box, { color: "#22d3ee", label: inFlight ? "Identifying…" : `Hold still ${"●".repeat(track.length)}${"○".repeat(Math.max(0, STABLE_FRAMES - track.length))}` });
    setHud(faces.length > 1 ? "One student at a time, please" : "Hold still…", faces.length > 1 ? "warn" : "");

    if (progress >= 1 && !inFlight && session.is_open) {
      const descriptor = FaceEngine.mean(track);
      track = [];
      submit(descriptor);
    }
  }

  async function submit(descriptor) {
    inFlight = true;
    setHud("Identifying…");
    try {
      const data = await api(cfg.scanUrl, { method: "POST", body: { descriptor } });
      const tone = toneFor(data);
      const label = data.student ? data.student.first_name + (data.record ? ` · ${data.record.status_label}` : "") : "Unknown";
      recent.push({ desc: descriptor, until: Date.now() + (COOLDOWN_MS[data.outcome] || 4000), label, color: tone.color });
      showResult(data);
      updateCounts(data.counts);
      if (data.outcome === "checked_in" && data.record) renderFeed([data.record], { markNew: true });
      if (data.outcome === "closed") { session.is_open = false; stop("Session closed"); }
      setHud(data.message || "Done", data.outcome === "checked_in" ? "ok" : "warn");
    } catch (err) {
      if (err.status === 401) { toast("Your login expired. Sign in again to keep scanning.", "error", 8000); stop("Signed out"); }
      else { setHud("Connection problem — retrying", "err"); toast(err.message, "error"); await sleep(1500); }
    } finally {
      inFlight = false;
    }
  }

  function stop(reason) {
    running = false;
    stage.dataset.state = "paused";
    setHud(reason, "err");
  }

  // ======================================================================
  // Camera
  // ======================================================================
  async function startCamera(deviceId) {
    setHud("Starting camera…");
    await FaceEngine.startCamera(video, { deviceId });
    const cams = await FaceEngine.listCameras();
    const current = video.srcObject?.getVideoTracks()[0]?.getSettings().deviceId;
    cameraSelect.innerHTML = cams.map((c, i) =>
      `<option value="${escape(c.deviceId)}" ${c.deviceId === current ? "selected" : ""}>${escape(c.label || `Camera ${i + 1}`)}</option>`
    ).join("") || '<option value="">Default camera</option>';
    if (deviceId) localStorage.setItem("scansync-camera", deviceId);
  }

  cameraSelect.addEventListener("change", async () => {
    try { await startCamera(cameraSelect.value); } catch (err) { toast(err.message, "error"); }
  });

  // ======================================================================
  // Controls
  // ======================================================================
  const btnSound = $("#btn-sound");
  syncToggle(btnSound, soundOn);
  btnSound.addEventListener("click", () => {
    soundOn = !soundOn;
    localStorage.setItem("scansync-sound", soundOn ? "on" : "off");
    syncToggle(btnSound, soundOn);
    if (soundOn) beep("soft");
  });

  const btnPause = $("#btn-pause");
  btnPause.addEventListener("click", () => {
    paused = !paused;
    syncToggle(btnPause, !paused);
    btnPause.title = paused ? "Resume scanning" : "Pause scanning";
    stage.dataset.state = paused ? "paused" : "scanning";
    if (paused) { FaceEngine.syncCanvas(overlay, video); setHud("Paused", "warn"); }
  });

  $("#btn-fullscreen").addEventListener("click", () => {
    const el = $("#scanner");
    document.fullscreenElement ? document.exitFullscreen() : el.requestFullscreen?.().catch(() => {});
  });

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    if (e.code === "Space") { e.preventDefault(); btnPause.click(); }
    if (e.key === "f") $("#btn-fullscreen").click();
    if (e.key === "m") btnSound.click();
  });

  $("#loader-retry").addEventListener("click", boot);

  // ---- Background sync (other devices, manual overrides) -----------------
  async function poll() {
    try {
      const snap = await api(cfg.statusUrl);
      clockOffset = Date.parse(snap.server_time) - Date.now();
      session = snap.session;
      updateCounts(snap.counts);
      renderFeed(snap.recent, { markNew: true });
      if (!session.is_open && running) stop("Session closed");
    } catch { /* ignore transient errors */ }
  }

  // ======================================================================
  // Boot
  // ======================================================================
  async function boot() {
    renderFeed(cfg.snapshot.recent);
    updateCounts(cfg.snapshot.counts);
    tickCountdown();

    if (!session.is_open) {
      stage.dataset.state = "paused";
      setLoader("This session is closed", "Re-open it to accept late arrivals.", 1, { error: true });
      $("#loader-retry").classList.add("hidden");
      setHud("Session closed", "err");
      return;
    }

    try {
      setLoader("Preparing face recognition", "Loading models…", 0);
      await FaceEngine.load((f, label) => setLoader("Preparing face recognition", label, f * 0.8));
      setLoader("Starting camera", "Allow camera access if your browser asks.", 0.9);
      await startCamera(localStorage.getItem("scansync-camera") || undefined).catch(async (err) => {
        // A saved camera may have been unplugged — fall back to the default once.
        if (localStorage.getItem("scansync-camera")) { localStorage.removeItem("scansync-camera"); return startCamera(); }
        throw err;
      });
      $("#loader-bar").style.width = "100%";
      loader.classList.add("is-done");
      stage.dataset.state = "scanning";
      setHud("Ready — look at the camera", "ok");
      if (!running) loop();
    } catch (err) {
      console.error(err);
      stage.dataset.state = "error";
      setLoader("Scanner unavailable", err.message, null, { error: true });
      setHud("Error", "err");
    }
  }

  setInterval(tickCountdown, 1000);
  setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  addEventListener("pagehide", () => FaceEngine.stopCamera(video));

  // face-api.js is loaded with `defer` just before this file.
  boot();
})();
