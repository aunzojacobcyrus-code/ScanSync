"""
ScanSync business logic.

Views stay thin; everything that decides *who* a face is, *whether* a scan
counts, and *what status* a student gets lives here so it is unit-testable.
"""
import base64
import binascii
import math
from dataclasses import dataclass

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import AttendanceRecord, AttendanceSession, FaceSample, SchoolClass, Student

DESCRIPTOR_LENGTH = 128


def cfg(key):
    return settings.SCANSYNC[key]


# --------------------------------------------------------------------------- #
# Permissions & Visibility
# --------------------------------------------------------------------------- #
def is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def is_student(user):
    return (
        user.is_authenticated
        and hasattr(user, "student_profile")
        and user.student_profile is not None
        and user.student_profile.is_active
    )


def visible_classes(user):
    """Administrators see every class; teachers see classes they teach; students see classes of their section or where enrolled."""
    qs = SchoolClass.objects.select_related("section", "teacher")
    if is_admin(user):
        return qs
    if is_student(user):
        return qs.filter(
            Q(section=user.student_profile.section) | Q(students=user.student_profile),
            is_active=True,
        ).distinct()
    return qs.filter(teacher=user)


def visible_sessions(user):
    return AttendanceSession.objects.filter(school_class__in=visible_classes(user))


def visible_records(user):
    if is_student(user):
        return AttendanceRecord.objects.filter(student=user.student_profile)
    return AttendanceRecord.objects.filter(session__school_class__in=visible_classes(user))


# --------------------------------------------------------------------------- #
# Descriptor validation & matching
# --------------------------------------------------------------------------- #
class InvalidDescriptor(ValueError):
    pass


def clean_descriptor(raw):
    """Validate a descriptor coming from the browser → list of 128 finite floats."""
    if not isinstance(raw, (list, tuple)) or len(raw) != DESCRIPTOR_LENGTH:
        raise InvalidDescriptor(f"Descriptor must be a list of {DESCRIPTOR_LENGTH} numbers.")
    try:
        values = [float(v) for v in raw]
    except (TypeError, ValueError):
        raise InvalidDescriptor("Descriptor contains non-numeric values.")
    if not all(math.isfinite(v) for v in values):
        raise InvalidDescriptor("Descriptor contains invalid numbers.")
    return values


@dataclass
class Match:
    student: Student
    distance: float


def find_best_match(descriptor, students=None, threshold=None):
    """
    Nearest-neighbour search over every stored face sample.

    For a classroom (~50 students × ~5 samples) a pure-Python scan is
    sub-millisecond, so no vector index is needed.
    Returns ``Match`` or ``None`` when nobody is within ``threshold``.
    """
    threshold = cfg("MATCH_THRESHOLD") if threshold is None else threshold
    samples = FaceSample.objects.filter(student__is_active=True)
    if students is not None:
        samples = samples.filter(student__in=students)

    best_by_student = {}
    for student_pk, stored in samples.values_list("student_id", "descriptor").iterator():
        if not isinstance(stored, list) or len(stored) != DESCRIPTOR_LENGTH:
            continue
        d = math.dist(descriptor, stored)
        if d < best_by_student.get(student_pk, math.inf):
            best_by_student[student_pk] = d

    if not best_by_student:
        return None
    student_pk, distance = min(best_by_student.items(), key=lambda kv: kv[1])
    if distance > threshold:
        return None
    return Match(student=Student.objects.select_related("section").get(pk=student_pk), distance=distance)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
class InvalidSample(ValueError):
    pass


def decode_data_url(data_url):
    """``data:image/jpeg;base64,...`` → ContentFile (JPEG/PNG/WebP only)."""
    if not isinstance(data_url, str) or "," not in data_url:
        raise InvalidSample("Image must be a base64 data URL.")
    header, b64 = data_url.split(",", 1)
    if not header.startswith("data:image/") or ";base64" not in header:
        raise InvalidSample("Unsupported image encoding.")
    if header[len("data:image/"):].split(";")[0] not in {"jpeg", "jpg", "png", "webp"}:
        raise InvalidSample("Only JPEG, PNG or WebP images are accepted.")
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidSample("Image data is corrupted.")
    if len(data) > 3 * 1024 * 1024:
        raise InvalidSample("Each face image must be under 3 MB.")
    return ContentFile(data, name="face.jpg")


def clean_samples(raw_samples):
    """Validate the list of ``{image, descriptor}`` objects posted by register.js."""
    if not isinstance(raw_samples, list):
        raise InvalidSample("Face samples are missing.")
    lo, hi = cfg("MIN_FACE_SAMPLES"), cfg("MAX_FACE_SAMPLES")
    if not lo <= len(raw_samples) <= hi:
        raise InvalidSample(f"Capture between {lo} and {hi} face samples.")
    cleaned = []
    for i, s in enumerate(raw_samples, 1):
        if not isinstance(s, dict):
            raise InvalidSample(f"Sample {i} is malformed.")
        try:
            cleaned.append((decode_data_url(s.get("image")), clean_descriptor(s.get("descriptor"))))
        except (InvalidDescriptor, InvalidSample) as exc:
            raise InvalidSample(f"Sample {i}: {exc}")
    return cleaned


def find_face_conflict(descriptors, exclude_student=None):
    """Is this face already enrolled under a *different* student?"""
    others = Student.objects.filter(is_active=True)
    if exclude_student is not None:
        others = others.exclude(pk=exclude_student.pk)
    for d in descriptors:
        match = find_best_match(d, students=others)
        if match:
            return match
    return None


@transaction.atomic
def save_face_samples(student, samples, replace=False):
    if replace:
        for old in student.face_samples.all():
            old.image.delete(save=False)
            old.delete()
    created = []
    for content, descriptor in samples:
        fs = FaceSample(student=student, descriptor=descriptor)
        fs.image.save(content.name, content, save=False)
        fs.save()
        created.append(fs)
    if created and (replace or not student.photo):
        first = created[0]
        first.image.open("rb")
        student.photo.save(f"{student.student_id}.jpg", ContentFile(first.image.read()), save=True)
        first.image.close()
    return created


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #
def open_session(school_class, user, date=None):
    """Open (or re-open) today's session for a class, snapshotting its rules."""
    date = date or timezone.localdate()
    session, created = AttendanceSession.objects.get_or_create(
        school_class=school_class,
        date=date,
        defaults={
            "start_time": school_class.start_time,
            "grace_period_minutes": school_class.grace_period_minutes,
            "absent_after_minutes": school_class.absent_after_minutes,
            "opened_by": user,
        },
    )
    if not created and session.closed_at is not None:
        session.closed_at = None
        session.save(update_fields=["closed_at"])
    return session, created


@transaction.atomic
def close_session(session):
    """
    End scanning and tag every roster student who never scanned as ABSENT.
    Returns the number of absences created.
    """
    scanned = session.records.values_list("student_id", flat=True)
    missing = session.school_class.roster().exclude(pk__in=scanned)
    AttendanceRecord.objects.bulk_create(
        [
            AttendanceRecord(
                session=session, student=s, status=AttendanceRecord.Status.ABSENT,
                method=AttendanceRecord.Method.AUTO, note="No scan before session closed",
            )
            for s in missing
        ],
        ignore_conflicts=True,
    )
    session.closed_at = timezone.now()
    session.save(update_fields=["closed_at"])
    return len(missing)


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
class ScanOutcome:
    CHECKED_IN = "checked_in"
    DUPLICATE = "duplicate"
    UNKNOWN = "unknown"
    NOT_ENROLLED = "not_enrolled"
    CLOSED = "closed"


def record_scan(session, student, distance=None, when=None):
    """
    Log a recognised student's arrival with duplicate-scan prevention.

    Returns ``(record, outcome)`` where outcome is CHECKED_IN or DUPLICATE.
    A student who was auto-marked ABSENT (session closed then re-opened) is
    upgraded on their first real scan instead of being treated as a duplicate.
    """
    when = when or timezone.now()
    status = session.status_for(when)
    try:
        with transaction.atomic():
            record, created = AttendanceRecord.objects.get_or_create(
                session=session,
                student=student,
                defaults={
                    "status": status, "time_in": when,
                    "method": AttendanceRecord.Method.FACE, "match_distance": distance,
                },
            )
    except IntegrityError:
        # Two scans raced past get_or_create; the other one won.
        return AttendanceRecord.objects.get(session=session, student=student), ScanOutcome.DUPLICATE

    if created:
        return record, ScanOutcome.CHECKED_IN

    if record.time_in is None and record.method == AttendanceRecord.Method.AUTO:
        record.status, record.time_in = status, when
        record.method, record.match_distance, record.note = AttendanceRecord.Method.FACE, distance, ""
        record.save()
        return record, ScanOutcome.CHECKED_IN

    return record, ScanOutcome.DUPLICATE


def process_scan(session, descriptor):
    """Full pipeline for one descriptor sent by the live scanner."""
    if not session.is_open:
        return {"outcome": ScanOutcome.CLOSED, "message": "This session is closed."}

    match = find_best_match(descriptor)
    if match is None:
        return {"outcome": ScanOutcome.UNKNOWN, "message": "Face not recognised. Please register first."}

    student = match.student
    payload = {"student": serialize_student(student), "distance": round(match.distance, 4)}

    if student.section_id != session.school_class.section_id:
        payload.update(
            outcome=ScanOutcome.NOT_ENROLLED,
            message=f"{student.first_name} is in {student.section}, not {session.school_class.section}.",
        )
        return payload

    record, outcome = record_scan(session, student, distance=match.distance)
    payload["record"] = serialize_record(record)
    payload["outcome"] = outcome
    if outcome == ScanOutcome.DUPLICATE:
        payload["message"] = f"Already checked in at {timezone.localtime(record.time_in):%I:%M %p}." \
            if record.time_in else "Already recorded for this session."
    else:
        payload["message"] = {
            AttendanceRecord.Status.PRESENT: "Welcome! You're marked Present.",
            AttendanceRecord.Status.LATE: "Checked in — marked Late.",
            AttendanceRecord.Status.ABSENT: "Arrived past the cut-off — marked Absent.",
        }[record.status]
    return payload


def process_student_scan(session, student, descriptor):
    """Personal scan pipeline for an authenticated student scanning from their dashboard."""
    if not session.is_open:
        return {"outcome": ScanOutcome.CLOSED, "message": "This session is currently closed."}

    if student.section_id != session.school_class.section_id:
        return {
            "outcome": ScanOutcome.NOT_ENROLLED,
            "message": f"You are in {student.section}, but this class is for {session.school_class.section}.",
        }

    # Verify that the captured face matches THIS student's enrolled profile
    match = find_best_match(descriptor, students=[student])
    if match is None:
        return {
            "outcome": ScanOutcome.UNKNOWN,
            "message": "Face did not match your enrolled profile. Please ensure good lighting and look directly into the camera.",
        }

    record, outcome = record_scan(session, student, distance=match.distance)
    payload = {
        "student": serialize_student(student),
        "distance": round(match.distance, 4),
        "record": serialize_record(record),
        "outcome": outcome,
    }
    if outcome == ScanOutcome.DUPLICATE:
        payload["message"] = f"Already checked in at {timezone.localtime(record.time_in):%I:%M %p}." \
            if record.time_in else "Already recorded for this session."
    else:
        payload["message"] = {
            AttendanceRecord.Status.PRESENT: "Attendance confirmed! You are marked Present.",
            AttendanceRecord.Status.LATE: "Attendance confirmed — marked Late.",
            AttendanceRecord.Status.ABSENT: "Session cut-off passed — marked Absent.",
        }[record.status]
    return payload


# --------------------------------------------------------------------------- #
# Read models / serialisers
# --------------------------------------------------------------------------- #
def serialize_student(student):
    return {
        "id": student.pk,
        "student_id": student.student_id,
        "name": student.full_name,
        "first_name": student.first_name,
        "initials": student.initials,
        "section": str(student.section),
        "photo": student.photo.url if student.photo else None,
    }


def serialize_record(record):
    t = timezone.localtime(record.time_in) if record.time_in else None
    return {
        "id": record.pk,
        "status": record.status,
        "status_label": record.get_status_display(),
        "time_in": t.isoformat() if t else None,
        "time_label": t.strftime("%I:%M:%S %p").lstrip("0") if t else "—",
        "method": record.method,
        "student": serialize_student(record.student),
    }


def session_counts(session):
    roster_total = session.school_class.roster().count()
    agg = session.records.aggregate(
        present=Count("id", filter=Q(status=AttendanceRecord.Status.PRESENT)),
        late=Count("id", filter=Q(status=AttendanceRecord.Status.LATE)),
        absent=Count("id", filter=Q(status=AttendanceRecord.Status.ABSENT)),
    )
    arrived = agg["present"] + agg["late"]
    agg.update(
        roster=roster_total,
        remaining=max(roster_total - arrived - agg["absent"], 0),
        rate=round(arrived / roster_total * 100) if roster_total else 0,
    )
    return agg


def session_snapshot(session, limit=25):
    recent = (
        session.records.filter(time_in__isnull=False)
        .select_related("student__section")
        .order_by("-time_in")[:limit]
    )
    return {
        "session": {
            "id": session.pk,
            "is_open": session.is_open,
            "date": session.date.isoformat(),
            "class": session.school_class.label,
            "start_time": session.start_datetime.isoformat(),
            "grace_deadline": session.grace_deadline.isoformat(),
            "absent_deadline": session.absent_deadline.isoformat() if session.absent_deadline else None,
        },
        "counts": session_counts(session),
        "recent": [serialize_record(r) for r in recent],
        "server_time": timezone.localtime().isoformat(),
    }
