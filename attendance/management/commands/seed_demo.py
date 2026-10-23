import datetime
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from attendance.models import Section, SchoolClass, Student

User = get_user_model()

class Command(BaseCommand):
    help = "Seeds initial demo data (admin, teacher, sections, classes, and sample students)"

    def add_arguments(self, parser):
        parser.add_argument("--admin-pass", default="admin123", help="Password for default admin user")
        parser.add_argument("--teacher-pass", default="teacher123", help="Password for default teacher user")
        parser.add_argument("--student-pass", default="student123", help="Password for default student user")

    def handle(self, *args, **options):
        admin_pass = options["admin_pass"]
        teacher_pass = options["teacher_pass"]

        # 1. Admin User
        admin_user, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "first_name": "Admin",
                "last_name": "User",
                "email": "admin@scansync.local",
                "is_staff": True,
                "is_superuser": True,
            }
        )
        admin_user.set_password(admin_pass)
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.save()
        self.stdout.write(self.style.SUCCESS(f"Superuser 'admin' ready (password: {admin_pass})"))

        # 2. Teacher User
        teacher_user, created = User.objects.get_or_create(
            username="teacher",
            defaults={
                "first_name": "Jude",
                "last_name": "Teacher",
                "email": "teacher@scansync.local",
                "is_staff": False,
            }
        )
        teacher_user.set_password(teacher_pass)
        teacher_user.save()
        self.stdout.write(self.style.SUCCESS(f"Teacher 'teacher' ready (password: {teacher_pass})"))

        # 3. Sections
        sec_2a, _ = Section.objects.get_or_create(
            name="BSIS 2-A",
            defaults={"program": "BS Information Systems", "year_level": 2}
        )
        sec_3b, _ = Section.objects.get_or_create(
            name="BSIT 3-B",
            defaults={"program": "BS Information Technology", "year_level": 3}
        )
        self.stdout.write(self.style.SUCCESS("Sections 'BSIS 2-A' and 'BSIT 3-B' ready"))

        # 4. Classes
        c1, _ = SchoolClass.objects.get_or_create(
            code="IS 103",
            section=sec_2a,
            defaults={
                "name": "Logic Formulation & Design",
                "teacher": teacher_user,
                "room": "Lab 201",
                "meeting_days": "MON,TUE,WED,THU,FRI",
                "start_time": datetime.time(8, 0),
                "end_time": datetime.time(9, 30),
                "grace_period_minutes": 15,
                "absent_after_minutes": 45,
                "is_active": True,
            }
        )
        c2, _ = SchoolClass.objects.get_or_create(
            code="IT 301",
            section=sec_3b,
            defaults={
                "name": "Systems Integration & Architecture",
                "teacher": admin_user,
                "room": "Rm 304",
                "meeting_days": "MON,TUE,WED,THU,FRI",
                "start_time": datetime.time(13, 0),
                "end_time": datetime.time(15, 0),
                "grace_period_minutes": 15,
                "absent_after_minutes": 60,
                "is_active": True,
            }
        )
        self.stdout.write(self.style.SUCCESS("Classes 'IS 103' and 'IT 301' ready"))

        # 5. Student User Account
        student_user, _ = User.objects.get_or_create(
            username="student",
            defaults={
                "first_name": "Juan",
                "last_name": "Dela Cruz",
                "email": "student@scansync.local",
                "is_staff": False,
            }
        )
        student_pass = options.get("student_pass", "student123")
        student_user.set_password(student_pass)
        student_user.save()
        self.stdout.write(self.style.SUCCESS(f"Student 'student' ready (password: {student_pass})"))

        # 6. Sample Student Roster
        sample_students = [
            ("2025-00101", "Juan", "P.", "Dela Cruz", sec_2a, student_user),
            ("2025-00102", "Maria", "S.", "Santos", sec_2a, None),
            ("2025-00103", "Christian", "L.", "Reyes", sec_2a, None),
            ("2025-00104", "Angela", "M.", "Gonzales", sec_2a, None),
            ("2025-00201", "Paolo", "R.", "Mendoza", sec_3b, None),
            ("2025-00202", "Bea", "T.", "Aquino", sec_3b, None),
        ]
        created_count = 0
        for sid, fn, mn, ln, sec, u in sample_students:
            s_obj, created = Student.objects.get_or_create(
                student_id=sid,
                defaults={
                    "first_name": fn,
                    "middle_name": mn,
                    "last_name": ln,
                    "section": sec,
                    "registered_by": admin_user,
                    "is_active": True,
                    "user": u,
                }
            )
            if not created and u and s_obj.user != u:
                s_obj.user = u
                s_obj.save(update_fields=["user"])
            if created:
                created_count += 1

        self.stdout.write(self.style.SUCCESS(f"Loaded {created_count} sample students into roster"))
        self.stdout.write(self.style.SUCCESS("\nDemo setup complete! You can now run 'python manage.py runserver'."))
