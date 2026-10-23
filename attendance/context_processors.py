from django.conf import settings
from django.utils import timezone


def scansync(request):
    """Expose recognition config + today's date to every template."""
    context = {
        "SCANSYNC": settings.SCANSYNC,
        "today": timezone.localdate(),
        "nav": getattr(getattr(request, "resolver_match", None), "url_name", "") or "",
        "open_session_count": 0,
    }
    user = getattr(request, "user", None)
    is_stud = False
    student_profile = None
    if user is not None and user.is_authenticated:
        from .services import is_student, visible_sessions  # local import avoids app-loading cycles

        is_stud = is_student(user)
        student_profile = getattr(user, "student_profile", None)
        context["open_session_count"] = visible_sessions(user).filter(closed_at__isnull=True).count()
    context["is_student"] = is_stud
    context["student_profile"] = student_profile
    return context
