"""Flask application factory and shared request protections."""
import hmac
import os
import secrets
from datetime import date, datetime, timedelta, timezone

from flask import Flask, abort, g, request, session
from config import Config

from .db import close_db, get_db, init_app as init_db_app


def format_date_label(value):
    if not value:
        return "—"
    try:
        parsed = date.fromisoformat(str(value)[:10])
        return parsed.strftime("%a, %b ") + str(parsed.day)
    except (ValueError, TypeError):
        return value


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)
    init_db_app(app)

    from .auth import bp as auth_bp
    from .views import bp as main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    app.teardown_appcontext(close_db)

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            g.user = get_db().execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
            if g.user is None:
                session.clear()
        if request.method == "POST":
            expected = session.get("csrf_token", "")
            provided = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
            if not expected or not provided or not hmac.compare_digest(expected, provided):
                abort(400, description="Your session token expired. Refresh the page and try again.")
        if g.user and request.method in {"GET", "HEAD"} and request.endpoint not in {"static", "auth.logout"}:
            refresh_notifications(g.user["id"])

    app.add_template_filter(format_date_label, "date_label")

    @app.context_processor
    def template_helpers():
        from markupsafe import Markup
        def csrf_field():
            if "csrf_token" not in session:
                session["csrf_token"] = secrets.token_urlsafe(32)
            return Markup('<input type="hidden" name="csrf_token" value="{}">'.format(session["csrf_token"]))

        prefs = None
        unread_count = 0
        if g.user:
            db = get_db()
            prefs = db.execute("SELECT * FROM preferences WHERE user_id = ?", (g.user["id"],)).fetchone()
            unread_count = db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (g.user["id"],)).fetchone()[0]
        return {"csrf_field": csrf_field, "date_label": format_date_label, "today": date.today().isoformat(),
                "prefs": prefs, "unread_count": unread_count}

    @app.template_filter("clock")
    def clock_filter(value):
        if not value:
            return ""
        try:
            return datetime.strptime(value, "%H:%M").strftime("%I:%M %p").lstrip("0")
        except (ValueError, TypeError):
            return value

    @app.template_filter("timeago")
    def timeago(value):
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            delta = datetime.now(dt.tzinfo) - dt if dt.tzinfo else datetime.utcnow() - dt
            if delta < timedelta(minutes=1):
                return "just now"
            if delta < timedelta(hours=1):
                return f"{int(delta.total_seconds() // 60)}m ago"
            if delta < timedelta(days=1):
                return f"{int(delta.total_seconds() // 3600)}h ago"
            return f"{int(delta.total_seconds() // 86400)}d ago"
        except (ValueError, TypeError):
            return value

    return app


def refresh_notifications(user_id):
    """Create idempotent in-app alerts for due work; no browser/email delivery is claimed."""
    db = get_db()
    prefs = db.execute("SELECT reminders_enabled FROM preferences WHERE user_id = ?", (user_id,)).fetchone()
    if not prefs or not prefs["reminders_enabled"]:
        return
    today = date.today()
    for row in db.execute(
        "SELECT id, title, due_date FROM tasks WHERE user_id = ? AND status != 'Completed' AND due_date BETWEEN ? AND ?",
        (user_id, today.isoformat(), (today + timedelta(days=1)).isoformat()),
    ):
        kind = "task_due"
        db.execute(
            "INSERT INTO notifications(user_id, kind, source_id, title, body, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, kind, source_id) DO NOTHING",
            (user_id, kind, row["id"], "Task coming up", f"{row['title']} is due {row['due_date']}.", datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")),
        )
    for row in db.execute(
        "SELECT id, name, exam_date FROM exams WHERE user_id = ? AND exam_date BETWEEN ? AND ?",
        (user_id, today.isoformat(), (today + timedelta(days=7)).isoformat()),
    ):
        db.execute(
            "INSERT INTO notifications(user_id, kind, source_id, title, body, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, kind, source_id) DO NOTHING",
            (user_id, "exam_soon", row["id"], "Exam approaching", f"{row['name']} is on {row['exam_date']}.", datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")),
        )
    db.commit()
