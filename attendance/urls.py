from django.urls import path

from . import api, views

app_name = "attendance"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("welcome/", views.landing_page, name="landing"),

    # Live scanner
    path("scanner/", views.scanner_select, name="scanner_select"),
    path("scanner/<int:session_id>/", views.scanner, name="scanner"),
    path("sessions/start/", views.session_start, name="session_start"),
    path("sessions/<int:session_id>/", views.session_detail, name="session_detail"),
    path("sessions/<int:session_id>/close/", views.session_close, name="session_close"),
    path("sessions/<int:session_id>/reopen/", views.session_reopen, name="session_reopen"),
    path("sessions/<int:session_id>/export/", views.session_export, name="session_export"),
    path("sessions/<int:session_id>/override/<int:student_pk>/", views.record_override, name="record_override"),

    # Students
    path("register/", views.student_signup, name="student_signup"),
    path("students/", views.student_list, name="student_list"),
    path("students/register/", views.student_register, name="student_register"),
    path("students/<int:pk>/", views.student_detail, name="student_detail"),
    path("students/<int:pk>/account/", views.student_account_manage, name="student_account_manage"),
    path("students/<int:pk>/faces/", views.student_reenroll, name="student_reenroll"),
    path("students/<int:pk>/toggle/", views.student_toggle_active, name="student_toggle_active"),
    path("students/<int:pk>/delete/", views.student_delete, name="student_delete"),
    path("sections/new/", views.section_create, name="section_create"),

    # Classes
    path("classes/", views.class_list, name="class_list"),
    path("classes/new/", views.class_form, name="class_create"),
    path("classes/enroll/", views.class_enroll_students, name="class_enroll_students"),
    path("classes/<int:pk>/edit/", views.class_form, name="class_edit"),
    path("classes/<int:class_id>/unenroll/<int:student_id>/", views.class_unenroll_student, name="class_unenroll_student"),
    path("classes/<int:pk>/delete/", views.class_delete, name="class_delete"),

    # Reports
    path("reports/", views.reports, name="reports"),
    path("reports/export/", views.reports_export, name="reports_export"),

    # Student scanning & portal
    path("student/classes/", views.student_classes, name="student_classes"),
    path("student/scan/<int:session_id>/", views.student_scan, name="student_scan"),

    # JSON API
    path("api/sessions/<int:session_id>/scan/", api.scan, name="api_scan"),
    path("api/sessions/<int:session_id>/student-scan/", api.student_scan, name="api_student_scan"),
    path("api/sessions/<int:session_id>/status/", api.session_status, name="api_session_status"),
    path("api/students/register/", api.register_student, name="api_register_student"),
    path("api/students/<int:pk>/faces/", api.reenroll_faces, name="api_reenroll_faces"),
]
