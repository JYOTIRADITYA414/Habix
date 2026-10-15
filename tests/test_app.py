from datetime import date, timedelta

from werkzeug.security import check_password_hash

from habix.db import get_db
from habix.services import detect_conflicts, habit_stats, occurrences, weekly_plan


def test_register_hashes_password_and_private_pages_require_login(client, app, register_user):
    response = client.get("/dashboard")
    assert response.status_code == 302
    register_user(client)
    assert client.get("/dashboard").status_code == 200
    with app.app_context():
        user = get_db().execute("SELECT * FROM users WHERE email=?", ("alex@example.test",)).fetchone()
        assert user["password_hash"] != "correct-horse-123"
        assert check_password_hash(user["password_hash"], "correct-horse-123")


def test_all_primary_pages_render_for_a_signed_in_student(client, register_user):
    register_user(client)
    pages = ["/dashboard", "/timetable?view=day", "/timetable?view=week", "/timetable?view=month",
             "/tasks", "/habits", "/study", "/exams", "/analytics", "/calendar", "/settings",
             "/assistant", "/notifications"]
    for page in pages:
        response = client.get(page)
        assert response.status_code == 200, f"{page} did not render: {response.status_code}"


def test_saved_planner_records_render_in_their_sections(client, app, register_user, csrf_token):
    register_user(client)
    today = date.today().isoformat()
    token = csrf_token(client, "/tasks")
    client.post("/tasks", data={"csrf_token": token, "title": "History report", "due_date": today,
                                "priority": "High", "category": "Assignment", "subject": "History"})
    assert b"History report" in client.get("/tasks").data

    token = csrf_token(client, "/timetable")
    client.post("/timetable", data={"csrf_token": token, "title": "History lecture", "event_date": today,
                                   "start_time": "10:00", "end_time": "11:00", "category": "Class"})
    assert b"History lecture" in client.get("/timetable?view=month").data

    token = csrf_token(client, "/habits")
    client.post("/habits", data={"csrf_token": token, "name": "Read a chapter", "frequency": ["0", "1", "2", "3", "4"], "target_per_week": "5"})
    assert b"Read a chapter" in client.get("/habits").data

    token = csrf_token(client, "/study")
    headers = {"X-CSRF-Token": token}
    client.post("/study/control", data={"action": "start", "subject": "History", "planned_minutes": "25"}, headers=headers)
    client.post("/study/control", data={"action": "stop"}, headers=headers)
    assert client.get("/analytics").status_code == 200

    token = csrf_token(client, "/exams")
    client.post("/exams", data={"csrf_token": token, "name": "History exam", "subject": "History",
                                "exam_date": (date.today() + timedelta(days=5)).isoformat()})
    with app.app_context():
        exam_id = get_db().execute("SELECT id FROM exams WHERE name=?", ("History exam",)).fetchone()["id"]
    token = csrf_token(client, "/exams")
    client.post(f"/exams/{exam_id}/topics", data={"csrf_token": token, "name": "Ancient cities", "priority": "High"})
    token = csrf_token(client, "/exams")
    client.post(f"/exams/{exam_id}/revisions", data={"csrf_token": token, "revision_date": today, "planned_minutes": "30"})
    assert b"Ancient cities" in client.get(f"/exams?plan={exam_id}").data
    assert b"History exam" in client.get("/calendar").data
    assert b"Here is a simple plan for today" in client.post("/assistant", data={"csrf_token": csrf_token(client, "/assistant"), "prompt": "Plan my day"}).data
    assert b"Task coming up" in client.get("/notifications").data


def test_task_cannot_be_read_or_changed_by_another_account(client, app, register_user, csrf_token):
    register_user(client)
    token = csrf_token(client, "/tasks")
    client.post("/tasks", data={"csrf_token": token, "title": "Private assignment", "priority": "High", "status": "Pending"})
    with app.app_context():
        task_id = get_db().execute("SELECT id FROM tasks WHERE title=?", ("Private assignment",)).fetchone()["id"]

    other = app.test_client()
    register_user(other, "Sam Student", "sam@example.test", "another-password")
    assert other.get(f"/tasks/{task_id}/edit").status_code == 404
    token = csrf_token(other, "/tasks")
    assert other.post(f"/tasks/{task_id}/toggle", data={"csrf_token": token}).status_code == 404
    with app.app_context():
        assert get_db().execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()["status"] == "Pending"


def test_timetable_warns_on_overlapping_events_and_rejects_bad_ranges(client, register_user, csrf_token):
    register_user(client)
    day = (date.today() + timedelta(days=1)).isoformat()
    token = csrf_token(client, "/timetable")
    first = {"csrf_token": token, "title": "Chemistry", "event_date": day, "start_time": "09:00", "end_time": "10:30", "category": "Class"}
    assert client.post("/timetable", data=first).status_code == 302
    token = csrf_token(client, "/timetable")
    second = {"csrf_token": token, "title": "Library study", "event_date": day, "start_time": "10:00", "end_time": "11:00", "category": "Study"}
    response = client.post("/timetable", data=second, follow_redirects=True)
    assert b"Overlap warning" in response.data

    token = csrf_token(client, "/timetable")
    invalid = {"csrf_token": token, "title": "Invalid", "event_date": day, "start_time": "13:00", "end_time": "12:00", "category": "Class"}
    response = client.post("/timetable", data=invalid, follow_redirects=True)
    assert b"end is after the start" in response.data


def test_day_order_classes_only_appear_on_assigned_dates(client, app, register_user, csrf_token):
    register_user(client)
    anchor = date.today()
    assigned_date = anchor + timedelta(days=2)
    with app.app_context():
        user_id = get_db().execute("SELECT id FROM users WHERE email=?", ("alex@example.test",)).fetchone()["id"]
        db = get_db()
        db.execute("INSERT INTO events(user_id,title,event_date,start_time,end_time,category,day_order,created_at) VALUES (?,?,?,?,?,?,?,?)",
                   (user_id, "Day Order 2 class", anchor.isoformat(), "09:00", "10:00", "Class", 2, "2026-01-01T00:00:00Z"))
        db.execute("INSERT INTO day_order_assignments(user_id,assignment_date,day_order) VALUES (?,?,?)", (user_id, assigned_date.isoformat(), 2))
        db.commit()
        found = occurrences(db, user_id, assigned_date, assigned_date)
        assert [item["title"] for item in found] == ["Day Order 2 class"]
        assert not occurrences(db, user_id, anchor, anchor)


def test_focus_timer_state_can_pause_resume_and_save(client, app, register_user, csrf_token):
    register_user(client)
    token = csrf_token(client, "/study")
    headers = {"X-CSRF-Token": token}
    started = client.post("/study/control", data={"action": "start", "subject": "Physics", "planned_minutes": "25"}, headers=headers)
    assert started.status_code == 200
    assert started.json["state"] == "active"
    paused = client.post("/study/control", data={"action": "pause"}, headers=headers)
    assert paused.status_code == 200
    assert paused.json["state"] == "paused"
    assert b"Physics" in client.get("/study").data
    resumed = client.post("/study/control", data={"action": "resume"}, headers=headers)
    assert resumed.status_code == 200
    stopped = client.post("/study/control", data={"action": "stop"}, headers=headers)
    assert stopped.status_code == 200
    with app.app_context():
        row = get_db().execute("SELECT * FROM study_sessions ORDER BY id DESC LIMIT 1").fetchone()
        assert row["state"] == "completed"
        assert 0 <= row["actual_minutes"] <= row["planned_minutes"]


def test_habit_streak_and_completion_rates_are_derived_from_checkins(client, app, register_user):
    register_user(client)
    today = date.today()
    with app.app_context():
        db = get_db()
        user_id = db.execute("SELECT id FROM users WHERE email=?", ("alex@example.test",)).fetchone()["id"]
        cur = db.execute("INSERT INTO habits(user_id,name,frequency,target_per_week,created_at) VALUES (?,?,?,?,?)",
                         (user_id, "Reading", "0,1,2,3,4,5,6", 7, "2026-01-01T00:00:00Z"))
        habit_id = cur.lastrowid
        for offset in range(3):
            checked = today - timedelta(days=offset)
            db.execute("INSERT INTO habit_checks(habit_id,check_date,created_at) VALUES (?,?,?)", (habit_id, checked.isoformat(), "2026-01-01T00:00:00Z"))
        db.commit()
        habit = db.execute("SELECT * FROM habits WHERE id=?", (habit_id,)).fetchone()
        result = habit_stats(db, habit, today)
        assert result["streak"] == 3
        assert result["longest"] == 3
        assert result["week_done"] == 3
        assert result["week_rate"] <= 100


def test_weekday_habit_streak_skips_unscheduled_days(client, app, register_user):
    register_user(client)
    with app.app_context():
        db = get_db()
        user_id = db.execute("SELECT id FROM users WHERE email=?", ("alex@example.test",)).fetchone()["id"]
        cur = db.execute("INSERT INTO habits(user_id,name,frequency,target_per_week,created_at) VALUES (?,?,?,?,?)",
                         (user_id, "Weekday reading", "0,1,2,3,4", 5, "2026-01-01T00:00:00Z"))
        habit_id = cur.lastrowid
        fixed_today = date(2026, 10, 13)  # Tuesday
        for checked in [date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13)]:
            db.execute("INSERT INTO habit_checks(habit_id,check_date,created_at) VALUES (?,?,?)", (habit_id, checked.isoformat(), "2026-01-01T00:00:00Z"))
        db.commit()
        stats = habit_stats(db, db.execute("SELECT * FROM habits WHERE id=?", (habit_id,)).fetchone(), fixed_today)
        assert stats["streak"] == 3
        assert stats["longest"] == 3


def test_rule_plan_respects_daily_study_target_and_reports_unscheduled_topics(client, app, register_user):
    register_user(client)
    with app.app_context():
        db = get_db()
        user_id = db.execute("SELECT id FROM users WHERE email=?", ("alex@example.test",)).fetchone()["id"]
        exam_id = db.execute("INSERT INTO exams(user_id,name,exam_date,created_at) VALUES (?,?,?,?)",
                             (user_id, "Tomorrow's exam", (date.today() + timedelta(days=1)).isoformat(), "2026-01-01T00:00:00Z")).lastrowid
        for n in range(7):
            db.execute("INSERT INTO topics(exam_id,name,priority) VALUES (?,?,?)", (exam_id, f"Topic {n}", "High" if n < 2 else "Low"))
        db.commit()
        result = weekly_plan(db, user_id, exam_id, 60)
        assert sum(item["minutes"] for item in result["plan"]) <= 60
        assert result["remaining"] == 7
        assert result["unscheduled"] == 4
        assert result["plan"][0]["priority"] == "High"
