/* ==========================================================================
   ScanSync — Student Self-Scanner
   Student's personal webcam → FaceEngine → POST /api/sessions/<id>/student-scan/
   Validates student's biometric identity and logs attendance directly
   ========================================================================== */
(() => {
  "use strict";
  const { $, $$, api, toast, escape } = window.ScanSync;
  const cfg = JSON.parse($("#student-scanner-config").textContent);

  const FRAME_MS = 100;
  const STABLE_FRAMES = 3;
  const MIN_FACE = 0.16;
  const MAX_YAW = 0.35;

  const stage = $("#stage");
  const video = $("#scanner-video");
  const overlay = $("#scanner-overlay");
  const flash = $("#scanner-flash");
  const hud = $("#hud-status");
  const loader = $("#stage-loader");
  const result = $("#result");

  let running = false;
  let inFlight = false;
  let track = [];
  let audio;

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

  function beep(kind) {
    try {
      audio ||= new (window.AudioContext || window.webkitAudioContext)();
      const notes = { success: [880, 1320], late: [660, 660], error: [220, 180] }[kind] || [600];
      notes.forEach((freq, i) => {
        const t = audio.currentTime + i * 0.13;
        const osc = audio.createOscillator();
        const gain = audio.createGain();
        osc.type = kind === "error" ? "square" : "sine";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, t);
        gain.gain.exponentialRampToValueAtTime(kind === "error" ? 0.08 : 0.18, t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
        osc.connect(gain).connect(audio.destination);
        osc.start(t); osc.stop(t + 0.2);
      });
    } catch { /* audio unavailable */ }
  }

  function showResult(data) {
    const outcome = data.outcome;
    const rec = data.record;
    const s = data.student;

    result.dataset.outcome = outcome;
    result.dataset.status = rec?.status || "";
    $("#result-name").textContent = s ? s.name : "Unverified Face";
    $("#result-meta").textContent = s ? `${s.student_id} · ${s.section}` : "";
    $("#result-msg").textContent = data.message || "";
    $("#result-time").textContent = rec?.time_label || new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

    const badge = $("#result-badge");
    badge.textContent = rec ? rec.status_label : (outcome === "duplicate" ? "Already Checked In" : "Unverified");
    result.classList.add("is-visible");

    const isSuccess = outcome === "checked_in" || outcome === "duplicate";
    const flashColor = outcome === "checked_in"
      ? (rec?.status === "LATE" ? "rgba(245, 158, 11, .55)" : "rgba(16, 185, 129, .55)")
      : (outcome === "duplicate" ? "rgba(91, 95, 244, .45)" : "rgba(244, 63, 94, .55)");

    flash.style.setProperty("--flash", flashColor);
    flash.classList.remove("is-on"); void flash.offsetWidth; flash.classList.add("is-on");
    beep(isSuccess ? (rec?.status === "LATE" ? "late" : "success") : "error");

    if (isSuccess) {
      stage.dataset.state = "success";
      setHud("Verified! Redirecting to dashboard…", "ok");
      setTimeout(() => {
        window.location.href = cfg.redirectUrl;
      }, 2400);
    } else {
      stage.dataset.state = "error";
      setHud(data.message || "Face not recognised", "err");
      setTimeout(() => {
        result.classList.remove("is-visible");
        stage.dataset.state = "scanning";
        setHud("Position your face in the frame", "ok");
      }, 3000);
    }
  }

  async function loop() {
    running = true;
    while (running) {
      const t0 = performance.now();
      if (!inFlight && !document.hidden && video.readyState >= 2) {
        try { await frame(); } catch (err) { console.error(err); }
      }
      await new Promise((r) => setTimeout(r, Math.max(0, FRAME_MS - (performance.now() - t0))));
    }
  }

  async function frame() {
    const faces = await FaceEngine.detectAll(video, { inputSize: 320, scoreThreshold: 0.55 });
    const ctx = FaceEngine.syncCanvas(overlay, video);
    const W = video.videoWidth, H = video.videoHeight;

    if (!faces.length) {
      track = [];
      if (!result.classList.contains("is-visible")) stage.dataset.state = "scanning";
      setHud("Position your face inside the frame", "ok");
      return;
    }

    if (faces.length > 1) {
      track = [];
      setHud("Only you should be in view", "warn");
      faces.forEach((f) => FaceEngine.drawBox(ctx, f.detection.box, { color: "#fbbf24" }));
      return;
    }

    const primary = faces[0];
    const q = FaceEngine.analyze(primary, W, H);
    const desc = Array.from(primary.descriptor);

    let hint = null;
    if (q.size < MIN_FACE) hint = "Move a little closer";
    else if (Math.abs(q.yaw) > MAX_YAW) hint = "Face the camera directly";
    else if (q.center > 0.32) hint = "Centre your face in frame";

    if (hint) {
      track = [];
      FaceEngine.drawBox(ctx, q.box, { color: "#fbbf24" });
      setHud(hint, "warn");
      return;
    }

    stage.dataset.state = "detecting";
    if (track.length && FaceEngine.distance(FaceEngine.mean(track), desc) > 0.45) track = [];
    track.push(desc);

    const progress = Math.min(track.length / STABLE_FRAMES, 1);
    FaceEngine.drawBox(ctx, q.box, {
      color: "#22d3ee",
      label: inFlight ? "Verifying…" : `Hold still ${"●".repeat(track.length)}${"○".repeat(Math.max(0, STABLE_FRAMES - track.length))}`
    });
    setHud("Hold still… scanning", "ok");

    if (progress >= 1 && !inFlight) {
      const descriptor = FaceEngine.mean(track);
      track = [];
      submit(descriptor);
    }
  }

  async function submit(descriptor) {
    inFlight = true;
    setHud("Verifying your identity…");
    try {
      const data = await api(cfg.scanUrl, { method: "POST", body: { descriptor } });
      showResult(data);
      if (data.outcome === "checked_in" || data.outcome === "duplicate") {
        running = false;
      }
    } catch (err) {
      toast(err.message || "Failed to submit attendance scan.", "error");
      setHud("Scan failed — try again", "err");
      stage.dataset.state = "error";
      beep("error");
      setTimeout(() => {
        stage.dataset.state = "scanning";
        setHud("Position your face in the frame", "ok");
      }, 2500);
    } finally {
      inFlight = false;
    }
  }

  async function boot() {
    try {
      setLoader("Preparing camera", "Loading recognition models…", 0);
      await FaceEngine.load((f, label) => setLoader("Preparing camera", label, f * 0.8));
      setLoader("Starting camera", "Allow camera access in your browser.", 0.9);
      await FaceEngine.startCamera(video);
      loader.classList.add("is-done");
      stage.dataset.state = "scanning";
      setHud("Ready — look at the camera", "ok");
      loop();
    } catch (err) {
      console.error(err);
      stage.dataset.state = "error";
      setLoader("Camera unavailable", err.message, null, { error: true });
      setHud("Camera error", "err");
    }
  }

  $("#loader-retry")?.addEventListener("click", boot);
  addEventListener("pagehide", () => {
    running = false;
    FaceEngine.stopCamera(video);
  });

  boot();
})();
