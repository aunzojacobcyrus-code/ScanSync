import datetime
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from attendance.models import Section, SchoolClass, Student, AttendanceSession, AttendanceRecord, Weekday
from attendance import services

User = get_user_model()

class ScanSyncCoreTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "admin@test.local", "pass123")
        self.teacher = User.objects.create_user("teacher", "teacher@test.local", "pass123")
        self.section = Section.objects.create(name="BSIS 2-A", program="BS Information Systems", year_level=2)
        self.student = Student.objects.create(
            student_id="2025-00001",
            first_name="Juan",
            last_name="Dela Cruz",
            section=self.section,
        )
        self.school_class = SchoolClass.objects.create(
            code="IS 101",
            name="Introduction to Computing",
            section=self.section,
            teacher=self.teacher,
            start_time=datetime.time(8, 0),
            end_time=datetime.time(9, 30),
            grace_period_minutes=15,
            absent_after_minutes=45,
            meeting_days="MON,WED,FRI",
        )
        self.client = Client()

    def test_class_meeting_days(self):
        # Meets on days in list
        mon = datetime.date(2026, 10, 19)  # Monday
        tue = datetime.date(2026, 10, 20)  # Tuesday
        self.assertTrue(self.school_class.meets_on(mon))
        self.assertFalse(self.school_class.meets_on(tue))

    def test_session_status_rules(self):
        session, _ = services.open_session(self.school_class, self.teacher, date=datetime.date(2026, 10, 19))
        tz = timezone.get_current_timezone()
        
        # 8:10 AM -> Present (<= 8:15 grace)
        t_present = timezone.make_aware(datetime.datetime(2026, 10, 19, 8, 10), tz)
        self.assertEqual(session.status_for(t_present), AttendanceRecord.Status.PRESENT)

        # 8:25 AM -> Late (> 8:15 grace, <= 8:45 absent)
        t_late = timezone.make_aware(datetime.datetime(2026, 10, 19, 8, 25), tz)
        self.assertEqual(session.status_for(t_late), AttendanceRecord.Status.LATE)

        # 8:50 AM -> Absent (> 8:45 absent)
        t_absent = timezone.make_aware(datetime.datetime(2026, 10, 19, 8, 50), tz)
        self.assertEqual(session.status_for(t_absent), AttendanceRecord.Status.ABSENT)

    def test_duplicate_scan_prevention(self):
        session, _ = services.open_session(self.school_class, self.teacher)
        t1 = timezone.now()
        
        # First scan
        rec1, outcome1 = services.record_scan(session, self.student, distance=0.2, when=t1)
        self.assertEqual(outcome1, services.ScanOutcome.CHECKED_IN)
        self.assertEqual(rec1.student, self.student)

        # Second scan for same student in same session
        rec2, outcome2 = services.record_scan(session, self.student, distance=0.22, when=t1 + datetime.timedelta(minutes=5))
        self.assertEqual(outcome2, services.ScanOutcome.DUPLICATE)
        self.assertEqual(rec2.pk, rec1.pk)
        self.assertEqual(session.records.count(), 1)

    def test_session_close_marks_absent(self):
        session, _ = services.open_session(self.school_class, self.teacher)
        # Roster has 1 student who didn't scan
        count = services.close_session(session)
        self.assertEqual(count, 1)
        self.assertFalse(session.is_open)
        rec = session.records.get(student=self.student)
        self.assertEqual(rec.status, AttendanceRecord.Status.ABSENT)
        self.assertEqual(rec.method, AttendanceRecord.Method.AUTO)

    def test_views_require_login(self):
        protected_views = [
            reverse("attendance:scanner_select"),
            reverse("attendance:student_list"),
            reverse("attendance:class_list"),
            reverse("attendance:reports"),
        ]
        for url in protected_views:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 302)

        # Root URL serves the landing page publicly (200 OK)
        res_root = self.client.get(reverse("attendance:dashboard"))
        self.assertEqual(res_root.status_code, 200)

    def test_views_render_for_authenticated_user(self):
        self.client.force_login(self.admin)
        session, _ = services.open_session(self.school_class, self.admin)

        urls = [
            reverse("attendance:dashboard"),
            reverse("attendance:scanner_select"),
            reverse("attendance:scanner", args=[session.pk]),
            reverse("attendance:session_detail", args=[session.pk]),
            reverse("attendance:student_list"),
            reverse("attendance:student_register"),
            reverse("attendance:student_detail", args=[self.student.pk]),
            reverse("attendance:class_list"),
            reverse("attendance:class_create"),
            reverse("attendance:class_edit", args=[self.school_class.pk]),
            reverse("attendance:reports"),
        ]
        for url in urls:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, f"URL {url} failed with status {res.status_code}")

    def test_api_status(self):
        self.client.force_login(self.teacher)
        session, _ = services.open_session(self.school_class, self.teacher)
        res = self.client.get(reverse("attendance:api_session_status", args=[session.pk]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("counts", data)
        self.assertEqual(data["counts"]["roster"], 1)
        self.assertEqual(data["counts"]["present"], 0)

    def test_student_dashboard_and_staff_protection(self):
        student_user = User.objects.create_user("student_user", "s@test.local", "pass123")
        self.student.user = student_user
        self.student.save()

        self.client.force_login(student_user)

        # Student visiting dashboard should render the student dashboard
        res = self.client.get(reverse("attendance:dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Student Portal")
        self.assertContains(res, self.student.first_name)

        # Student visiting staff view should be redirected to their dashboard
        res_staff = self.client.get(reverse("attendance:student_list"))
        self.assertEqual(res_staff.status_code, 302)
        self.assertRedirects(res_staff, reverse("attendance:dashboard"))

    def test_student_self_registration_flow(self):
        # GET registration page
        res = self.client.get(reverse("student_signup"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Create student account")

        # POST registration form
        data = {
            "student_id": "2025-99999",
            "first_name": "Maria",
            "last_name": "Clara",
            "section": self.section.pk,
            "username": "maria.clara",
            "password": "secretpassword",
            "password_confirm": "secretpassword",
        }
        res_post = self.client.post(reverse("student_signup"), data)
        self.assertEqual(res_post.status_code, 302)

        # Student was created and linked to new User
        new_student = Student.objects.get(student_id="2025-99999")
        self.assertIsNotNone(new_student.user)
        self.assertEqual(new_student.user.username, "maria.clara")

        # Since Maria has 0 face samples, she is redirected to face enrollment
        self.assertRedirects(res_post, reverse("attendance:student_reenroll", args=[new_student.pk]))

    def test_student_claim_existing_profile(self):
        # If student record already exists without user, signup links the user to existing student
        data = {
            "student_id": self.student.student_id,
            "first_name": "Juan",
            "last_name": "Dela Cruz",
            "section": self.section.pk,
            "username": "juandc",
            "password": "secretpassword",
            "password_confirm": "secretpassword",
        }
        res_post = self.client.post(reverse("student_signup"), data)
        self.assertEqual(res_post.status_code, 302)
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.user)
        self.assertEqual(self.student.user.username, "juandc")

    def test_staff_account_manage(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            reverse("attendance:student_account_manage", args=[self.student.pk]),
            {"username": "juan_staff_created", "password": "securepassword123"},
        )
        self.assertEqual(res.status_code, 302)
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.user)
        self.assertEqual(self.student.user.username, "juan_staff_created")

    def test_student_reenroll_permissions(self):
        student_user = User.objects.create_user("student_user", "s@test.local", "pass123")
        self.student.user = student_user
        self.student.save()

        other_student = Student.objects.create(
            student_id="2025-88888",
            first_name="Pedro",
            last_name="Penduko",
            section=self.section,
        )

        self.client.force_login(student_user)

        # Enrolling own face should succeed (200 OK)
        res_own = self.client.get(reverse("attendance:student_reenroll", args=[self.student.pk]))
        self.assertEqual(res_own.status_code, 200)

        # Enrolling another student's face should be forbidden (403)
        res_other = self.client.get(reverse("attendance:student_reenroll", args=[other_student.pk]))
        self.assertEqual(res_other.status_code, 403)

    def test_admin_and_main_site_session_isolation(self):
        student_user = User.objects.create_user("student_bob", "bob@test.local", "bobpassword")
        self.student.user = student_user
        self.student.save()

        # 1. Main site login
        res_main_login = self.client.post(
            reverse("login"),
            {"username": "student_bob", "password": "bobpassword"},
        )
        self.assertEqual(res_main_login.status_code, 302)
        self.assertIn("sessionid", self.client.cookies)
        main_session_key = self.client.cookies["sessionid"].value
        self.assertNotIn("scansync_admin_sessionid", self.client.cookies)

        # 2. Admin site login
        res_admin_login = self.client.post(
            reverse("admin:login"),
            {"username": "admin", "password": "pass123"},
        )
        self.assertEqual(res_admin_login.status_code, 302)
        self.assertIn("scansync_admin_sessionid", self.client.cookies)
        admin_session_key = self.client.cookies["scansync_admin_sessionid"].value

        # The main site session key was NOT overwritten
        self.assertEqual(self.client.cookies["sessionid"].value, main_session_key)
        self.assertNotEqual(main_session_key, admin_session_key)

        # 3. Main dashboard request uses the student session
        res_dash = self.client.get(reverse("attendance:dashboard"))
        self.assertEqual(res_dash.status_code, 200)
        self.assertEqual(res_dash.context["user"], student_user)
        self.assertContains(res_dash, "Student Portal")

        # 4. Admin site request uses the superuser session
        res_admin = self.client.get(reverse("admin:index"))
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin.context["user"], self.admin)
        self.assertTrue(res_admin.context["user"].is_superuser)

        # 5. Logging out of main site does not log out of admin
        self.client.post(reverse("logout"))
        res_admin_after = self.client.get(reverse("admin:index"))
        self.assertEqual(res_admin_after.status_code, 200)
        self.assertEqual(res_admin_after.context["user"], self.admin)

    def test_teacher_enroll_student_in_class_and_shows_in_student_dashboard(self):
        # Create a second section and a student in that second section
        sec2 = Section.objects.create(name="BSIT 3-B", program="BS Information Technology", year_level=3)
        student2_user = User.objects.create_user("student_sally", "sally@test.local", "sallypass")
        student2 = Student.objects.create(
            student_id="2025-00777",
            first_name="Sally",
            last_name="Reyes",
            section=sec2,
            user=student2_user,
        )

        # Before enrollment: student2 is in BSIT 3-B, school_class is for BSIS 2-A
        self.client.force_login(student2_user)
        res_before = self.client.get(reverse("attendance:dashboard"))
        self.assertEqual(res_before.status_code, 200)
        self.assertNotContains(res_before, "IS 101")

        # Teacher logs in and adds student2 to self.school_class
        self.client.force_login(self.teacher)
        res_enroll = self.client.post(
            reverse("attendance:class_enroll_students"),
            {"school_class": self.school_class.pk, "student_ids": [student2.pk]},
        )
        self.assertEqual(res_enroll.status_code, 302)

        # Student2 is now in the class roster
        self.assertIn(student2, self.school_class.roster())

        # Student2 logs in: IS 101 now appears on their dashboard!
        self.client.force_login(student2_user)
        res_after = self.client.get(reverse("attendance:dashboard"))
        self.assertEqual(res_after.status_code, 200)
        self.assertContains(res_after, "IS 101")
        self.assertContains(res_after, "Added by Teacher")

        # Teacher can also remove the student from the class
        self.client.force_login(self.teacher)
        res_unenroll = self.client.post(
            reverse("attendance:class_unenroll_student", args=[self.school_class.pk, student2.pk]),
        )
        self.assertEqual(res_unenroll.status_code, 302)
        self.assertNotIn(student2, self.school_class.roster())

    def test_enrolled_classes_visible_even_when_not_meeting_today(self):
        # Determine a weekday that is definitely NOT today
        today = timezone.localdate()
        other_days = [d.value for d in Weekday if not self.school_class.meets_on(today) or d.value != Weekday.choices[today.weekday()][0]]
        non_today_day = other_days[0]

        # Create a class that only meets on a day different from today
        off_day_class = SchoolClass.objects.create(
            code="DM 108",
            name="Logic Formulation",
            section=self.section,
            teacher=self.teacher,
            start_time=datetime.time(10, 0),
            end_time=datetime.time(11, 30),
            meeting_days=non_today_day,
            grace_period_minutes=15,
        )

        student_user = User.objects.create_user("student_jacob", "jacob@test.local", "jacobpass")
        self.student.user = student_user
        self.student.save()

        self.client.force_login(student_user)
        res = self.client.get(reverse("attendance:dashboard"))
        self.assertEqual(res.status_code, 200)

        # Enrolled classes must include DM 108
        self.assertContains(res, "My enrolled classes")
        self.assertContains(res, "DM 108")
        self.assertContains(res, "Logic Formulation")
        self.assertContains(res, "Section BSIS 2-A")
        self.assertIn("enrolled_classes", res.context)
        enrolled_codes = [item["cls"].code for item in res.context["enrolled_classes"]]
        self.assertIn("DM 108", enrolled_codes)

    def test_student_classes_view_and_navigation(self):
        student_user = User.objects.create_user("student_maya", "maya@test.local", "mayapass")
        self.student.user = student_user
        self.student.save()

        self.client.force_login(student_user)

        # 1. Dashboard sidebar contains My Classes link
        res_dash = self.client.get(reverse("attendance:dashboard"))
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, "My Classes")
        self.assertContains(res_dash, "nav-student-classes")

        # 2. Dedicated student_classes view loads successfully
        res_classes = self.client.get(reverse("attendance:student_classes"))
        self.assertEqual(res_classes.status_code, 200)
        self.assertContains(res_classes, "Enrolled classes &amp; subjects")
        self.assertContains(res_classes, "IS 101")

        # 3. Visiting /classes/ as student gracefully routes to student_classes view
        res_classes_url = self.client.get(reverse("attendance:class_list"))
        self.assertEqual(res_classes_url.status_code, 200)
        self.assertContains(res_classes_url, "Enrolled classes &amp; subjects")

    def test_admin_user_list_shows_superadmin_access_column(self):
        self.client.post(
            reverse("admin:login"),
            {"username": "admin", "password": "pass123"},
        )
        res = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Superadmin Access")
        self.assertContains(res, "column-superadmin_access")

    def test_landing_page_renders_for_unauthenticated_visitors(self):
        # 1. Visiting root URL as unauthenticated visitor serves the landing page
        res = self.client.get(reverse("attendance:dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Attendance in a")
        self.assertContains(res, "single glance")
        self.assertContains(res, "Next-Gen AI Biometrics")

        # 2. Direct welcome URL also serves the landing page
        res_welcome = self.client.get(reverse("attendance:landing"))
        self.assertEqual(res_welcome.status_code, 200)
        self.assertContains(res_welcome, "Smart Facial Recognition")
