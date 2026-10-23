"""
HTML page views for ScanSync.

JSON endpoints used by the webcam scripts (scan / register / live status) live
in ``api.py``. All heavy lifting is delegated to ``services.py``.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import exports, services
from .forms import (
    RecordOverrideForm,
    ReportFilterForm,
    SchoolClassForm,
    SectionForm,
    StudentAccountForm,
    StudentForm,
    StudentSignupForm,
)
from .models import AttendanceRecord, AttendanceSession, SchoolClass, Section, Student

S = AttendanceRecord.Status


# --------------------------------------------------------------------------- #
# Dashboard & Role Dispatch
# --------------------------------------------------------------------------- #
def _staff_only(request):
    if services.is_student(request.user):
        messages.info(request, "Welcome to your student dashboard.")
        return redirect("attendance:dashboard")
    return None


def landing_page(request):
    total_classes = SchoolClass.objects.filter(is_active=True).count()
    total_students = Student.objects.filter(is_active=True).count()
    total_records = AttendanceRecord.objects.count()

    return render(
        request,
        "attendance/landing.html",
        {
            "total_classes": total_classes,
            "total_students": total_students,
            "total_records": total_records,
        },
    )


def dashboard(request):
    if not request.user.is_authenticated:
        return landing_page(request)

    if services.is_student(request.user):
        return student_dashboard(request)

    today = timezone.localdate()
    classes = services.visible_classes(request.user).filter(is_active=True)
    records = services.visible_records(request.user)

    today_records = records.filter(session__date=today)
    kpi = today_records.aggregate(
        present=Count("id", filter=Q(status=S.PRESENT)),
        late=Count("id", filter=Q(status=S.LATE)),
        absent=Count("id", filter=Q(status=S.ABSENT)),
    )
    sessions_today = {s.school_class_id: s for s in services.visible_sessions(request.user).filter(date=today)}

    todays_classes = []
    for c in classes:
        if c.meets_on(today) or c.pk in sessions_today:
            session = sessions_today.get(c.pk)
            todays_classes.append({
                "cls": c,
                "session": session,
                "counts": services.session_counts(session) if session else None,
            })

    # 7-day attendance trend (arrived ÷ recorded)
    start = today - timedelta(days=6)
    daily = {
        row["session__date"]: row
        for row in records.filter(session__date__gte=start)
        .values("session__date")
        .annotate(total=Count("id"), arrived=Count("id", filter=Q(status__in=[S.PRESENT, S.LATE])),
                  late=Count("id", filter=Q(status=S.LATE)))
    }
    trend = []
    for i in range(7):
        d = start + timedelta(days=i)
        row = daily.get(d, {"total": 0, "arrived": 0, "late": 0})
        rate = round(row["arrived"] / row["total"] * 100) if row["total"] else 0
        late_pct = round(row["late"] / row["total"] * 100) if row["total"] else 0
        trend.append({"date": d, "rate": rate, "late_pct": late_pct, "total": row["total"]})

    all_students = Student.objects.filter(is_active=True).select_related("section").order_by("last_name", "first_name")

    context = {
        "kpi": kpi,
        "kpi_students": Student.objects.filter(is_active=True).count(),
        "kpi_classes": classes.count(),
        "todays_classes": todays_classes,
        "trend": trend,
        "recent": today_records.filter(time_in__isnull=False)
        .select_related("student__section", "session__school_class").order_by("-time_in")[:8],
        "open_sessions": services.visible_sessions(request.user).filter(closed_at__isnull=True)
        .select_related("school_class__section"),
        "all_classes": classes,
        "all_students": all_students,
    }
    return render(request, "attendance/dashboard.html", context)


def _get_student_enrolled_classes(student, today=None):
    if today is None:
        today = timezone.localdate()
    classes = (
        SchoolClass.objects.filter(
            Q(section=student.section) | Q(students=student),
            is_active=True,
        )
        .distinct()
        .select_related("teacher", "section")
    )
    sessions_today = {
        s.school_class_id: s
        for s in AttendanceSession.objects.filter(school_class__in=classes, date=today)
    }
    records_today = {
        r.session_id: r
        for r in AttendanceRecord.objects.filter(student=student, session__date=today)
    }

    records = AttendanceRecord.objects.filter(student=student)
    class_stats = {
        row["session__school_class_id"]: row
        for row in records.values("session__school_class_id").annotate(
            total=Count("id"),
            attended=Count("id", filter=Q(status__in=[S.PRESENT, S.LATE])),
        )
    }

    todays_classes = []
    enrolled_classes = []
    live_sessions = []

    for c in classes:
        session = sessions_today.get(c.pk)
        record = records_today.get(session.pk) if session else None
        can_scan = bool(session and session.is_open and (record is None or record.time_in is None))

        st = class_stats.get(c.pk, {"total": 0, "attended": 0})
        rate = round(st["attended"] / st["total"] * 100) if st["total"] else None

        item = {
            "cls": c,
            "session": session,
            "record": record,
            "can_scan": can_scan,
            "is_individually_added": c.section_id != student.section_id,
            "total_sessions": st["total"],
            "attended": st["attended"],
            "rate": rate,
            "meets_today": c.meets_on(today),
        }
        enrolled_classes.append(item)

        if can_scan:
            live_sessions.append(item)

        if c.meets_on(today) or c.pk in sessions_today:
            todays_classes.append(item)

    return enrolled_classes, todays_classes, live_sessions, records


@login_required
def student_dashboard(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        return redirect("attendance:dashboard")

    today = timezone.localdate()
    enrolled_classes, todays_classes, live_sessions, records = _get_student_enrolled_classes(student, today)

    kpi = records.aggregate(
        present=Count("id", filter=Q(status=S.PRESENT)),
        late=Count("id", filter=Q(status=S.LATE)),
        absent=Count("id", filter=Q(status=S.ABSENT)),
        total=Count("id"),
    )
    arrived = (kpi["present"] or 0) + (kpi["late"] or 0)
    kpi["rate"] = round(arrived / kpi["total"] * 100) if kpi["total"] else 0

    history = records.select_related("session__school_class__teacher").order_by("-session__date", "-time_in")[:20]

    context = {
        "student": student,
        "today": today,
        "kpi": kpi,
        "todays_classes": todays_classes,
        "enrolled_classes": enrolled_classes,
        "live_sessions": live_sessions,
        "history": history,
        "sample_count": student.face_samples.count(),
        "min_samples": settings.SCANSYNC["MIN_FACE_SAMPLES"],
    }
    return render(request, "attendance/student_dashboard.html", context)


@login_required
def student_classes(request):
    if not services.is_student(request.user):
        return redirect("attendance:class_list")

    student = getattr(request.user, "student_profile", None)
    if not student:
        return redirect("attendance:dashboard")

    today = timezone.localdate()
    enrolled_classes, todays_classes, live_sessions, records = _get_student_enrolled_classes(student, today)

    context = {
        "student": student,
        "today": today,
        "enrolled_classes": enrolled_classes,
        "live_sessions": live_sessions,
        "todays_classes": todays_classes,
        "sample_count": student.face_samples.count(),
        "min_samples": settings.SCANSYNC["MIN_FACE_SAMPLES"],
    }
    return render(request, "attendance/student_classes.html", context)


@login_required
def student_scan(request, session_id):
    if not services.is_student(request.user):
        messages.error(request, "Only enrolled students can use the student self-scanner.")
        return redirect("attendance:dashboard")

    student = request.user.student_profile
    session = get_object_or_404(
        AttendanceSession.objects.select_related("school_class__section", "school_class__teacher"),
        pk=session_id,
    )

    is_enrolled = (
        session.school_class.section_id == student.section_id
        or session.school_class.students.filter(pk=student.pk).exists()
    )
    if not is_enrolled:
        messages.error(
            request,
            f"You are not enrolled in {session.school_class.code} ({session.school_class.name}).",
        )
        return redirect("attendance:dashboard")

    if not session.is_open:
        messages.warning(request, f"The attendance session for {session.school_class.code} is closed.")
        return redirect("attendance:dashboard")

    existing_record = AttendanceRecord.objects.filter(session=session, student=student).first()
    if existing_record and existing_record.time_in is not None:
        messages.info(
            request,
            f"You are already recorded as {existing_record.get_status_display()} for {session.school_class.code}.",
        )
        return redirect("attendance:dashboard")

    if student.face_samples.count() < settings.SCANSYNC["MIN_FACE_SAMPLES"]:
        messages.warning(
            request,
            "You do not have enough face samples enrolled yet. Please register your face first.",
        )
        return redirect("attendance:dashboard")

    return render(request, "attendance/student_scanner.html", {
        "session": session,
        "student": student,
        "scanner_config": {
            "scanUrl": reverse("attendance:api_student_scan", args=[session.pk]),
            "redirectUrl": reverse("attendance:dashboard"),
            "threshold": settings.SCANSYNC["MATCH_THRESHOLD"],
        },
    })


# --------------------------------------------------------------------------- #
# Live scanner
# --------------------------------------------------------------------------- #
@login_required
def scanner_select(request):
    """Pick a class to start scanning (or resume an open session)."""
    check = _staff_only(request)
    if check:
        return check
    today = timezone.localdate()
    classes = services.visible_classes(request.user).filter(is_active=True).annotate(
        roster_count=Count("section__students", filter=Q(section__students__is_active=True))
    )
    sessions = {s.school_class_id: s for s in AttendanceSession.objects.filter(date=today, school_class__in=classes)}
    items = [{"cls": c, "session": sessions.get(c.pk), "today": c.meets_on(today)} for c in classes]
    items.sort(key=lambda i: (not i["today"], i["cls"].start_time))
    return render(request, "attendance/scanner_select.html", {"items": items})


@login_required
@require_POST
def session_start(request):
    school_class = get_object_or_404(services.visible_classes(request.user), pk=request.POST.get("school_class"))
    session, created = services.open_session(school_class, request.user)
    if not created:
        messages.info(request, f"Resumed today's session for {school_class.label}.")
    return redirect("attendance:scanner", session_id=session.pk)


@login_required
def scanner(request, session_id):
    session = get_object_or_404(
        services.visible_sessions(request.user).select_related("school_class__section"), pk=session_id
    )
    snapshot = services.session_snapshot(session)
    return render(request, "attendance/scanner.html", {
        "session": session,
        "snapshot": snapshot,
        "match_threshold": settings.SCANSYNC["MATCH_THRESHOLD"],
        "scanner_config": {
            "scanUrl": reverse("attendance:api_scan", args=[session.pk]),
            "statusUrl": reverse("attendance:api_session_status", args=[session.pk]),
            "threshold": settings.SCANSYNC["MATCH_THRESHOLD"],
            "snapshot": snapshot,
        },
    })


@login_required
@require_POST
def session_close(request, session_id):
    session = get_object_or_404(services.visible_sessions(request.user), pk=session_id)
    if session.is_open:
        absent = services.close_session(session)
        messages.success(request, f"Session closed. {absent} student(s) without a scan were marked Absent.")
    return redirect("attendance:session_detail", session_id=session.pk)


@login_required
@require_POST
def session_reopen(request, session_id):
    session = get_object_or_404(services.visible_sessions(request.user), pk=session_id)
    session.closed_at = None
    session.save(update_fields=["closed_at"])
    messages.info(request, "Session re-opened. Late arrivals can now scan in.")
    return redirect("attendance:scanner", session_id=session.pk)


@login_required
def session_detail(request, session_id):
    session = get_object_or_404(
        services.visible_sessions(request.user).select_related("school_class__section"), pk=session_id
    )
    records = {r.student_id: r for r in session.records.select_related("student")}
    roster = list(session.school_class.roster())
    # Include records for students who have since moved section / been archived.
    roster_ids = {s.pk for s in roster}
    roster += [r.student for sid, r in records.items() if sid not in roster_ids]
    rows = [{"student": s, "record": records.get(s.pk)} for s in roster]
    order = {S.PRESENT: 0, S.LATE: 1, S.ABSENT: 2, None: 3}
    rows.sort(key=lambda r: (order[r["record"].status if r["record"] else None], r["student"].last_name))
    return render(request, "attendance/session_detail.html", {
        "session": session, "rows": rows, "counts": services.session_counts(session),
        "status_choices": S.choices,
    })


@login_required
@require_POST
def record_override(request, session_id, student_pk):
    """Teacher correction (e.g. excused absence, forgot to scan)."""
    session = get_object_or_404(services.visible_sessions(request.user), pk=session_id)
    student = get_object_or_404(Student, pk=student_pk)
    form = RecordOverrideForm(request.POST)
    if form.is_valid():
        record, _ = AttendanceRecord.objects.get_or_create(
            session=session, student=student,
            defaults={"status": form.cleaned_data["status"], "method": AttendanceRecord.Method.MANUAL},
        )
        record.status = form.cleaned_data["status"]
        record.method = AttendanceRecord.Method.MANUAL
        record.note = form.cleaned_data["note"] or f"Set by {request.user.get_username()}"
        if record.status != S.ABSENT and record.time_in is None:
            record.time_in = timezone.now()
        record.save()
        messages.success(request, f"{student.full_name} marked {record.get_status_display()}.")
    return redirect("attendance:session_detail", session_id=session.pk)


@login_required
def session_export(request, session_id):
    session = get_object_or_404(services.visible_sessions(request.user), pk=session_id)
    fmt = "xlsx" if request.GET.get("format") == "xlsx" else "csv"
    scope = f"{session.school_class.code}_{session.school_class.section}_{session.date}".replace(" ", "")
    return exports.export(session.records.all(), fmt, scope=scope)


# --------------------------------------------------------------------------- #
# Students
# --------------------------------------------------------------------------- #
@login_required
def student_list(request):
    check = _staff_only(request)
    if check:
        return check
    q = request.GET.get("q", "").strip()
    section = request.GET.get("section", "")
    show = request.GET.get("show", "active")
    students = Student.objects.select_related("section", "user").annotate(samples=Count("face_samples")).order_by("last_name", "first_name")
    if show != "all":
        students = students.filter(is_active=(show != "archived"))
    if q:
        students = students.filter(Q(student_id__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    if section:
        students = students.filter(section_id=section)
    page = Paginator(students, 24).get_page(request.GET.get("page"))
    return render(request, "attendance/student_list.html", {
        "page": page, "q": q, "section": section, "show": show,
        "sections": Section.objects.annotate(n=Count("students", filter=Q(students__is_active=True))),
        "section_form": SectionForm(),
    })


def _register_config(mode, api_url):
    return {
        "mode": mode,
        "apiUrl": api_url,
        "minSamples": settings.SCANSYNC["MIN_FACE_SAMPLES"],
        "maxSamples": settings.SCANSYNC["MAX_FACE_SAMPLES"],
    }


@login_required
def student_register(request):
    """Webcam enrolment page. The form posts JSON to ``api.register_student``."""
    return render(request, "attendance/student_register.html", {
        "form": StudentForm(initial={"section": request.GET.get("section")}),
        "mode": "create",
        "min_samples": settings.SCANSYNC["MIN_FACE_SAMPLES"],
        "max_samples": settings.SCANSYNC["MAX_FACE_SAMPLES"],
        "section_form": SectionForm(),
        "register_config": _register_config("create", reverse("attendance:api_register_student")),
    })


def student_signup(request):
    """Public self-registration for students to create their login accounts."""
    if request.user.is_authenticated:
        return redirect("attendance:dashboard")

    if request.method == "POST":
        form = StudentSignupForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                User = get_user_model()
                user = User.objects.create_user(
                    username=data["username"],
                    email=data.get("email", ""),
                    password=data["password"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                )
                student = Student.objects.filter(student_id=data["student_id"]).first()
                if student:
                    student.user = user
                    student.first_name = data["first_name"]
                    student.last_name = data["last_name"]
                    student.middle_name = data.get("middle_name", "")
                    student.section = data["section"]
                    student.save()
                else:
                    student = Student.objects.create(
                        user=user,
                        student_id=data["student_id"],
                        first_name=data["first_name"],
                        middle_name=data.get("middle_name", ""),
                        last_name=data["last_name"],
                        section=data["section"],
                    )
            login(request, user)
            messages.success(
                request,
                f"Welcome, {student.first_name}! Your student account has been created successfully.",
            )
            if student.face_samples.count() < settings.SCANSYNC["MIN_FACE_SAMPLES"]:
                messages.info(
                    request,
                    "Next, please enrol your 4 face angles to enable facial recognition check-in.",
                )
                return redirect("attendance:student_reenroll", pk=student.pk)
            return redirect("attendance:dashboard")
    else:
        form = StudentSignupForm()

    return render(request, "registration/student_signup.html", {"form": form})


@login_required
def student_reenroll(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if services.is_student(request.user) and getattr(request.user, "student_profile", None) != student:
        raise PermissionDenied("You can only enrol face samples for your own account.")
    is_stud = services.is_student(request.user)
    cancel_url = reverse("attendance:dashboard") if is_stud else student.get_absolute_url()
    return render(request, "attendance/student_register.html", {
        "student": student,
        "mode": "reenroll",
        "is_student_user": is_stud,
        "cancel_url": cancel_url,
        "min_samples": settings.SCANSYNC["MIN_FACE_SAMPLES"],
        "max_samples": settings.SCANSYNC["MAX_FACE_SAMPLES"],
        "register_config": _register_config("reenroll", reverse("attendance:api_reenroll_faces", args=[student.pk])),
    })


@login_required
def student_detail(request, pk):
    check = _staff_only(request)
    if check:
        return check
    student = get_object_or_404(
        Student.objects.select_related("section", "user").prefetch_related("face_samples"), pk=pk
    )
    if request.method == "POST":
        form = StudentForm(request.POST, instance=student)
        if form.is_valid():
            form.save()
            messages.success(request, "Student details updated.")
            return redirect(student)
    else:
        form = StudentForm(instance=student)

    history = services.visible_records(request.user).filter(student=student).select_related(
        "session__school_class"
    ).order_by("-session__date", "-time_in")
    stats = history.aggregate(
        present=Count("id", filter=Q(status=S.PRESENT)),
        late=Count("id", filter=Q(status=S.LATE)),
        absent=Count("id", filter=Q(status=S.ABSENT)),
        total=Count("id"),
    )
    stats["rate"] = round((stats["present"] + stats["late"]) / stats["total"] * 100) if stats["total"] else None
    return render(request, "attendance/student_detail.html", {
        "student": student,
        "form": form,
        "account_form": StudentAccountForm(student=student),
        "history": history[:50],
        "stats": stats,
    })


@login_required
@require_POST
def student_account_manage(request, pk):
    """Allows teachers and administrators to create or update student login accounts."""
    check = _staff_only(request)
    if check:
        return check
    student = get_object_or_404(Student, pk=pk)
    form = StudentAccountForm(request.POST, student=student)
    if form.is_valid():
        User = get_user_model()
        username = form.cleaned_data["username"]
        password = form.cleaned_data["password"]
        if student.user:
            user = student.user
            user.username = username
            user.set_password(password)
            user.save()
            messages.success(request, f"Updated portal credentials for {student.full_name}.")
        else:
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=student.first_name,
                last_name=student.last_name,
            )
            student.user = user
            student.save(update_fields=["user"])
            messages.success(request, f"Created student portal account (@{username}) for {student.full_name}.")
    else:
        err = " ".join([e for errs in form.errors.values() for e in errs])
        messages.error(request, f"Could not save account: {err}")
    return redirect("attendance:student_detail", pk=student.pk)


@login_required
@require_POST
def student_toggle_active(request, pk):
    student = get_object_or_404(Student, pk=pk)
    student.is_active = not student.is_active
    student.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"{student.full_name} {'restored' if student.is_active else 'archived'}.")
    return redirect(student)


@login_required
@require_POST
def student_delete(request, pk):
    if not services.is_admin(request.user):
        raise PermissionDenied("Only administrators can permanently delete students.")
    student = get_object_or_404(Student, pk=pk)
    for fs in student.face_samples.all():
        fs.image.delete(save=False)
    if student.photo:
        student.photo.delete(save=False)
    name = student.full_name
    student.delete()
    messages.success(request, f"{name} and their attendance history were deleted.")
    return redirect("attendance:student_list")


@login_required
@require_POST
def section_create(request):
    form = SectionForm(request.POST)
    if form.is_valid():
        section = form.save()
        messages.success(request, f"Section {section} created.")
    else:
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect(request.POST.get("next") or "attendance:student_list")


# --------------------------------------------------------------------------- #
# Classes
# --------------------------------------------------------------------------- #
@login_required
def class_list(request):
    if services.is_student(request.user):
        return student_classes(request)
    check = _staff_only(request)
    if check:
        return check
    classes = services.visible_classes(request.user).annotate(
        roster_count=Count("section__students", filter=Q(section__students__is_active=True), distinct=True),
        session_count=Count("sessions", distinct=True),
    )
    return render(request, "attendance/class_list.html", {"classes": classes, "section_form": SectionForm()})


@login_required
def class_form(request, pk=None):
    instance = get_object_or_404(services.visible_classes(request.user), pk=pk) if pk else None
    form = SchoolClassForm(request.POST or None, instance=instance, user=request.user,
                           initial=None if instance else {"teacher": request.user.pk})
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        messages.success(request, f"Class {obj.label} saved.")
        return redirect("attendance:class_list")

    roster = list(instance.roster().select_related("section")) if instance else []
    enrolled_pks = set(instance.students.values_list("pk", flat=True)) if instance else set()
    other_students = (
        Student.objects.filter(is_active=True)
        .exclude(pk__in=[s.pk for s in roster])
        .select_related("section")
        .order_by("last_name", "first_name")
        if instance
        else []
    )

    return render(
        request,
        "attendance/class_form.html",
        {
            "form": form,
            "instance": instance,
            "roster": roster,
            "enrolled_pks": enrolled_pks,
            "other_students": other_students,
        },
    )


@login_required
@require_POST
def class_enroll_students(request):
    """Enrolls one or more students into a class."""
    check = _staff_only(request)
    if check:
        return check

    class_id = request.POST.get("school_class")
    school_class = get_object_or_404(services.visible_classes(request.user), pk=class_id)
    student_ids = request.POST.getlist("student_ids")
    if not student_ids and request.POST.get("student_id"):
        student_ids = [request.POST.get("student_id")]

    if not student_ids:
        messages.warning(request, "Please select at least one student to add.")
        return redirect(request.POST.get("next") or reverse("attendance:dashboard"))

    students = Student.objects.filter(pk__in=student_ids, is_active=True)
    added = 0
    for s in students:
        school_class.students.add(s)
        added += 1

    messages.success(
        request, f"Added {added} student(s) to {school_class.code} – {school_class.name}."
    )
    return redirect(request.POST.get("next") or reverse("attendance:dashboard"))


@login_required
@require_POST
def class_unenroll_student(request, class_id, student_id):
    """Removes an individually enrolled student from a class."""
    check = _staff_only(request)
    if check:
        return check

    school_class = get_object_or_404(services.visible_classes(request.user), pk=class_id)
    student = get_object_or_404(Student, pk=student_id)
    school_class.students.remove(student)
    messages.info(request, f"Removed {student.full_name} from {school_class.code}.")
    return redirect(request.POST.get("next") or reverse("attendance:class_edit", args=[school_class.pk]))


@login_required
@require_POST
def class_delete(request, pk):
    obj = get_object_or_404(services.visible_classes(request.user), pk=pk)
    label = obj.label
    obj.delete()
    messages.success(request, f"Class {label} and its attendance sessions were deleted.")
    return redirect("attendance:class_list")


# --------------------------------------------------------------------------- #
# Reports & export
# --------------------------------------------------------------------------- #
def _report_form(request):
    data = request.GET.copy()
    if not any(data.get(k) for k in ("date_from", "date_to", "school_class", "section", "student", "status")):
        today = timezone.localdate()
        data.setdefault("date_from", (today.replace(day=1)).isoformat())
        data.setdefault("date_to", today.isoformat())
    return ReportFilterForm(data, classes=services.visible_classes(request.user))


@login_required
def reports(request):
    check = _staff_only(request)
    if check:
        return check
    form = _report_form(request)
    records = form.filter(services.visible_records(request.user)).select_related(
        "student", "session__school_class__section"
    ).order_by("-session__date", "session__school_class__code", "student__last_name")
    summary = records.aggregate(
        total=Count("id"),
        present=Count("id", filter=Q(status=S.PRESENT)),
        late=Count("id", filter=Q(status=S.LATE)),
        absent=Count("id", filter=Q(status=S.ABSENT)),
        sessions=Count("session", distinct=True),
    )
    summary["rate"] = round((summary["present"] + summary["late"]) / summary["total"] * 100) if summary["total"] else 0
    page = Paginator(records, 50).get_page(request.GET.get("page"))
    return render(request, "attendance/reports.html", {
        "form": form, "page": page, "summary": summary,
        "export_qs": request.GET.urlencode() if request.GET else form.data.urlencode(),
    })


@login_required
def reports_export(request):
    form = _report_form(request)
    fmt = "xlsx" if request.GET.get("format") == "xlsx" else "csv"
    records = form.filter(services.visible_records(request.user))
    if not form.is_valid():
        messages.error(request, "Fix the report filters before exporting.")
        return redirect(f"{reverse('attendance:reports')}?{request.GET.urlencode()}")
    return exports.export(records, fmt)
