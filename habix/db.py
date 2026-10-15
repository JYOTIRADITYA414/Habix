"""SQLite connection management and schema initialization."""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from flask import current_app, g

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 theme TEXT NOT NULL DEFAULT 'light', study_target_minutes INTEGER NOT NULL DEFAULT 120,
 break_minutes INTEGER NOT NULL DEFAULT 10, week_start INTEGER NOT NULL DEFAULT 0,
 reminders_enabled INTEGER NOT NULL DEFAULT 1, preferred_start TEXT NOT NULL DEFAULT '08:00',
 preferred_end TEXT NOT NULL DEFAULT '22:00'
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 title TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', event_date TEXT NOT NULL,
 repeat_until TEXT, weekdays TEXT, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
 location TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'Class',
 day_order INTEGER, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_user_date ON events(user_id, event_date);
CREATE TABLE IF NOT EXISTS day_order_assignments (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 assignment_date TEXT NOT NULL, day_order INTEGER NOT NULL CHECK(day_order BETWEEN 1 AND 31),
 PRIMARY KEY(user_id, assignment_date)
);
CREATE TABLE IF NOT EXISTS tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', due_date TEXT, due_time TEXT,
 priority TEXT NOT NULL DEFAULT 'Medium', category TEXT NOT NULL DEFAULT 'General', subject TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'Pending', created_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_tasks_user_due ON tasks(user_id, due_date, status);
CREATE TABLE IF NOT EXISTS subtasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 title TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS habits (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#7067f0', frequency TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
 target_per_week INTEGER NOT NULL DEFAULT 7, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS habit_checks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
 check_date TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(habit_id, check_date)
);
CREATE INDEX IF NOT EXISTS ix_habit_checks_date ON habit_checks(habit_id, check_date);
CREATE TABLE IF NOT EXISTS study_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 subject TEXT NOT NULL, planned_minutes INTEGER NOT NULL, actual_minutes INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'planned', elapsed_seconds INTEGER NOT NULL DEFAULT 0,
 started_at TEXT, ended_at TEXT, session_date TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_study_user_date ON study_sessions(user_id, session_date);
CREATE TABLE IF NOT EXISTS exams (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', exam_date TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topics (
 id INTEGER PRIMARY KEY AUTOINCREMENT, exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
 name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Not Started', priority TEXT NOT NULL DEFAULT 'Medium'
);
CREATE TABLE IF NOT EXISTS revisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE, topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
 revision_date TEXT NOT NULL, planned_minutes INTEGER NOT NULL DEFAULT 30, completed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, source_id INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 created_at TEXT NOT NULL, read_at TEXT, UNIQUE(user_id, kind, source_id)
);
"""

MIGRATIONS = [(1, SCHEMA)]


class HybridRow(dict):
    """PostgreSQL row supporting both SQLite-style numeric and named lookup."""

    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _hybrid_row(cursor):
    if cursor.description is None:
        return lambda values: values
    columns = [column.name for column in cursor.description]
    return lambda values: HybridRow(columns, values)


class PostgresConnection:
    """Small DB-API adapter for the parameter and cursor API used by HABIX."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace("?", "%s"), parameters)

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)


def get_db():
    if "db" not in g:
        database_url = current_app.config.get("DATABASE_URL")
        if database_url:
            from importlib import import_module

            psycopg = import_module("psycopg")
            url = database_url.replace("postgres://", "postgresql://", 1)
            connection = psycopg.connect(
                url, row_factory=_hybrid_row, prepare_threshold=None, connect_timeout=10
            )
            g.db = PostgresConnection(connection)
        else:
            path = current_app.config["DATABASE"]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            g.db = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
    for version, sql in MIGRATIONS:
        if version not in applied:
            if isinstance(db, PostgresConnection):
                postgres_sql = sql.replace("PRAGMA foreign_keys = ON;", "").replace(
                    "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
                )
                db.executescript(postgres_sql)
            else:
                db.executescript(sql)
            applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            db.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?) ON CONFLICT(version) DO NOTHING",
                (version, applied_at),
            )
            db.commit()


def init_app(app):
    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        print("Initialized HABIX AI database.")

    with app.app_context():
        init_db()
