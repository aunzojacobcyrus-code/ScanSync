"""
ScanSync data model.

    Section ──< Student ──< FaceSample            (who can be recognised)
       │
       └──< SchoolClass ──< AttendanceSession ──< AttendanceRecord >── Student
                                                  (one per student per session)

* A ``SchoolClass`` belongs to one ``Section``; its roster is every active
  student in that section.
* An ``AttendanceSession`` is one meeting of a class on one date. It snapshots
  the class start time and grace period so editing a class later never rewrites
  history.
* ``AttendanceRecord`` has a DB-level unique constraint on (session, student).
  That constraint, plus the service layer, provides duplicate-scan prevention.
"""
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone


# --------------------------------------------------------------------------- #
# Students & face profiles
# --------------------------------------------------------------------------- #
class Section(models.Model):
    """A block/section of students, e.g. ``BSIS 2-A``."""

    name = models.CharField(max_length=40, unique=True)
    program = models.CharField(max_length=120, blank=True, help_text="e.g. BS Information Systems")
    year_level = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(6)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


student_id_validator = RegexValidator(
    r"^[A-Za-z0-9\-]+$", "Student ID may only contain letters, numbers and dashes."
)


class Student(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="student_profile",
        help_text="Login user account for this student.",
    )
    student_id = models.CharField("Student ID", max_length=30, unique=True, validators=[student_id_validator])
    first_name = models.CharField(max_length=60)
    middle_name = models.CharField(max_length=60, blank=True)
    last_name = models.CharField(max_length=60)
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name="students")
    photo = models.ImageField(upload_to="faces/profile/", blank=True, help_text="Profile thumbnail (first face sample).")
    is_active = models.BooleanField(default=True, help_text="Archived students are not recognised or counted.")
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["section", "is_active"])]

    def __str__(self):
        return f"{self.full_name} ({self.student_id})"

    @property
    def full_name(self):
        middle = f" {self.middle_name[0]}." if self.middle_name else ""
        return f"{self.first_name}{middle} {self.last_name}"

    @property
    def sortable_name(self):
        return f"{self.last_name}, {self.first_name}"

    @property
    def initials(self):
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    def get_absolute_url(self):
        return reverse("attendance:student_detail", args=[self.pk])


def face_sample_upload_to(instance, filename):
    return f"faces/{instance.student.student_id}/{uuid.uuid4().hex}.jpg"


class FaceSample(models.Model):
    """
    One enrolment image plus its 128-dimension face descriptor.

    Descriptors are computed in the browser by face-api.js (a FaceNet-style
    ResNet-34 model) and stored as a JSON list of floats. Matching is a
    nearest-neighbour Euclidean search done server-side (see services.py).
    """

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="face_samples")
    image = models.ImageField(upload_to=face_sample_upload_to)
    descriptor = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Face sample #{self.pk} of {self.student.student_id}"


# --------------------------------------------------------------------------- #
# Classes & schedule
# --------------------------------------------------------------------------- #
class Weekday(models.TextChoices):
    MON = "MON", "Mon"
    TUE = "TUE", "Tue"
    WED = "WED", "Wed"
    THU = "THU", "Thu"
    FRI = "FRI", "Fri"
    SAT = "SAT", "Sat"
    SUN = "SUN", "Sun"


WEEKDAY_ORDER = [d.value for d in Weekday]


class SchoolClass(models.Model):
    """A subject handled by a teacher for one section, e.g. *IS 103 – Logic Formulation, BSIS 2-A*."""

    code = models.CharField(max_length=20, help_text="Subject code, e.g. IS 103")
    name = models.CharField(max_length=120, help_text="Descriptive title, e.g. Logic Formulation")
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name="classes")
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="classes"
    )
    students = models.ManyToManyField(
        Student, blank=True, related_name="enrolled_classes", help_text="Students enrolled in this class."
    )
    room = models.CharField(max_length=40, blank=True)
    meeting_days = models.CharField(max_length=27, blank=True, help_text="Comma-separated, e.g. MON,WED,FRI")

    # Teacher-defined status rules
    start_time = models.TimeField(help_text="Official class start time.")
    end_time = models.TimeField(null=True, blank=True)
    grace_period_minutes = models.PositiveSmallIntegerField(
        default=15, validators=[MaxValueValidator(180)],
        help_text="Arrivals up to this many minutes after start are still Present.",
    )
    absent_after_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(600)],
        help_text="Optional. Scans later than this many minutes after start are tagged Absent "
                  "instead of Late. Leave blank to always tag late arrivals as Late.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_time", "code"]
        verbose_name = "class"
        verbose_name_plural = "classes"
        constraints = [
            models.UniqueConstraint(fields=["code", "section"], name="unique_subject_per_section"),
        ]

    def __str__(self):
        return f"{self.code} – {self.name} ({self.section})"

    @property
    def label(self):
        return f"{self.code} · {self.section}"

    @property
    def teacher_name(self):
        if not self.teacher:
            return ""
        return self.teacher.get_full_name() or self.teacher.username

    @property
    def days_list(self):
        return [d for d in self.meeting_days.split(",") if d]

    def meets_on(self, date):
        return not self.days_list or WEEKDAY_ORDER[date.weekday()] in self.days_list

    def roster(self):
        return Student.objects.filter(
            models.Q(section_id=self.section_id) | models.Q(enrolled_classes=self),
            is_active=True,
        ).distinct()

    def get_absolute_url(self):
        return reverse("attendance:class_edit", args=[self.pk])


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #
class AttendanceSession(models.Model):
    """One class meeting on one date. Scans are only accepted while it is open."""

    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="sessions")
    date = models.DateField(default=timezone.localdate)

    # Snapshot of the rules in force for this meeting
    start_time = models.TimeField()
    grace_period_minutes = models.PositiveSmallIntegerField()
    absent_after_minutes = models.PositiveSmallIntegerField(null=True, blank=True)

    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-start_time"]
        constraints = [
            models.UniqueConstraint(fields=["school_class", "date"], name="one_session_per_class_per_day"),
        ]

    def __str__(self):
        return f"{self.school_class.label} on {self.date:%Y-%m-%d}"

    def get_absolute_url(self):
        return reverse("attendance:session_detail", args=[self.pk])

    # ---- schedule helpers ------------------------------------------------- #
    @property
    def is_open(self):
        return self.closed_at is None

    @property
    def start_datetime(self):
        naive = datetime.combine(self.date, self.start_time)
        return timezone.make_aware(naive, timezone.get_current_timezone())

    @property
    def grace_deadline(self):
        return self.start_datetime + timedelta(minutes=self.grace_period_minutes)

    @property
    def absent_deadline(self):
        if self.absent_after_minutes is None:
            return None
        return self.start_datetime + timedelta(minutes=self.absent_after_minutes)

    def status_for(self, arrived_at):
        """
        Automated status tagging.

        * arrived ≤ start + grace                → PRESENT (early arrivals included)
        * arrived > start + absent_after (if set) → ABSENT
        * otherwise                              → LATE
        """
        if self.absent_deadline is not None and arrived_at > self.absent_deadline:
            return AttendanceRecord.Status.ABSENT
        if arrived_at <= self.grace_deadline:
            return AttendanceRecord.Status.PRESENT
        return AttendanceRecord.Status.LATE


class AttendanceRecord(models.Model):
    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        LATE = "LATE", "Late"
        ABSENT = "ABSENT", "Absent"

    class Method(models.TextChoices):
        FACE = "FACE", "Face scan"
        MANUAL = "MANUAL", "Manual override"
        AUTO = "AUTO", "Auto (no scan)"

    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name="records")
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="attendance_records")
    status = models.CharField(max_length=7, choices=Status.choices)
    time_in = models.DateTimeField(null=True, blank=True, help_text="Arrival time; empty when never scanned.")
    method = models.CharField(max_length=6, choices=Method.choices, default=Method.FACE)
    match_distance = models.FloatField(null=True, blank=True, help_text="Descriptor distance (lower = closer).")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["session", "time_in"]
        constraints = [
            # Duplicate scan prevention at the database level.
            models.UniqueConstraint(fields=["session", "student"], name="unique_student_per_session"),
        ]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["time_in"])]

    def __str__(self):
        return f"{self.student.student_id} · {self.session} · {self.status}"

    @property
    def confidence_pct(self):
        """Rough human-friendly confidence derived from descriptor distance."""
        if self.match_distance is None:
            return None
        return max(0, min(100, round((1 - self.match_distance) * 100)))
