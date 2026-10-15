"""Private HABIX pages and their validated actions."""
import csv
import io
import re
from calendar import month_name
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlsplit

from flask import (Blueprint, abort, flash, g, make_response, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .services import detect_conflicts, habit_stats, month_bounds, occurrences, weekly_plan

bp = Blueprint("main", __name__)
CATEGORIES = ["Class", "Study", "Gym", "Break", "Personal"]
PRIORITIES = ["Low", "Medium", "High"]
STATUSES = ["Pending", "In Progress", "Completed"]
WEEKDAYS = [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")]


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)
    return wrapped_view


def owned(table, record_id, user_id):
    if table == "events" or table == "tasks" or table == "habits" or table == "exams":
        row = get_db().execute(f"SELECT * FROM {table} WHERE id = ? AND user_id = ?", (record_id, user_id)).fetchone()
        if row is None:
            abort(404)
        return row
    abort(404)


def _valid_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _valid_time(value):
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except (TypeError, ValueError):
        return False


def _now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _session_elapsed(session_row):
    elapsed = int(session_row["elapsed_seconds"] or 0)
    if session_row["state"] == "active" and session_row["started_at"]:
        elapsed += max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(session_row["started_at"].replace("Z", "+00:00"))).total_seconds()))
    return min(elapsed, int(session_row["planned_minutes"]) * 60)


def _prefs(user_id):
    return get_db().execute("SELECT * FROM preferences WHERE user_id = ?", (user_id,)).fetchone()


def _safe_referrer(fallback):
    referrer = request.referrer
    if not referrer:
        return fallback
    parsed = urlsplit(referrer)
    if (parsed.scheme and parsed.scheme not in {"http", "https"}) or (parsed.netloc and parsed.netloc != request.host) or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return fallback
    return referrer


@bp.get("/")
def index():
    return redirect(url_for("main.dashboard" if g.user else "auth.login"))


@bp.get("/dashboard")
@login_required
def dashboard():
    db = get_db()
    today = date.today()
    today_text = today.isoformat()
    tasks_today = db.execute("SELECT * FROM tasks WHERE user_id = ? AND due_date = ? ORDER BY CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, due_time", (g.user["id"], today_text)).fetchall()
    pending_today = sum(t["status"] != "Completed" for t in tasks_today)
    complete_today = sum(t["status"] == "Completed" for t in tasks_today)
    task_total = len(tasks_today)
    progress = round(100 * complete_today / task_total) if task_total else 0
    overdue = db.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND due_date < ? AND status != 'Completed'", (g.user["id"], today_text)).fetchone()[0]
    todays_events = occurrences(db, g.user["id"], today, today)
    now_time = datetime.now().strftime("%H:%M")
    next_event = next((event for event in todays_events if event["end_time"] > now_time), None)
    study = db.execute("SELECT COALESCE(SUM(actual_minutes), 0) FROM study_sessions WHERE user_id = ? AND session_date = ? AND state = 'completed'", (g.user["id"], today_text)).fetchone()[0]
    prefs = _prefs(g.user["id"])
    target = prefs["study_target_minutes"] if prefs else 120
    habits = db.execute("SELECT * FROM habits WHERE user_id = ? ORDER BY id", (g.user["id"],)).fetchall()
    habit_rows = []
    for habit in habits:
        stats = habit_stats(db, habit, today)
        checked = db.execute("SELECT 1 FROM habit_checks WHERE habit_id = ? AND check_date = ?", (habit["id"], today_text)).fetchone() is not None
        habit_rows.append({"item": habit, "stats": stats, "checked": checked})
    due_exams = db.execute("SELECT * FROM exams WHERE user_id = ? AND exam_date >= ? ORDER BY exam_date LIMIT 3", (g.user["id"], today_text)).fetchall()
    upcoming_tasks = db.execute("SELECT * FROM tasks WHERE user_id=? AND status!='Completed' AND due_date>? ORDER BY due_date,due_time LIMIT 3", (g.user["id"], today_text)).fetchall()
    week_start = today - timedelta(days=6)
    chart_dates = [(week_start + timedelta(days=n)).isoformat() for n in range(7)]
    study_map = {row["session_date"]: row["minutes"] for row in db.execute("SELECT session_date, SUM(actual_minutes) AS minutes FROM study_sessions WHERE user_id = ? AND state = 'completed' AND session_date BETWEEN ? AND ? GROUP BY session_date", (g.user["id"], chart_dates[0], chart_dates[-1]))}
    return render_template("dashboard.html", tasks=tasks_today, pending_today=pending_today, completed_today=complete_today,
                           progress=progress, overdue=overdue, events=todays_events, next_event=next_event, now_time=now_time,
                           study=study, target=target, habits=habit_rows, exams=due_exams, upcoming_tasks=upcoming_tasks,
                           chart_labels=[date.fromisoformat(d).strftime("%a") for d in chart_dates],
                           chart_values=[round(study_map.get(d, 0) / 60, 1) for d in chart_dates])


@bp.route("/timetable", methods=("GET", "POST"))
@login_required
def timetable():
    db = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        subject = request.form.get("subject", "").strip()
        event_date = request.form.get("event_date", "")
        start_time = request.form.get("start_time", "")
        end_time = request.form.get("end_time", "")
        event_id = request.form.get("event_id", type=int)
        repeat_until = request.form.get("repeat_until", "") or None
        weekdays = sorted(set(int(day) for day in request.form.getlist("weekdays") if day.isdigit() and 0 <= int(day) <= 6))
        category = request.form.get("category", "Class")
        day_order = request.form.get("day_order", "")
        if event_id:
            owned("events", event_id, g.user["id"])
        error = None
        parsed = _valid_date(event_date)
        if not title or len(title) > 120:
            error = "Add an event title (up to 120 characters)."
        elif not parsed:
            error = "Choose a valid event date."
        elif not _valid_time(start_time) or not _valid_time(end_time) or start_time >= end_time:
            error = "Choose valid times and make sure the end is after the start."
        elif repeat_until and (not _valid_date(repeat_until) or repeat_until < event_date):
            error = "Repeat-until date must be on or after the event date."
        elif category not in CATEGORIES:
            error = "Choose a valid event category."
        elif day_order and (not day_order.isdigit() or not 1 <= int(day_order) <= 31):
            error = "Day order must be between 1 and 31."
        if error:
            flash(error, "error")
        else:
            conflicts = detect_conflicts(db, g.user["id"], event_date, start_time, end_time, event_id)
            parsed_order = int(day_order) if day_order else None
            values = (title, subject[:100], event_date, repeat_until if (weekdays or parsed_order) else None, ",".join(map(str, weekdays)) if weekdays else None,
                      start_time, end_time, request.form.get("location", "").strip()[:120],
                      request.form.get("description", "").strip()[:1000], category,
                      parsed_order)
            if event_id:
                db.execute("UPDATE events SET title=?, subject=?, event_date=?, repeat_until=?, weekdays=?, start_time=?, end_time=?, location=?, description=?, category=?, day_order=? WHERE id=? AND user_id=?", values + (event_id, g.user["id"]))
                message = "Event updated."
            else:
                db.execute("INSERT INTO events(title, subject, event_date, repeat_until, weekdays, start_time, end_time, location, description, category, day_order, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values + (g.user["id"], _now_utc()))
                message = "Event added."
            db.commit()
            if conflicts:
                flash(message + " Overlap warning: " + ", ".join(item["title"] for item in conflicts) + " also falls in this time slot.", "warning")
            else:
                flash(message, "success")
            return redirect(url_for("main.timetable", date=event_date))
    selected_date = _valid_date(request.args.get("date", "")) or date.today()
    selected_order = db.execute("SELECT day_order FROM day_order_assignments WHERE user_id=? AND assignment_date=?", (g.user["id"], selected_date.isoformat())).fetchone()
    week_start = _prefs(g.user["id"])["week_start"]
    view = request.args.get("view", "week")
    if view not in {"day", "week", "month"}:
        view = "week"
    if view == "day":
        start, end = selected_date, selected_date
    elif view == "month":
        start, end = month_bounds(selected_date.year, selected_date.month)
    else:
        start = selected_date - timedelta(days=(selected_date.weekday() - week_start) % 7)
        end = start + timedelta(days=6)
    items = occurrences(db, g.user["id"], start, end)
    entries = {}
    for item in items:
        entries.setdefault(item["occurrence_date"], []).append(item)
    month_cells = []
    if view == "month":
        first = start - timedelta(days=(start.weekday() - week_start) % 7)
        for offset in range(42):
            day = first + timedelta(days=offset)
            month_cells.append({"date": day.isoformat(), "day": day.day, "outside": day.month != selected_date.month})
    edit_event = None
    if request.args.get("edit", type=int):
        edit_event = owned("events", request.args.get("edit", type=int), g.user["id"])
    prev_date, next_date = selected_date - timedelta(days=7 if view == "week" else 1), selected_date + timedelta(days=7 if view == "week" else 1)
    if view == "month":
        prev_date = (selected_date.replace(day=1) - timedelta(days=1)).replace(day=1)
        next_date = (selected_date.replace(day=28) + timedelta(days=4)).replace(day=1)
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday_labels = [day_names[(week_start + n) % 7][:3] for n in range(7)]
    return render_template("timetable.html", events=items, selected_date=selected_date, start=start, end=end, view=view,
                           edit_event=edit_event, categories=CATEGORIES, weekdays=WEEKDAYS, prev_date=prev_date, next_date=next_date,
                           entries=entries, month_cells=month_cells, selected_order=selected_order["day_order"] if selected_order else None,
                           weekday_labels=weekday_labels)


@bp.post("/timetable/day-order")
@login_required
def assign_day_order():
    assignment_date = request.form.get("assignment_date", "")
    order_value = request.form.get("day_order", "")
    if not _valid_date(assignment_date):
        flash("Choose a valid day-order date.", "error")
        return redirect(url_for("main.timetable"))
    if not order_value:
        get_db().execute("DELETE FROM day_order_assignments WHERE user_id=? AND assignment_date=?", (g.user["id"], assignment_date))
        get_db().commit()
        flash("Day-order assignment removed.", "success")
    elif not order_value.isdigit() or not 1 <= int(order_value) <= 31:
        flash("Day order must be between 1 and 31.", "error")
    else:
        db = get_db()
        db.execute("INSERT INTO day_order_assignments(user_id,assignment_date,day_order) VALUES (?,?,?) ON CONFLICT(user_id,assignment_date) DO UPDATE SET day_order=excluded.day_order", (g.user["id"], assignment_date, int(order_value)))
        db.commit()
        flash(f"Day Order {order_value} assigned to {assignment_date}.", "success")
    return redirect(url_for("main.timetable", date=assignment_date, view=request.form.get("view", "week")))


@bp.post("/timetable/<int:event_id>/delete")
@login_required
def delete_event(event_id):
    owned("events", event_id, g.user["id"])
    get_db().execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, g.user["id"]))
    get_db().commit()
    flash("Event deleted.", "success")
    return redirect(url_for("main.timetable"))


@bp.route("/tasks", methods=("GET", "POST"))
@login_required
def tasks():
    db = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        due_date = request.form.get("due_date", "") or None
        due_time = request.form.get("due_time", "") or None
        priority = request.form.get("priority", "Medium")
        status = request.form.get("status", "Pending")
        if not title or len(title) > 180:
            flash("Add a task title (up to 180 characters).", "error")
        elif due_date and not _valid_date(due_date):
            flash("Choose a valid due date.", "error")
        elif due_time and not _valid_time(due_time):
            flash("Choose a valid due time.", "error")
        elif priority not in PRIORITIES or status not in STATUSES:
            flash("Choose a valid priority and status.", "error")
        else:
            db.execute("INSERT INTO tasks(user_id,title,description,due_date,due_time,priority,category,subject,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (g.user["id"], title, request.form.get("description", "").strip()[:3000], due_date, due_time,
                        priority, request.form.get("category", "General").strip()[:60] or "General", request.form.get("subject", "").strip()[:100], status, _now_utc()))
            db.commit()
            flash("Task added.", "success")
            return redirect(url_for("main.tasks"))
    q = request.args.get("q", "").strip()[:100]
    status_filter = request.args.get("status", "")
    priority_filter = request.args.get("priority", "")
    category_filter = request.args.get("category", "").strip()[:60]
    date_filter = request.args.get("when", "all")
    conditions, args = ["user_id = ?"], [g.user["id"]]
    if q:
        conditions.append("(title LIKE ? OR description LIKE ? OR subject LIKE ? OR category LIKE ?)")
        args.extend([f"%{q}%"] * 4)
    if status_filter in STATUSES:
        conditions.append("status = ?"); args.append(status_filter)
    if priority_filter in PRIORITIES:
        conditions.append("priority = ?"); args.append(priority_filter)
    if category_filter:
        conditions.append("category = ?"); args.append(category_filter)
    today = date.today().isoformat()
    if date_filter == "today":
        conditions.append("due_date = ?"); args.append(today)
    elif date_filter == "upcoming":
        conditions.append("due_date > ? AND status != 'Completed'"); args.append(today)
    elif date_filter == "overdue":
        conditions.append("due_date < ? AND status != 'Completed'"); args.append(today)
    sql = "SELECT * FROM tasks WHERE " + " AND ".join(conditions) + " ORDER BY CASE WHEN status='Completed' THEN 1 ELSE 0 END, due_date IS NULL, due_date, due_time"
    task_rows = db.execute(sql, args).fetchall()
    categories = [row[0] for row in db.execute("SELECT DISTINCT category FROM tasks WHERE user_id=? ORDER BY category", (g.user["id"],)).fetchall()]
    subtask_map = {task["id"]: db.execute("SELECT * FROM subtasks WHERE task_id = ? ORDER BY id", (task["id"],)).fetchall() for task in task_rows}
    return render_template("tasks.html", tasks=task_rows, subtasks=subtask_map, priorities=PRIORITIES, statuses=STATUSES,
                           q=q, status_filter=status_filter, priority_filter=priority_filter, category_filter=category_filter,
                           categories=categories, date_filter=date_filter)


@bp.route("/tasks/<int:task_id>/edit", methods=("GET", "POST"))
@login_required
def edit_task(task_id):
    task = owned("tasks", task_id, g.user["id"])
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        due_date = request.form.get("due_date", "") or None
        priority, status = request.form.get("priority", "Medium"), request.form.get("status", "Pending")
        due_time = request.form.get("due_time", "") or None
        if not title or len(title) > 180 or (due_date and not _valid_date(due_date)) or (due_time and not _valid_time(due_time)) or priority not in PRIORITIES or status not in STATUSES:
            flash("Check the title, due date/time, priority, and status.", "error")
        else:
            get_db().execute("UPDATE tasks SET title=?,description=?,due_date=?,due_time=?,priority=?,category=?,subject=?,status=?,completed_at=? WHERE id=? AND user_id=?",
                             (title, request.form.get("description", "").strip()[:3000], due_date, request.form.get("due_time", "") or None,
                              priority, request.form.get("category", "General").strip()[:60] or "General", request.form.get("subject", "").strip()[:100],
                              status, _now_utc() if status == "Completed" else None, task_id, g.user["id"]))
            get_db().commit()
            flash("Task updated.", "success")
            return redirect(url_for("main.tasks"))
    return render_template("task_edit.html", task=task, priorities=PRIORITIES, statuses=STATUSES)


@bp.post("/tasks/<int:task_id>/toggle")
@login_required
def toggle_task(task_id):
    task = owned("tasks", task_id, g.user["id"])
    status = "Completed" if task["status"] != "Completed" else "Pending"
    get_db().execute("UPDATE tasks SET status=?, completed_at=? WHERE id=? AND user_id=?", (status, _now_utc() if status == "Completed" else None, task_id, g.user["id"]))
    get_db().commit()
    flash("Task marked " + status.lower() + ".", "success")
    return redirect(_safe_referrer(url_for("main.tasks")))


@bp.post("/tasks/<int:task_id>/delete")
@login_required
def delete_task(task_id):
    owned("tasks", task_id, g.user["id"])
    get_db().execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, g.user["id"]))
    get_db().commit()
    flash("Task deleted.", "success")
    return redirect(url_for("main.tasks"))


@bp.post("/tasks/<int:task_id>/subtasks")
@login_required
def add_subtask(task_id):
    owned("tasks", task_id, g.user["id"])
    title = request.form.get("title", "").strip()
    if title and len(title) <= 180:
        get_db().execute("INSERT INTO subtasks(task_id,title) VALUES (?,?)", (task_id, title))
        get_db().commit()
        flash("Subtask added.", "success")
    else:
        flash("Subtask title is required.", "error")
    return redirect(url_for("main.tasks"))


@bp.post("/subtasks/<int:subtask_id>/toggle")
@login_required
def toggle_subtask(subtask_id):
    row = get_db().execute("SELECT s.id, s.done FROM subtasks s JOIN tasks t ON t.id=s.task_id WHERE s.id=? AND t.user_id=?", (subtask_id, g.user["id"])).fetchone()
    if not row:
        abort(404)
    get_db().execute("UPDATE subtasks SET done=? WHERE id=?", (0 if row["done"] else 1, subtask_id))
    get_db().commit()
    return redirect(url_for("main.tasks"))


@bp.route("/habits", methods=("GET", "POST"))
@login_required
def habits():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        days = sorted(set(int(d) for d in request.form.getlist("frequency") if d.isdigit() and 0 <= int(d) <= 6))
        target = request.form.get("target_per_week", "7", type=int)
        if not name or len(name) > 100 or not days or not 1 <= target <= 7:
            flash("Add a habit, select its days, and set a weekly target from 1 to 7.", "error")
        else:
            color = request.form.get("color", "#7067f0")
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                color = "#7067f0"
            db.execute("INSERT INTO habits(user_id,name,color,frequency,target_per_week,created_at) VALUES (?,?,?,?,?,?)",
                       (g.user["id"], name, color, ",".join(map(str, days)), target, _now_utc()))
            db.commit()
            flash("Habit created.", "success")
            return redirect(url_for("main.habits"))
    today = date.today()
    rows = db.execute("SELECT * FROM habits WHERE user_id = ? ORDER BY id", (g.user["id"],)).fetchall()
    habit_rows = []
    for habit in rows:
        stats = habit_stats(db, habit, today)
        checks = {row[0] for row in db.execute("SELECT check_date FROM habit_checks WHERE habit_id=? AND check_date>=?", (habit["id"], (today - timedelta(days=27)).isoformat()))}
        habit_rows.append({"item": habit, "stats": stats, "checked": today.isoformat() in checks,
                           "calendar": [(today - timedelta(days=27-n)).isoformat() in checks for n in range(28)]})
    return render_template("habits.html", habits=habit_rows, weekdays=WEEKDAYS, today=today.isoformat())


@bp.post("/habits/<int:habit_id>/check")
@login_required
def check_habit(habit_id):
    habit = owned("habits", habit_id, g.user["id"])
    check_date = request.form.get("check_date", date.today().isoformat())
    if not _valid_date(check_date):
        flash("Choose a valid check-in date.", "error")
    else:
        db = get_db()
        existing = db.execute("SELECT id FROM habit_checks WHERE habit_id=? AND check_date=?", (habit_id, check_date)).fetchone()
        if existing:
            db.execute("DELETE FROM habit_checks WHERE id=?", (existing["id"],))
            flash("Check-in removed.", "success")
        else:
            db.execute("INSERT INTO habit_checks(habit_id,check_date,created_at) VALUES (?,?,?)", (habit_id, check_date, _now_utc()))
            flash("Habit checked in.", "success")
        db.commit()
    return redirect(url_for("main.habits"))


@bp.post("/habits/<int:habit_id>/delete")
@login_required
def delete_habit(habit_id):
    owned("habits", habit_id, g.user["id"])
    get_db().execute("DELETE FROM habits WHERE id=? AND user_id=?", (habit_id, g.user["id"]))
    get_db().commit()
    flash("Habit deleted.", "success")
    return redirect(url_for("main.habits"))


@bp.get("/study")
@login_required
def study():
    db = get_db()
    active = db.execute("SELECT * FROM study_sessions WHERE user_id=? AND state IN ('active','paused') ORDER BY id DESC LIMIT 1", (g.user["id"],)).fetchone()
    history = db.execute("SELECT * FROM study_sessions WHERE user_id=? AND state='completed' ORDER BY session_date DESC, id DESC LIMIT 20", (g.user["id"],)).fetchall()
    subject_rows = db.execute("SELECT subject, SUM(actual_minutes) AS minutes FROM study_sessions WHERE user_id=? AND state='completed' GROUP BY subject ORDER BY minutes DESC", (g.user["id"],)).fetchall()
    prefs = _prefs(g.user["id"])
    return render_template("study.html", active=active, active_data=dict(active) if active else None,
                           history=history, subjects=subject_rows, break_minutes=prefs["break_minutes"], target=prefs["study_target_minutes"])


@bp.post("/study/control")
@login_required
def study_control():
    db = get_db()
    action = request.form.get("action", "")
    active = db.execute("SELECT * FROM study_sessions WHERE user_id=? AND state IN ('active','paused') ORDER BY id DESC LIMIT 1", (g.user["id"],)).fetchone()
    if action == "start":
        subject = request.form.get("subject", "").strip()[:100]
        minutes = request.form.get("planned_minutes", 25, type=int)
        if not subject or not 1 <= minutes <= 600:
            return {"error": "Add a subject and choose 1–600 focus minutes."}, 400
        if active:
            return {"error": "Finish or stop your current session first."}, 409
        cur = db.execute("INSERT INTO study_sessions(user_id,subject,planned_minutes,state,started_at,session_date,created_at) VALUES (?,?,?,'active',?,?,?) RETURNING id",
                         (g.user["id"], subject, minutes, _now_utc(), date.today().isoformat(), _now_utc()))
        session_id = cur.fetchone()["id"]
        db.commit()
        return {"id": session_id, "state": "active", "subject": subject, "planned_minutes": minutes, "elapsed_seconds": 0, "started_at": _now_utc()}
    if not active:
        return {"error": "There is no active study session."}, 404
    if action == "pause" and active["state"] == "active":
        elapsed = _session_elapsed(active)
        db.execute("UPDATE study_sessions SET state='paused',elapsed_seconds=?,started_at=NULL WHERE id=? AND user_id=?", (elapsed, active["id"], g.user["id"]))
        db.commit()
        return {"state": "paused", "elapsed_seconds": elapsed}
    if action == "resume" and active["state"] == "paused":
        db.execute("UPDATE study_sessions SET state='active',started_at=? WHERE id=? AND user_id=?", (_now_utc(), active["id"], g.user["id"]))
        db.commit()
        return {"state": "active", "started_at": _now_utc(), "elapsed_seconds": active["elapsed_seconds"]}
    if action == "stop":
        elapsed = _session_elapsed(active)
        actual_minutes = round(elapsed / 60)
        db.execute("UPDATE study_sessions SET state='completed',elapsed_seconds=?,actual_minutes=?,ended_at=?,started_at=NULL WHERE id=? AND user_id=?",
                   (elapsed, actual_minutes, _now_utc(), active["id"], g.user["id"]))
        db.commit()
        return {"state": "completed", "actual_minutes": actual_minutes}
    return {"error": "That timer action is not available now."}, 400


@bp.get("/exams")
@login_required
def exams():
    db = get_db()
    rows = db.execute("SELECT * FROM exams WHERE user_id=? ORDER BY exam_date", (g.user["id"],)).fetchall()
    today = date.today()
    items = []
    for exam in rows:
        topics = db.execute("SELECT * FROM topics WHERE exam_id=? ORDER BY CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END,id", (exam["id"],)).fetchall()
        done = sum(topic["status"] == "Completed" for topic in topics)
        revisions = db.execute("SELECT * FROM revisions WHERE exam_id=? ORDER BY revision_date,id", (exam["id"],)).fetchall()
        items.append({"item": exam, "topics": topics, "done": done, "revisions": revisions,
                      "days_left": (date.fromisoformat(exam["exam_date"]) - today).days})
    if request.args.get("plan", type=int):
        prefs = _prefs(g.user["id"])
        plan = weekly_plan(db, g.user["id"], request.args.get("plan", type=int), prefs["study_target_minutes"])
    else:
        plan = None
    return render_template("exams.html", exams=items, plan=plan, priorities=PRIORITIES,
                           statuses=["Not Started", "In Progress", "Completed"])


@bp.post("/exams")
@login_required
def add_exam():
    name, exam_date = request.form.get("name", "").strip(), request.form.get("exam_date", "")
    if not name or len(name) > 160 or not _valid_date(exam_date):
        flash("Add an exam name and valid date.", "error")
    else:
        get_db().execute("INSERT INTO exams(user_id,name,subject,exam_date,notes,created_at) VALUES (?,?,?,?,?,?)",
                         (g.user["id"], name, request.form.get("subject", "").strip()[:100], exam_date, request.form.get("notes", "").strip()[:1000], _now_utc()))
        get_db().commit()
        flash("Exam added.", "success")
    return redirect(url_for("main.exams"))


@bp.post("/exams/<int:exam_id>/delete")
@login_required
def delete_exam(exam_id):
    owned("exams", exam_id, g.user["id"])
    get_db().execute("DELETE FROM exams WHERE id=? AND user_id=?", (exam_id, g.user["id"]))
    get_db().commit()
    flash("Exam deleted.", "success")
    return redirect(url_for("main.exams"))


@bp.post("/exams/<int:exam_id>/topics")
@login_required
def add_topic(exam_id):
    owned("exams", exam_id, g.user["id"])
    name, priority = request.form.get("name", "").strip(), request.form.get("priority", "Medium")
    if not name or len(name) > 180 or priority not in PRIORITIES:
        flash("Add a topic and choose its priority.", "error")
    else:
        get_db().execute("INSERT INTO topics(exam_id,name,priority) VALUES (?,?,?)", (exam_id, name, priority))
        get_db().commit()
        flash("Syllabus topic added.", "success")
    return redirect(url_for("main.exams"))


@bp.post("/topics/<int:topic_id>/status")
@login_required
def update_topic(topic_id):
    row = get_db().execute("SELECT t.id FROM topics t JOIN exams e ON e.id=t.exam_id WHERE t.id=? AND e.user_id=?", (topic_id, g.user["id"])).fetchone()
    if not row:
        abort(404)
    status = request.form.get("status", "")
    if status not in {"Not Started", "In Progress", "Completed"}:
        abort(400)
    get_db().execute("UPDATE topics SET status=? WHERE id=?", (status, topic_id))
    get_db().commit()
    return redirect(url_for("main.exams"))


@bp.post("/exams/<int:exam_id>/revisions")
@login_required
def add_revision(exam_id):
    owned("exams", exam_id, g.user["id"])
    revision_date = request.form.get("revision_date", date.today().isoformat())
    minutes = request.form.get("planned_minutes", 30, type=int)
    topic_id = request.form.get("topic_id", type=int) or None
    valid_topic = not topic_id or get_db().execute("SELECT id FROM topics WHERE id=? AND exam_id=?", (topic_id, exam_id)).fetchone()
    if not _valid_date(revision_date) or not 1 <= minutes <= 600 or not valid_topic:
        flash("Check the revision date, duration, and selected topic.", "error")
    else:
        get_db().execute("INSERT INTO revisions(user_id,exam_id,topic_id,revision_date,planned_minutes) VALUES (?,?,?,?,?)", (g.user["id"], exam_id, topic_id, revision_date, minutes))
        get_db().commit()
        flash("Revision session added.", "success")
    return redirect(url_for("main.exams"))


@bp.post("/revisions/<int:revision_id>/toggle")
@login_required
def toggle_revision(revision_id):
    row = get_db().execute("SELECT r.completed FROM revisions r WHERE r.id=? AND r.user_id=?", (revision_id, g.user["id"])).fetchone()
    if not row:
        abort(404)
    get_db().execute("UPDATE revisions SET completed=? WHERE id=? AND user_id=?", (0 if row["completed"] else 1, revision_id, g.user["id"]))
    get_db().commit()
    return redirect(url_for("main.exams"))


@bp.get("/analytics")
@login_required
def analytics():
    db = get_db()
    span = request.args.get("span", "7")
    days = int(span) if span in {"7", "30"} else 7
    start = date.today() - timedelta(days=days - 1)
    rows = db.execute("SELECT session_date,SUM(actual_minutes) AS minutes,SUM(planned_minutes) AS planned FROM study_sessions WHERE user_id=? AND state='completed' AND session_date BETWEEN ? AND ? GROUP BY session_date", (g.user["id"], start.isoformat(), date.today().isoformat())).fetchall()
    values = {row["session_date"]: row["minutes"] for row in rows}
    planned_values = {row["session_date"]: row["planned"] for row in rows}
    labels = [(start + timedelta(days=n)) for n in range(days)]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    task_stats = db.execute("SELECT COUNT(*) total,SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) completed FROM tasks WHERE user_id=? AND created_at>=?", (g.user["id"], cutoff)).fetchone()
    task_total, task_done = task_stats["total"] or 0, task_stats["completed"] or 0
    habits = db.execute("SELECT h.id,h.name,h.frequency,COUNT(c.id) done FROM habits h LEFT JOIN habit_checks c ON c.habit_id=h.id AND c.check_date>=? WHERE h.user_id=? GROUP BY h.id", (start.isoformat(), g.user["id"])).fetchall()
    habit_names, habit_rates = [], []
    for habit in habits:
        scheduled_days = {int(d) for d in habit["frequency"].split(",") if d.isdigit()}
        scheduled = sum((start + timedelta(days=n)).weekday() in scheduled_days for n in range(days))
        habit_names.append(habit["name"])
        habit_rates.append(round(100 * habit["done"] / scheduled) if scheduled else 0)
    subject = db.execute("SELECT subject,SUM(actual_minutes) minutes FROM study_sessions WHERE user_id=? AND state='completed' AND session_date>=? GROUP BY subject ORDER BY minutes DESC LIMIT 8", (g.user["id"], start.isoformat())).fetchall()
    return render_template("analytics.html", span=days, labels=[d.strftime("%d %b") for d in labels],
                           study_hours=[round(values.get(d.isoformat(), 0) / 60, 2) for d in labels],
                           planned_hours=[round(planned_values.get(d.isoformat(), 0) / 60, 2) for d in labels],
                           task_rate=round(100 * task_done / task_total) if task_total else 0, task_done=task_done, task_total=task_total,
                           habit_names=habit_names, habit_rates=habit_rates, subjects=[r["subject"] or "Unspecified" for r in subject],
                           subject_hours=[round(r["minutes"] / 60, 2) for r in subject])


@bp.get("/calendar")
@login_required
def calendar():
    selected = _valid_date(request.args.get("date", "")) or date.today()
    start, end = month_bounds(selected.year, selected.month)
    db = get_db()
    events = occurrences(db, g.user["id"], start, end)
    tasks_rows = db.execute("SELECT * FROM tasks WHERE user_id=? AND due_date BETWEEN ? AND ? ORDER BY due_date,due_time", (g.user["id"], start.isoformat(), end.isoformat())).fetchall()
    exam_rows = db.execute("SELECT * FROM exams WHERE user_id=? AND exam_date BETWEEN ? AND ? ORDER BY exam_date", (g.user["id"], start.isoformat(), end.isoformat())).fetchall()
    entries = {}
    for event in events:
        entries.setdefault(event["occurrence_date"], []).append({"type": "event", "title": event["title"], "time": event["start_time"], "category": event["category"]})
    for task in tasks_rows:
        entries.setdefault(task["due_date"], []).append({"type": "task", "title": task["title"], "time": task["due_time"], "category": task["priority"]})
    for exam in exam_rows:
        entries.setdefault(exam["exam_date"], []).append({"type": "exam", "title": exam["name"], "time": "", "category": "Exam"})
    calendar_cells = []
    week_start = _prefs(g.user["id"])["week_start"]
    first_cell = start - timedelta(days=(start.weekday() - week_start) % 7)
    for offset in range(42):
        day = first_cell + timedelta(days=offset)
        calendar_cells.append({"date": day.isoformat(), "day": day.day, "outside": day.month != selected.month})
    upcoming = []
    for day, daily_items in entries.items():
        for item in daily_items:
            upcoming.append({"date": day, "title": item["title"], "type": item["type"], "label": item["type"].capitalize()})
    upcoming.sort(key=lambda item: (item["date"], item["title"]))
    previous = (selected.replace(day=1) - timedelta(days=1)).replace(day=1)
    following = (selected.replace(day=28) + timedelta(days=4)).replace(day=1)
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday_labels = [day_names[(week_start + n) % 7][:3] for n in range(7)]
    return render_template("calendar.html", selected=selected, month=month_name[selected.month], year=selected.year,
                           start=start, end=end, entries=entries, prev=previous, next=following,
                           calendar_cells=calendar_cells, upcoming=upcoming[:20], weekday_labels=weekday_labels)


@bp.route("/settings", methods=("GET", "POST"))
@login_required
def settings():
    db = get_db()
    prefs = _prefs(g.user["id"])
    if request.method == "POST":
        action = request.form.get("action", "preferences")
        if action == "profile":
            name, email = request.form.get("name", "").strip(), request.form.get("email", "").strip().lower()
            current = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            user_full = db.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
            duplicate = db.execute("SELECT id FROM users WHERE email=? AND id!=?", (email, g.user["id"])).fetchone()
            if not name or len(name) > 80 or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or duplicate:
                flash("Check your name and email address.", "error")
            elif new_password and (not check_password_hash(user_full["password_hash"], current) or len(new_password) < 8 or len(new_password) > 128):
                flash("Enter your current password and a new password of at least 8 characters.", "error")
            else:
                if new_password:
                    db.execute("UPDATE users SET name=?,email=?,password_hash=? WHERE id=?", (name, email, generate_password_hash(new_password), g.user["id"]))
                else:
                    db.execute("UPDATE users SET name=?,email=? WHERE id=?", (name, email, g.user["id"]))
                db.commit()
                flash("Profile updated.", "success")
                return redirect(url_for("main.settings"))
        else:
            target = request.form.get("study_target_minutes", 120, type=int)
            break_minutes = request.form.get("break_minutes", 10, type=int)
            theme = request.form.get("theme", "light")
            week_start = request.form.get("week_start", 0, type=int)
            preferred_start = request.form.get("preferred_start", "08:00")
            preferred_end = request.form.get("preferred_end", "22:00")
            if not 15 <= target <= 1440 or not 1 <= break_minutes <= 120 or theme not in {"light", "dark"} or week_start not in range(7) or not _valid_time(preferred_start) or not _valid_time(preferred_end):
                flash("Check your study target, break length, theme, and week start.", "error")
            else:
                db.execute("UPDATE preferences SET theme=?,study_target_minutes=?,break_minutes=?,week_start=?,reminders_enabled=?,preferred_start=?,preferred_end=? WHERE user_id=?",
                           (theme, target, break_minutes, week_start, 1 if request.form.get("reminders_enabled") else 0,
                            preferred_start, preferred_end, g.user["id"]))
                db.commit()
                flash("Preferences saved.", "success")
                return redirect(url_for("main.settings"))
    return render_template("settings.html", prefs=prefs)


@bp.get("/export/<kind>.csv")
@login_required
def export_csv(kind):
    if kind not in {"tasks", "events", "study"}:
        abort(404)
    columns = {"tasks": ("title,description,due_date,due_time,priority,category,subject,status", "SELECT title,description,due_date,due_time,priority,category,subject,status FROM tasks WHERE user_id=? ORDER BY due_date"),
               "events": ("title,subject,event_date,repeat_until,weekdays,start_time,end_time,location,category,day_order", "SELECT title,subject,event_date,repeat_until,weekdays,start_time,end_time,location,category,day_order FROM events WHERE user_id=? ORDER BY event_date,start_time"),
               "study": ("subject,session_date,planned_minutes,actual_minutes,state", "SELECT subject,session_date,planned_minutes,actual_minutes,state FROM study_sessions WHERE user_id=? ORDER BY session_date")}
    header, sql = columns[kind]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(header.split(","))
    for row in get_db().execute(sql, (g.user["id"],)):
        writer.writerow([("'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value) for value in row])
    response = make_response(buffer.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=habix-{kind}-{date.today().isoformat()}.csv"
    return response


@bp.get("/notifications")
@login_required
def notifications():
    rows = get_db().execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (g.user["id"],)).fetchall()
    return render_template("notifications.html", notifications=rows)


@bp.post("/notifications/read")
@login_required
def mark_notifications_read():
    get_db().execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (_now_utc(), g.user["id"]))
    get_db().commit()
    flash("Notifications marked as read.", "success")
    return redirect(url_for("main.notifications"))


@bp.route("/assistant", methods=("GET", "POST"))
@login_required
def assistant():
    answer = None
    if request.method == "POST":
        prompt = request.form.get("prompt", "").strip()[:500]
        lower = prompt.lower()
        db = get_db()
        if "neglect" in lower or "subject" in lower:
            row = db.execute("SELECT subject,COALESCE(SUM(actual_minutes),0) minutes FROM study_sessions WHERE user_id=? AND state='completed' AND session_date>=? GROUP BY subject ORDER BY minutes ASC LIMIT 1", (g.user["id"], (date.today()-timedelta(days=30)).isoformat())).fetchone()
            answer = f"Your least-studied subject in the last 30 days is {row['subject']} ({row['minutes']} recorded minutes)." if row else "I don't have enough completed study sessions to compare subjects yet."
        elif "exam" in lower or "revision" in lower:
            exam = db.execute("SELECT id,name FROM exams WHERE user_id=? AND exam_date>=? ORDER BY exam_date LIMIT 1", (g.user["id"], date.today().isoformat())).fetchone()
            if exam:
                plan = weekly_plan(db, g.user["id"], exam["id"], _prefs(g.user["id"])["study_target_minutes"])
                answer = f"For {exam['name']}, {plan['days_left']} day(s) remain. Start with these priority topics: " + ("; ".join(f"{item['date']}: {item['topic']} ({item['minutes']} min)" for item in plan["plan"][:5]) or "your syllabus is already marked complete.")
            else:
                answer = "Add an upcoming exam and syllabus topics first; then I can draft a revision sequence."
        elif "week" in lower or "timetable" in lower:
            events = occurrences(db, g.user["id"], date.today(), date.today()+timedelta(days=6))
            answer = f"I found {len(events)} timetable event(s) for the next seven days." + (" Your first is " + events[0]["title"] + " on " + events[0]["occurrence_date"] + " at " + events[0]["start_time"] + "." if events else " Add recurring classes in Timetable to build out your week.")
        else:
            today = date.today()
            events = occurrences(db, g.user["id"], today, today)
            tasks_today = db.execute("SELECT title,due_time FROM tasks WHERE user_id=? AND due_date=? AND status!='Completed' ORDER BY due_time", (g.user["id"], today.isoformat())).fetchall()
            answer = "Here is a simple plan for today: " + ("Classes: " + ", ".join(f"{e['start_time']} {e['title']}" for e in events) + ". " if events else "No classes are on your timetable today. ") + ("Due tasks: " + ", ".join(t["title"] for t in tasks_today) + ". " if tasks_today else "No tasks are due today. ") + "Protect a focus block that fits your daily target, and leave your configured break between sessions."
        if not prompt:
            answer = "Type a request or choose one of the examples to get a rule-based suggestion."
    return render_template("assistant.html", answer=answer)
