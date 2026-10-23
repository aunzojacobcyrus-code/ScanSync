"""Root URL configuration for ScanSync."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "ScanSync Administration"
admin.site.site_title = "ScanSync Admin"
admin.site.index_title = "Attendance data"

# Only superusers can access the Django admin backend
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser

from attendance import views as attendance_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("register/", attendance_views.student_signup, name="student_signup"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("attendance.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
