/* ==========================================================================
   ScanSync — student face enrolment
   Guided multi-angle capture → POST JSON {student, samples:[{image, descriptor}]}
   ========================================================================== */
(() => {
  "use strict";
  const { $, $$, api, toast, escape } = window.ScanSync;
  const cfg = JSON.parse($("#register-config").textContent);

  // Guided 4-step poses: front -> slightly left -> slightly right -> front again
  const POSES = [
    { prompt: "Look straight at the camera (Front)", ok: (q) => Math.abs(q.yaw) < 0.15 },
    { prompt: "Look slightly to your left", ok: (q) => q.yaw > 0.12 && q.yaw < 0.50 },
    { prompt: "Look slightly to your right", ok: (q) => q.yaw < -0.12 && q.yaw > -0.50 },
    { prompt: "Look straight at the camera again (Front)", ok: (q) => Math.abs(q.yaw) < 0.15 },
  ];
  const HOLD_FRAMES = 4;          // consecutive good frames before auto-capture
  const CAPTURE_GAP_MS = 700;     // pause between captures
  const SAME_PERSON = 0.55;       // every sample must stay this close to the running mean
  const MIN_FACE = 0.2;

  const capture = $("#capture");
  const video = $("#register-video");
  const overlay = $("#register-overlay");
  const prompt = $("#capture-prompt");
  const btnCapture = $("#btn-capture");
  const btnReset = $("#btn-reset");
  const btnSubmit = $("#btn-submit");
  const btnStart = $("#btn-start-camera");
  const autoToggle = $("#auto-capture");
  const thumbs = $("#thumbs");
  const form = $("#student-form");

  let samples = [];               // [{image, descriptor}]
  let latest = null;              // most recent single good detection
  let holdCount = 0;
  let lastCaptureAt = 0;
  let running = false;
  let submitting = false;

  // ---- UI ----------------------------------------------------------------
  function render() {
    const n = samples.length;
    $("#capture-count").textContent = `${n} / ${cfg.maxSamples}`;
    $$("#capture-steps li").forEach((li, i) => {
      li.className = i < n ? "is-done" : i === n && running ? "is-current" : "";
    });
    thumbs.innerHTML = samples.map((s, i) => `
      <div class="thumb"><img src="${s.image}" alt="Face sample ${i + 1}"><span>#${i + 1}</span>
        <button type="button" data-remove="${i}" aria-label="Remove sample ${i + 1}">✕</button></div>`).join("");
    const need = 4 - n;
    $("#capture-hint").textContent = n >= 4 ? "All 4 samples captured ✓"
      : need > 0 ? `${need} more sample${need === 1 ? "" : "s"} required` : "Ready";
    btnReset.disabled = n === 0;
    btnCapture.disabled = !running || n >= 4;
    btnSubmit.disabled = n < 4 || submitting;
    if (running) prompt.textContent = n >= 4 ? "Done! Review and save" : POSES[n % POSES.length].prompt;
  }

  thumbs.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove]");
    if (!btn) return;
    samples.splice(Number(btn.dataset.remove), 1);
    render();
  });

  btnReset.addEventListener("click", () => { samples = []; holdCount = 0; render(); });

  // ---- Capture -----------------------------------------------------------
  function takeSample(det, { manual = false } = {}) {
    if (samples.length >= cfg.maxSamples) return;
    const descriptor = Array.from(det.result.descriptor);
    if (samples.length) {
      const mean = FaceEngine.mean(samples.map((s) => s.descriptor));
      if (FaceEngine.distance(mean, descriptor) > SAME_PERSON) {
        if (manual) toast("That looks like a different person than the earlier samples.", "warning");
        prompt.textContent = "Different face detected — only one student please";
        holdCount = 0;
        return;
      }
    }
    samples.push({ image: FaceEngine.cropFace(video, det.q.box), descriptor });
    lastCaptureAt = performance.now();
    holdCount = 0;
    const shutter = $("#capture-shutter");
    shutter.classList.remove("is-on"); void shutter.offsetWidth; shutter.classList.add("is-on");
    render();
    if (samples.length === cfg.maxSamples) toast("All face samples captured. Review them and save.", "success");
  }

  btnCapture.addEventListener("click", () => {
    if (!latest) return toast("No clear face in view. Centre the face in the oval.", "warning");
    takeSample(latest, { manual: true });
  });

  async function loop() {
    while (running) {
      const t0 = performance.now();
      if (!document.hidden && video.readyState >= 2) {
        try { await frame(); } catch (err) { console.error(err); }
      }
      await new Promise((r) => setTimeout(r, Math.max(0, 110 - (performance.now() - t0))));
    }
  }

  async function frame() {
    const faces = await FaceEngine.detectAll(video, { inputSize: 416, scoreThreshold: 0.55 });
    const ctx = FaceEngine.syncCanvas(overlay, video);
    latest = null;
    const done = samples.length >= cfg.maxSamples;

    if (faces.length !== 1) {
      capture.dataset.quality = faces.length ? "bad" : "";
      holdCount = 0;
      if (!done) prompt.textContent = faces.length ? "Only one person in the frame, please" : "Position the face inside the oval";
      faces.forEach((f) => FaceEngine.drawBox(ctx, f.detection.box, { color: "#fbbf24" }));
      return;
    }

    const result = faces[0];
    const q = FaceEngine.analyze(result, video.videoWidth, video.videoHeight);
    let issue = null;
    if (q.size < MIN_FACE) issue = "Move a little closer";
    else if (q.size > 0.62) issue = "Move back slightly";
    else if (q.center > 0.22) issue = "Centre the face in the oval";
    else if (q.score < 0.7) issue = "Improve the lighting on the face";

    if (issue) {
      capture.dataset.quality = "bad";
      holdCount = 0;
      if (!done) prompt.textContent = issue;
      return;
    }

    latest = { result, q };
    if (done) { capture.dataset.quality = "good"; return; }

    const pose = POSES[samples.length % POSES.length];
    const poseOk = pose.ok(q);
    capture.dataset.quality = poseOk ? "good" : "";
    prompt.textContent = poseOk ? (autoToggle.checked ? "Hold still…" : "Good — press Capture") : pose.prompt;

    if (autoToggle.checked && poseOk && performance.now() - lastCaptureAt > CAPTURE_GAP_MS) {
      if (++holdCount >= HOLD_FRAMES) takeSample(latest);
    } else if (!poseOk) {
      holdCount = 0;
    }
  }

  // ---- Camera start ------------------------------------------------------
  async function start() {
    const loader = $("#register-loader");
    const setStatus = (title, text, f) => {
      $("#register-loader-title").textContent = title;
      $("#register-loader-text").textContent = text;
      if (f != null) $("#register-loader-bar").style.width = `${Math.round(f * 100)}%`;
    };
    btnStart.disabled = true;
    btnStart.innerHTML = '<span class="spinner"></span> Starting…';
    try {
      await FaceEngine.load((f, label) => setStatus("Loading face models", label, f * 0.8));
      setStatus("Starting camera", "Allow camera access if your browser asks.", 0.9);
      await FaceEngine.startCamera(video, { deviceId: localStorage.getItem("scansync-camera") || undefined })
        .catch(() => FaceEngine.startCamera(video));
      loader.classList.add("is-done");
      running = true;
      render();
      loop();
    } catch (err) {
      setStatus("Couldn't start", err.message, 0);
      btnStart.disabled = false;
      btnStart.textContent = "Try again";
    }
  }
  btnStart.addEventListener("click", start);
  addEventListener("pagehide", () => { running = false; FaceEngine.stopCamera(video); });

  // ---- Submit ------------------------------------------------------------
  function clearErrors() {
    $$(".field.has-error").forEach((f) => f.classList.remove("has-error"));
    $$(".field .errorlist").forEach((ul) => (ul.innerHTML = ""));
  }

  function showFieldErrors(fields) {
    for (const [name, errors] of Object.entries(fields || {})) {
      const field = $(`[data-field="${name}"]`);
      if (!field) { toast(errors.join(" "), "error"); continue; }
      field.classList.add("has-error");
      field.querySelector(".errorlist").innerHTML = errors.map((e) => `<li>${escape(e)}</li>`).join("");
    }
    $(".field.has-error input, .field.has-error select")?.focus();
  }

  btnSubmit.addEventListener("click", async () => {
    if (samples.length < cfg.minSamples) return;
    clearErrors();
    const body = { samples };
    if (cfg.mode === "create") {
      body.student = Object.fromEntries(new FormData(form));
      const accToggle = document.getElementById("create-account-toggle");
      if (accToggle && accToggle.checked) {
        body.account = {
          create: true,
          username: document.getElementById("acc_username")?.value.trim() || body.student.student_id,
          password: document.getElementById("acc_password")?.value || "student123",
        };
      }
    } else {
      body.replace = true;
    }
    submitting = true;
    const label = btnSubmit.innerHTML;
    btnSubmit.disabled = true;
    btnSubmit.innerHTML = '<span class="spinner"></span> Saving…';
    try {
      const data = await api(cfg.apiUrl, { method: "POST", body });
      running = false;
      FaceEngine.stopCamera(video);
      toast(cfg.mode === "create" ? `${data.student.name} registered.` : "Face samples updated.", "success");
      setTimeout(() => (location.href = data.redirect), 600);
      return;
    } catch (err) {
      if (err.data?.fields) showFieldErrors(err.data.fields);
      toast(err.message, err.status === 409 ? "warning" : "error", 7000);
    }
    submitting = false;
    btnSubmit.innerHTML = label;
    render();
  });

  // Enter in the form should not reload the page.
  form?.addEventListener("submit", (e) => e.preventDefault());

  render();
})();
