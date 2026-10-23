/* ==========================================================================
   ScanSync — FaceEngine
   Thin wrapper around face-api.js (TensorFlow.js) + the browser webcam API.

   * Models: TinyFaceDetector (fast detection), FaceLandmark68 (alignment),
     FaceRecognitionNet (128-d descriptor).
   * The browser only *describes* faces. Identity matching, status tagging and
     duplicate prevention happen in Django (attendance/services.py).
   ========================================================================== */
window.FaceEngine = (() => {
  "use strict";

  const cfg = document.documentElement.dataset;
  const MODEL_URL = cfg.faceModelUrl || "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.15/model/";
  let loading = null;

  /** Load the three networks once; `onProgress(fraction, label)` drives the loader UI. */
  function load(onProgress) {
    if (loading) return loading;
    loading = (async () => {
      if (!window.faceapi) {
        throw new Error("face-api.js could not be loaded. Check the internet connection or vendor the library locally (see README).");
      }
      try { await faceapi.tf.setBackend("webgl"); } catch { /* falls back to cpu/wasm */ }
      await faceapi.tf.ready();
      const nets = [
        ["tinyFaceDetector", "Face detector"],
        ["faceLandmark68Net", "Landmark model"],
        ["faceRecognitionNet", "Recognition model"],
      ];
      for (let i = 0; i < nets.length; i++) {
        const [key, label] = nets[i];
        onProgress?.(i / nets.length, `Loading ${label.toLowerCase()}…`);
        if (!faceapi.nets[key].isLoaded) await faceapi.nets[key].loadFromUri(MODEL_URL);
      }
      onProgress?.(1, "Models ready");
    })().catch((err) => { loading = null; throw err; });
    return loading;
  }

  // ---- Camera ------------------------------------------------------------
  async function startCamera(video, { deviceId, width = 1280, height = 720 } = {}) {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("This browser cannot access the webcam here. Open ScanSync via http://localhost or HTTPS.");
    }
    stopCamera(video);
    const constraints = {
      audio: false,
      video: {
        width: { ideal: width }, height: { ideal: height },
        ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: "user" }),
      },
    };
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      throw new Error(cameraErrorMessage(err));
    }
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    await new Promise((resolve) => (video.readyState >= 2 ? resolve() : video.addEventListener("loadeddata", resolve, { once: true })));
    await video.play();
    return stream;
  }

  function stopCamera(video) {
    video?.srcObject?.getTracks().forEach((t) => t.stop());
    if (video) video.srcObject = null;
  }

  async function listCameras() {
    if (!navigator.mediaDevices?.enumerateDevices) return [];
    return (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  }

  function cameraErrorMessage(err) {
    switch (err?.name) {
      case "NotAllowedError":
      case "SecurityError": return "Camera permission was denied. Allow camera access in the browser's address bar and reload.";
      case "NotFoundError":
      case "OverconstrainedError": return "No webcam was found. Connect a camera to this computer and try again.";
      case "NotReadableError": return "The webcam is being used by another application (Zoom, Teams, Camera app…). Close it and retry.";
      default: return `Unable to start the webcam: ${err?.message || err}`;
    }
  }

  // ---- Detection ---------------------------------------------------------
  const options = (inputSize = 416, scoreThreshold = 0.5) =>
    new faceapi.TinyFaceDetectorOptions({ inputSize, scoreThreshold });

  /** All faces with landmarks + descriptors. */
  async function detectAll(input, { inputSize = 416, scoreThreshold = 0.5 } = {}) {
    return faceapi.detectAllFaces(input, options(inputSize, scoreThreshold)).withFaceLandmarks().withFaceDescriptors();
  }

  /**
   * Quality metrics for a single detection.
   *  size  — face width ÷ frame width (closeness)
   *  yaw   — −1..1 head turn estimated from nose vs jaw line (0 = frontal)
   *  center— distance of face centre from frame centre (0..~0.7)
   */
  function analyze(result, frameW, frameH) {
    const box = result.detection.box;
    const jaw = result.landmarks.getJawOutline();
    const nose = result.landmarks.getNose()[3];
    const left = jaw[0].x, right = jaw[16].x;
    const yaw = right - left > 0 ? ((nose.x - left) / (right - left) - 0.5) * 2 : 0;
    const cx = (box.x + box.width / 2) / frameW - 0.5;
    const cy = (box.y + box.height / 2) / frameH - 0.5;
    return { score: result.detection.score, size: box.width / frameW, yaw, center: Math.hypot(cx, cy), box };
  }

  const distance = (a, b) => {
    let s = 0;
    for (let i = 0; i < a.length; i++) { const d = a[i] - b[i]; s += d * d; }
    return Math.sqrt(s);
  };

  /** Element-wise mean of several descriptors (smooths single-frame noise). */
  function mean(descriptors) {
    const out = new Array(descriptors[0].length).fill(0);
    for (const d of descriptors) for (let i = 0; i < d.length; i++) out[i] += d[i];
    return out.map((v) => v / descriptors.length);
  }

  /** JPEG crop around a face box (padded, square) as a data URL. */
  function cropFace(video, box, { size = 320, pad = 0.6, quality = 0.88 } = {}) {
    const side = Math.max(box.width, box.height) * (1 + pad);
    const sx = Math.max(0, box.x + box.width / 2 - side / 2);
    const sy = Math.max(0, box.y + box.height / 2 - side / 2 - box.height * 0.08);
    const sw = Math.min(side, video.videoWidth - sx);
    const sh = Math.min(side, video.videoHeight - sy);
    const c = document.createElement("canvas");
    c.width = c.height = size;
    c.getContext("2d").drawImage(video, sx, sy, sw, sh, 0, 0, size, size);
    return c.toDataURL("image/jpeg", quality);
  }

  // ---- Overlay drawing (coordinates mirrored to match the selfie video) ---
  function syncCanvas(canvas, video) {
    if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
    }
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return ctx;
  }

  function drawBox(ctx, box, { color = "#22d3ee", label = "", mirror = true } = {}) {
    const W = ctx.canvas.width;
    const x = mirror ? W - box.x - box.width : box.x;
    const { y, width: w, height: h } = box;
    const r = Math.min(w, h) * 0.12;
    const len = Math.min(w, h) * 0.24;
    ctx.save();
    ctx.lineWidth = Math.max(3, W / 320);
    ctx.strokeStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur = 18;
    ctx.lineCap = "round";
    // corner brackets
    const corners = [
      [x, y + len, x, y + r, x + r, y, x + len, y],
      [x + w - len, y, x + w - r, y, x + w, y + r, x + w, y + len],
      [x + w, y + h - len, x + w, y + h - r, x + w - r, y + h, x + w - len, y + h],
      [x + len, y + h, x + r, y + h, x, y + h - r, x, y + h - len],
    ];
    for (const [ax, ay, bx, by, cx, cy, dx, dy] of corners) {
      ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.quadraticCurveTo(x === bx || x + w === bx ? bx : bx, by, cx, cy); ctx.lineTo(dx, dy); ctx.stroke();
    }
    ctx.globalAlpha = 0.08; ctx.fillStyle = color; ctx.fillRect(x, y, w, h); ctx.globalAlpha = 1;
    if (label) {
      ctx.shadowBlur = 0;
      const fs = Math.max(14, W / 50);
      ctx.font = `600 ${fs}px Inter, system-ui, sans-serif`;
      const tw = ctx.measureText(label).width + fs;
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.roundRect(x, y - fs * 2, tw, fs * 1.6, fs * 0.5); ctx.fill();
      ctx.fillStyle = "#0b0d1f"; ctx.fillText(label, x + fs / 2, y - fs * 0.85);
    }
    ctx.restore();
  }

  return { load, startCamera, stopCamera, listCameras, detectAll, analyze, distance, mean, cropFace, syncCanvas, drawBox };
})();
