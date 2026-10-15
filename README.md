# HABIX AI — Your Smart Student Life Manager

A responsive Flask and SQLite student planner for classes, tasks, habits, focus sessions, exams, and progress. The app creates its SQLite database automatically on first run and keeps user records scoped to the signed-in account.

## Windows desktop app

The Electron edition runs HABIX in its own desktop window and bundles the Python service, so users do not need to install Python to run the finished app. Its SQLite database and generated secret key are stored in Electron's per-user app-data folder; uninstalling or moving the app does not remove that data.

To build the Windows desktop app from source, install Python 3.10+ and Node.js (which includes `npm`), then run these commands in PowerShell:

```powershell
python -m pip install -r desktop-requirements.txt
Set-ExecutionPolicy -Scope Process Bypass
./build-desktop.ps1
```

The app launches from `desktop-dist/win-unpacked/HABIX AI.exe`. Keep the full `win-unpacked` folder in a permanent location because the Desktop shortcut points to its executable. The Electron wrapper binds the local service to `127.0.0.1`, chooses an available port at launch, and stops it when the app closes. Backend logs are written to the same per-user app-data folder.

For desktop development, first build the backend as above, run `npm install` and `node ./node_modules/electron/install.js`, then use `npm start` from the project folder.

## Run locally on Windows

1. Install Python 3.10 or newer from python.org and open PowerShell in this folder.
2. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the app and test dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Create your local environment file and set a secret key:

   ```powershell
   Copy-Item .env.example .env
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

   Copy the generated value into `HABIX_SECRET_KEY` in `.env`. Keep `.env` private.

5. Start the server:

   ```powershell
   python app.py
   ```

6. Open [http://127.0.0.1:5000](http://127.0.0.1:5000) and register your account.

Flask creates `instance/habix.sqlite3` and initializes the tables automatically from `habix/db.py`. Applied schema versions and UTC timestamps are recorded in `schema_migrations`. The initial schema uses `CREATE TABLE IF NOT EXISTS`, so initialization can be rerun safely. Future schema changes should be added as a new sequential entry in `MIGRATIONS`; back up the database before upgrading. Run initialization explicitly with `flask --app app init-db`. `HABIX_DATABASE` can point to another SQLite file. Back up the database file to preserve planner data.

For local HTTP, keep `HABIX_COOKIE_SECURE=0`. Set it to `1` when serving behind HTTPS. The development server is intended for local use, not direct public hosting. Use a production WSGI server and HTTPS before exposing the app to a network.

## What works

- Registration, login, logout, password hashing, CSRF checks, session cookies, and account ownership checks.
- Dashboard totals calculated from saved tasks, study sessions, habits, timetable events, and exams.
- Daily, weekly, and monthly timetable views; event editing and deletion; recurring weekday events; custom day orders assigned to calendar dates; and overlap warnings.
- Task create, edit, delete, status changes, subtasks, search, and date/priority/status filters.
- Habit schedules, check-ins, streaks, recent history, and weekly/monthly completion rates.
- Focus sessions with start, pause, resume, and stop. Active timer state is stored in SQLite so a page refresh can recover it. Completed sessions feed subject and daily study summaries. Break countdowns are local to the open page and are not study records.
- Exam dates, syllabus topics, topic status and priority, revision sessions, and a deterministic rule-based plan using topic priority, days remaining, and the daily study target.
- Calendar entries for timetable events, task due dates, and exams; in-app deadline reminders; progress charts; profile settings; theme and week-start preferences; and CSV exports for tasks, timetable events, and study sessions.

## Deliberate limits in this version

- The planner assistant is explicitly rule-based. No AI provider is connected and no AI API key is used.
- Reminders appear in the HABIX notification center only. This version does not send browser push or email notifications.
- Chart.js, Lucide icons, and the optional Google font are loaded from CDNs. If the device is offline, the app remains usable, but charts/icons/custom fonts may not load.
- User-entered calendar dates and clock times use the computer's local time. Audit timestamps are stored in UTC. Set the computer's time zone correctly before recording sessions.
- SQLite is the current storage backend. The schema and data access are kept in a separate module so a PostgreSQL adapter and formal migrations can be added later.

## Project structure

```text
habix-ai/
├── app.py                    # Local entry point and Flask application
├── config.py                 # Environment-backed settings
├── requirements.txt
├── desktop-requirements.txt   # Additional PyInstaller dependency for packaging
├── package.json               # Electron app and Windows packaging config
├── build-desktop.ps1          # Builds the bundled backend and portable app
├── backend_entry.py           # PyInstaller backend entry point
├── electron/                  # Electron main process and startup window
├── .env.example
├── habix/
│   ├── __init__.py           # App factory, CSRF, request context
│   ├── auth.py               # Registration, login, logout
│   ├── db.py                 # SQLite schema, migrations, and connections
│   ├── services.py           # Timetable, streak, and planner logic
│   ├── views.py              # Private pages and validated actions
│   ├── templates/
│   └── static/
├── instance/                 # Created SQLite database
└── tests/
```

## Checks

Run the tests from this folder after installing requirements:

```powershell
python -m pytest
```

Tests cover account security, private record ownership, timetable overlap/day-order behavior, persisted timer controls, planner record rendering, rule-plan limits, and habit progress calculations.
