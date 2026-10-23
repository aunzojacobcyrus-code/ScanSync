# ScanSync 📸⚡

**Classroom-Scale Facial Recognition Attendance System**
*A Repository for ScanSync Mini Capstone*

ScanSync is a modern, web-based attendance tracker engineered for schools and universities. Students simply look at a webcam — ScanSync recognises them in real-time, validates class enrollment, tags each arrival as **Present**, **Late**, or **Absent** according to customizable schedule rules, and prevents duplicate check-ins.

---

## ✨ Features

- **Real-Time Facial Recognition**:
  - TinyFaceDetector + FaceLandmark68 + FaceRecognitionNet powered by `@vladmandic/face-api` (TensorFlow.js WebGL).
  - Browser calculates 128-dimensional biometric embeddings client-side; server performs Euclidean matching in sub-millisecond time.
- **Automated Status Tagging**:
  - Class start time, grace period (e.g. 15 mins), and cut-off absent window (e.g. 45 mins).
  - Snapshotting rules ensure historic records remain intact when schedules change.
- **Duplicate Prevention**:
  - Database-level unique constraints (`session`, `student`) plus multi-scan detection with audio & visual feedback.
- **Guided Student Enrolment**:
  - Interactive multi-pose guidance (straight, left, right, smile) with auto-capture and consistency checks.
- **Live Kiosk Scanner**:
  - Fullscreen kiosk mode with mirror video, corner brackets, live check-in ticker, audio alerts, and countdown clocks.
- **Reports & Analytics**:
  - 7-day attendance trends, KPI cards, filterable logs, and one-click Excel (`.xlsx`) & CSV exports.
- **Dual Themes & Responsive UI**:
  - SaaS aesthetic in Vanilla CSS with automatic Dark/Light mode switching.

---

## 🚀 Quickstart

### 1. Requirements
- Python 3.10+ (virtual environment recommended)
- A modern web browser with webcam access (Chrome, Edge, Firefox, Safari)

### 2. Installation

Activate your virtual environment and install the dependencies:

```bash
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Database & Migrations

```bash
python manage.py migrate
```

### 4. Seed Demo Data

Seed sample sections, classes, and initial user accounts:

```bash
python manage.py seed_demo
```

This creates:
- **Admin account**: `admin` / `admin123`
- **Teacher account**: `teacher` / `teacher123`
- **Student account**: `student` / `student123` (Juan Dela Cruz, BSIS 2-A)
- **Sections**: `BSIS 2-A`, `BSIT 3-B`
- **Classes**: `IS 103` (Logic Formulation), `IT 301` (Systems Integration)
- **Students**: 6 sample roster students ready for webcam enrolment

### 5. Run the Server

```bash
python manage.py runserver
```

Open your browser to: **[http://localhost:8000](http://localhost:8000)**

---

## 🔒 Webcam Permissions & HTTPS

Modern browsers require a **secure context** to grant webcam access (`getUserMedia`):
- `http://localhost:8000` or `http://127.0.0.1:8000` is trusted by default.
- If accessing from another machine on your local network (e.g. tablet or kiosk terminal at `http://192.168.x.x`), you must serve over HTTPS or configure browser flags (`chrome://flags/#unsafely-treat-insecure-origin-as-secure`).

---

## 🌐 Running Fully Offline

By default, ScanSync loads `face-api.js` and neural net model weights via fast CDN. To operate in air-gapped classrooms:

1. Download the weights from `@vladmandic/face-api` into `static/vendor/models/`.
2. Download `face-api.js` into `static/vendor/face-api.js`.
3. Set environment variables in your `.env` or system:
   ```bash
   SCANSYNC_FACE_API_JS=/static/vendor/face-api.js
   SCANSYNC_FACE_MODEL_URL=/static/vendor/models/
   ```

---

## 🧪 Running Tests

To verify models, duplicate prevention, and template rendering:

```bash
python manage.py test
```
>>>>>>> 52a2b17 (Initial commit: ScanSync facial recognition attendance system)
