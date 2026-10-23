"""Forms for ScanSync — used both for HTML POSTs and to validate JSON payloads."""
from django import forms
from django.contrib.auth import get_user_model

from .models import AttendanceRecord, SchoolClass, Section, Student, Weekday, student_id_validator


class StyledMixin:
    """Adds the design-system ``input`` class to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, forms.CheckboxSelectMultiple):
                continue
            css = "select" if isinstance(w, forms.Select) else "input"
            if isinstance(w, forms.CheckboxInput):
                css = "checkbox"
            w.attrs.setdefault("class", css)
            w.attrs.setdefault("id", f"id_{name}")


class SectionForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Section
        fields = ["name", "program", "year_level"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. BSIS 2-A"}),
            "program": forms.TextInput(attrs={"placeholder": "BS Information Systems"}),
        }


class StudentForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Student
        fields = ["student_id", "first_name", "middle_name", "last_name", "section"]
        widgets = {
            "student_id": forms.TextInput(attrs={"placeholder": "e.g. 2025-00123", "autocomplete": "off"}),
            "first_name": forms.TextInput(attrs={"placeholder": "Juan"}),
            "middle_name": forms.TextInput(attrs={"placeholder": "Optional"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Dela Cruz"}),
        }

    def clean_student_id(self):
        return self.cleaned_data["student_id"].strip().upper()

    def clean_first_name(self):
        return self.cleaned_data["first_name"].strip().title()

    def clean_last_name(self):
        return self.cleaned_data["last_name"].strip().title()


class SchoolClassForm(StyledMixin, forms.ModelForm):
    meeting_days = forms.MultipleChoiceField(
        choices=Weekday.choices, required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Leave empty if the class can be scanned on any day.",
    )

    class Meta:
        model = SchoolClass
        fields = ["code", "name", "section", "teacher", "room", "meeting_days",
                  "start_time", "end_time", "grace_period_minutes", "absent_after_minutes", "is_active"]
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "IS 103"}),
            "name": forms.TextInput(attrs={"placeholder": "Logic Formulation"}),
            "room": forms.TextInput(attrs={"placeholder": "Room 204"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "grace_period_minutes": forms.NumberInput(attrs={"min": 0, "max": 180}),
            "absent_after_minutes": forms.NumberInput(attrs={"min": 0, "max": 600, "placeholder": "Optional"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teacher"].queryset = get_user_model().objects.filter(is_active=True).order_by("first_name", "username")
        self.fields["teacher"].label_from_instance = lambda u: u.get_full_name() or u.username
        if self.instance.pk:
            self.initial["meeting_days"] = self.instance.days_list
        # Teachers can only assign classes to themselves.
        if user is not None and not (user.is_staff or user.is_superuser):
            self.fields["teacher"].queryset = self.fields["teacher"].queryset.filter(pk=user.pk)
            self.fields["teacher"].initial = user.pk
            self.fields["teacher"].empty_label = None

    def clean_meeting_days(self):
        order = [d.value for d in Weekday]
        return ",".join(sorted(self.cleaned_data["meeting_days"], key=order.index))

    def clean(self):
        data = super().clean()
        grace, absent = data.get("grace_period_minutes"), data.get("absent_after_minutes")
        if grace is not None and absent is not None and absent <= grace:
            self.add_error("absent_after_minutes", "Absent cut-off must be later than the grace period.")
        start, end = data.get("start_time"), data.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", "End time must be after the start time.")
        return data


class ReportFilterForm(StyledMixin, forms.Form):
    """Filters shared by the reports table and spreadsheet export."""

    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    school_class = forms.ModelChoiceField(queryset=SchoolClass.objects.none(), required=False,
                                          empty_label="All classes", label="Class")
    section = forms.ModelChoiceField(queryset=Section.objects.all(), required=False, empty_label="All sections")
    student = forms.CharField(required=False, label="Student",
                              widget=forms.TextInput(attrs={"placeholder": "Name or student ID"}))
    status = forms.ChoiceField(choices=[("", "Any status")] + list(AttendanceRecord.Status.choices), required=False)

    def __init__(self, *args, classes=None, **kwargs):
        super().__init__(*args, **kwargs)
        if classes is not None:
            self.fields["school_class"].queryset = classes
            self.fields["school_class"].label_from_instance = lambda c: f"{c.code} – {c.name} ({c.section})"

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            self.add_error("date_to", "End date must be on or after the start date.")
        return data

    def filter(self, records):
        from django.db.models import Q

        if not self.is_valid():
            return records.none()
        d = self.cleaned_data
        if d.get("date_from"):
            records = records.filter(session__date__gte=d["date_from"])
        if d.get("date_to"):
            records = records.filter(session__date__lte=d["date_to"])
        if d.get("school_class"):
            records = records.filter(session__school_class=d["school_class"])
        if d.get("section"):
            records = records.filter(session__school_class__section=d["section"])
        if d.get("status"):
            records = records.filter(status=d["status"])
        if d.get("student"):
            q = d["student"].strip()
            records = records.filter(
                Q(student__student_id__icontains=q) | Q(student__first_name__icontains=q) | Q(student__last_name__icontains=q)
            )
        return records


class RecordOverrideForm(forms.Form):
    status = forms.ChoiceField(choices=AttendanceRecord.Status.choices)
    note = forms.CharField(max_length=200, required=False)


class StudentSignupForm(StyledMixin, forms.Form):
    """Self-service registration form for students."""

    student_id = forms.CharField(
        label="Student ID",
        max_length=30,
        validators=[student_id_validator],
        widget=forms.TextInput(attrs={"placeholder": "e.g. 2025-00123", "autocomplete": "off"}),
        help_text="Your official school student ID number.",
    )
    first_name = forms.CharField(
        max_length=60,
        widget=forms.TextInput(attrs={"placeholder": "Juan"}),
    )
    middle_name = forms.CharField(
        max_length=60,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Optional"}),
    )
    last_name = forms.CharField(
        max_length=60,
        widget=forms.TextInput(attrs={"placeholder": "Dela Cruz"}),
    )
    section = forms.ModelChoiceField(
        queryset=Section.objects.all(),
        empty_label="-- Select your section --",
        help_text="The section you are currently enrolled in.",
    )
    email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"placeholder": "student@example.com", "autocomplete": "email"}),
        help_text="Optional school or personal email.",
    )
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "e.g. juan.delacruz", "autocomplete": "username"}),
        help_text="Used to log in to the student portal.",
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"placeholder": "At least 6 characters", "autocomplete": "new-password"}),
    )
    password_confirm = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={"placeholder": "Repeat password", "autocomplete": "new-password"}),
    )

    def clean_student_id(self):
        sid = self.cleaned_data["student_id"].strip().upper()
        existing = Student.objects.filter(student_id=sid).first()
        if existing and existing.user_id:
            raise forms.ValidationError(
                "An account is already linked to this Student ID. Please sign in or contact an administrator."
            )
        return sid

    def clean_username(self):
        uname = self.cleaned_data["username"].strip()
        User = get_user_model()
        if User.objects.filter(username__iexact=uname).exists():
            raise forms.ValidationError("This username is already taken. Please choose another username.")
        return uname

    def clean_first_name(self):
        return self.cleaned_data["first_name"].strip().title()

    def clean_last_name(self):
        return self.cleaned_data["last_name"].strip().title()

    def clean_middle_name(self):
        val = self.cleaned_data.get("middle_name", "")
        return val.strip().title() if val else ""

    def clean(self):
        data = super().clean()
        p1 = data.get("password")
        p2 = data.get("password_confirm")
        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Passwords do not match.")
        if p1 and len(p1) < 6:
            self.add_error("password", "Password must be at least 6 characters.")
        return data


class StudentAccountForm(StyledMixin, forms.Form):
    """Allows teachers and administrators to provision or reset a student's login account."""

    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "e.g. 2025-00123"}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Password (min 6 characters)"}),
    )

    def __init__(self, *args, student=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.student = student
        if student and not self.is_bound:
            if student.user:
                self.fields["username"].initial = student.user.username
            else:
                self.fields["username"].initial = student.student_id.lower()

    def clean_username(self):
        uname = self.cleaned_data["username"].strip()
        User = get_user_model()
        existing = User.objects.filter(username__iexact=uname).first()
        if existing:
            if self.student and self.student.user_id == existing.pk:
                return uname
            raise forms.ValidationError("This username is already taken by another account.")
        return uname

    def clean_password(self):
        pw = self.cleaned_data.get("password", "")
        if len(pw) < 6:
            raise forms.ValidationError("Password must be at least 6 characters.")
        return pw
