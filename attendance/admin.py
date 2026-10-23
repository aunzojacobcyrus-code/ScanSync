from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html

from .models import AttendanceRecord, AttendanceSession, FaceSample, SchoolClass, Section, Student

User = get_user_model()

try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class CustomUserAdmin(BaseUserAdmin):
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "superadmin_access",
    )

    @admin.display(description="Superadmin Access", boolean=True, ordering="is_superuser")
    def superadmin_access(self, obj):
        return obj.is_superuser


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "year_level", "created_at")
    search_fields = ("name", "program")


class FaceSampleInline(admin.TabularInline):
    model = FaceSample
    extra = 0
    fields = ("preview", "image", "created_at")
    readonly_fields = ("preview", "created_at")

    @admin.display(description="Preview")
    def preview(self, obj):
        return format_html('<img src="{}" style="height:64px;border-radius:8px">', obj.image.url) if obj.image else "—"


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_id", "last_name", "first_name", "section", "is_active", "sample_count")
    list_filter = ("section", "is_active")
    search_fields = ("student_id", "first_name", "last_name")
    inlines = [FaceSampleInline]

    @admin.display(description="Face samples")
    def sample_count(self, obj):
        return obj.face_samples.count()


@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "section", "teacher", "start_time", "grace_period_minutes", "is_active")
    list_filter = ("section", "teacher", "is_active")
    search_fields = ("code", "name")


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0
    fields = ("student", "status", "time_in", "method", "match_distance", "note")
    raw_id_fields = ("student",)


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ("school_class", "date", "start_time", "grace_period_minutes", "opened_at", "closed_at")
    list_filter = ("date", "school_class")
    date_hierarchy = "date"
    inlines = [AttendanceRecordInline]


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("student", "session", "status", "time_in", "method")
    list_filter = ("status", "method", "session__date", "session__school_class")
    search_fields = ("student__student_id", "student__first_name", "student__last_name")
    raw_id_fields = ("student", "session")
