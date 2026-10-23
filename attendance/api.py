"""
JSON endpoints consumed by the webcam front-end.

    POST /api/sessions/<id>/scan/       live recognition → attendance record
    GET  /api/sessions/<id>/status/     counters + recent check-ins (polling)
    POST /api/students/register/        new student + face samples
    POST /api/students/<pk>/faces/      re-enrol face samples

The browser computes 128-d face descriptors with face-api.js; the server owns
identity matching, status tagging and duplicate prevention.
"""
import json
from functools import wraps

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from . import services
from .forms import StudentForm
from .models import Student


def api_login_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required."}, status=401)
        return view(request, *args, **kwargs)
    return wrapper


def parse_json(request):
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------- #
# Live scanning
# --------------------------------------------------------------------------- #
@api_login_required
@require_POST
def scan(request, session_id):
    session = get_object_or_404(
        services.visible_sessions(request.user).select_related("school_class__section"), pk=session_id
    )
    body = parse_json(request)
    if body is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    try:
        descriptor = services.clean_descriptor(body.get("descriptor"))
    except services.InvalidDescriptor as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    result = services.process_scan(session, descriptor)
    result["counts"] = services.session_counts(session)
    return JsonResponse(result)


@api_login_required
@require_POST
def student_scan(request, session_id):
    if not services.is_student(request.user):
        return JsonResponse({"error": "Student account required."}, status=403)

    student = request.user.student_profile
    from .models import AttendanceSession
    session = get_object_or_404(
        AttendanceSession.objects.select_related("school_class__section"), pk=session_id
    )
    body = parse_json(request)
    if body is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    try:
        descriptor = services.clean_descriptor(body.get("descriptor"))
    except services.InvalidDescriptor as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    result = services.process_student_scan(session, student, descriptor)
    return JsonResponse(result)


@api_login_required
@require_GET
def session_status(request, session_id):
    session = get_object_or_404(
        services.visible_sessions(request.user).select_related("school_class__section"), pk=session_id
    )
    return JsonResponse(services.session_snapshot(session))


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
@api_login_required
@require_POST
def register_student(request):
    body = parse_json(request)
    if body is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    form = StudentForm(data=body.get("student") or {})
    if not form.is_valid():
        return JsonResponse({"error": "Please fix the highlighted fields.", "fields": form.errors}, status=400)

    try:
        samples = services.clean_samples(body.get("samples"))
    except services.InvalidSample as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    conflict = services.find_face_conflict([d for _, d in samples])
    if conflict and not body.get("force"):
        return JsonResponse({
            "error": f"This face already matches {conflict.student.full_name} "
                     f"({conflict.student.student_id}). Each student can only be registered once.",
            "conflict": services.serialize_student(conflict.student),
        }, status=409)

    account_info = body.get("account") or {}
    with transaction.atomic():
        student = form.save(commit=False)
        student.registered_by = request.user
        if account_info.get("create") and account_info.get("username") and account_info.get("password"):
            User = get_user_model()
            uname = account_info["username"].strip()
            pw = account_info["password"]
            if not User.objects.filter(username__iexact=uname).exists():
                user = User.objects.create_user(
                    username=uname,
                    password=pw,
                    first_name=student.first_name,
                    last_name=student.last_name,
                )
                student.user = user
        student.save()
        services.save_face_samples(student, samples)

    return JsonResponse({
        "ok": True,
        "student": services.serialize_student(student),
        "redirect": student.get_absolute_url(),
    }, status=201)


@api_login_required
@require_POST
def reenroll_faces(request, pk):
    student = get_object_or_404(Student, pk=pk)
    if services.is_student(request.user) and getattr(request.user, "student_profile", None) != student:
        return JsonResponse({"error": "You can only enrol your own face."}, status=403)

    body = parse_json(request)
    if body is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    try:
        samples = services.clean_samples(body.get("samples"))
    except services.InvalidSample as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    conflict = services.find_face_conflict([d for _, d in samples], exclude_student=student)
    if conflict:
        return JsonResponse({
            "error": f"These captures match {conflict.student.full_name} ({conflict.student.student_id}), "
                     f"not {student.full_name}.",
        }, status=409)

    services.save_face_samples(student, samples, replace=bool(body.get("replace", True)))
    redirect_url = reverse("attendance:dashboard") if services.is_student(request.user) else student.get_absolute_url()
    return JsonResponse({"ok": True, "student": services.serialize_student(student),
                         "redirect": redirect_url})
