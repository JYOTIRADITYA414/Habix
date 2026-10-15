"""Business logic for timetable occurrences, progress, and rule-based plans."""
from calendar import monthrange
from datetime import date, datetime, timedelta


def occurrences(db, user_id, start, end):
    rows = db.execute(
        "SELECT * FROM events WHERE user_id = ? AND event_date <= ? AND (repeat_until IS NULL OR repeat_until >= ?)",
        (user_id, end.isoformat(), start.isoformat()),
    ).fetchall()
    result = []
    for row in rows:
        weekdays = {int(day) for day in (row["weekdays"] or "").split(",") if day.strip().isdigit()}
        cursor = max(start, date.fromisoformat(row["event_date"]))
        if row["repeat_until"]:
            last = min(end, date.fromisoformat(row["repeat_until"]))
        else:
            last = end if weekdays else min(end, date.fromisoformat(row["event_date"]))
        if row["day_order"] and not weekdays:
            # Day-order classes recur only on dates the student assigns that order.
            order_last = min(end, date.fromisoformat(row["repeat_until"])) if row["repeat_until"] else end
            assigned = db.execute(
                "SELECT assignment_date FROM day_order_assignments WHERE user_id=? AND day_order=? AND assignment_date BETWEEN ? AND ?",
                (user_id, row["day_order"], cursor.isoformat(), order_last.isoformat()),
            ).fetchall()
            dates = [date.fromisoformat(item[0]) for item in assigned]
        else:
            dates = []
            while cursor <= last:
                if (not weekdays and cursor.isoformat() == row["event_date"]) or (weekdays and cursor.weekday() in weekdays):
                    dates.append(cursor)
                cursor += timedelta(days=1)
        for event_day in dates:
            if (not row["day_order"] or weekdays or event_day >= date.fromisoformat(row["event_date"])):
                item = dict(row)
                item["occurrence_date"] = event_day.isoformat()
                result.append(item)
    return sorted(result, key=lambda item: (item["occurrence_date"], item["start_time"]))


def detect_conflicts(db, user_id, event_date, start_time, end_time, exclude_id=None):
    items = occurrences(db, user_id, date.fromisoformat(event_date), date.fromisoformat(event_date))
    return [item for item in items if item["id"] != exclude_id and start_time < item["end_time"] and end_time > item["start_time"]]


def habit_stats(db, habit, today=None):
    today = today or date.today()
    checks = {date.fromisoformat(row[0]) for row in db.execute("SELECT check_date FROM habit_checks WHERE habit_id = ?", (habit["id"],)) if date.fromisoformat(row[0]) <= today}
    scheduled = {int(n) for n in habit["frequency"].split(",") if n.strip().isdigit()}
    cursor = today
    if today.weekday() in scheduled and today not in checks:
        cursor -= timedelta(days=1)
    streak = 0
    earliest = min(checks) if checks else today
    while cursor >= earliest:
        if cursor.weekday() in scheduled:
            if cursor not in checks:
                break
            streak += 1
        cursor -= timedelta(days=1)
    longest = 0
    run = 0
    if checks:
        cursor = min(checks)
        while cursor <= today:
            if cursor.weekday() in scheduled:
                if cursor in checks:
                    run += 1
                    longest = max(longest, run)
                else:
                    run = 0
            cursor += timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())
    week_dates = [week_start + timedelta(days=n) for n in range((today - week_start).days + 1) if (week_start + timedelta(days=n)).weekday() in scheduled]
    week_done = sum(day in checks for day in week_dates)
    month_start = today.replace(day=1)
    month_dates = [month_start + timedelta(days=n) for n in range((today - month_start).days + 1) if (month_start + timedelta(days=n)).weekday() in scheduled]
    month_done = sum(day in checks for day in month_dates)
    return {"streak": streak, "longest": longest, "week_done": week_done, "week_total": len(week_dates),
            "week_rate": round(100 * week_done / len(week_dates)) if week_dates else 0,
            "month_done": month_done, "month_total": len(month_dates),
            "month_rate": round(100 * month_done / len(month_dates)) if month_dates else 0}


def weekly_plan(db, user_id, exam_id, daily_minutes):
    exam = db.execute("SELECT * FROM exams WHERE id = ? AND user_id = ?", (exam_id, user_id)).fetchone()
    if not exam:
        return None
    today = date.today()
    due = date.fromisoformat(exam["exam_date"])
    days_left = max(1, (due - today).days)
    topics = db.execute("SELECT * FROM topics WHERE exam_id = ? AND status != 'Completed' ORDER BY CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END, id", (exam_id,)).fetchall()
    if not topics:
        return {"exam": exam, "days_left": (due - today).days, "plan": [], "remaining": 0, "unscheduled": 0}
    available_days = min(days_left, 30)
    daily_minutes = max(15, daily_minutes)
    slots_per_day = max(1, daily_minutes // 20)
    planned_topics = topics[:available_days * slots_per_day]
    plan = []
    for day_index in range(available_days):
        group = planned_topics[day_index * slots_per_day:(day_index + 1) * slots_per_day]
        if not group:
            break
        base_minutes, extra_minutes = divmod(daily_minutes, len(group))
        for topic_index, topic in enumerate(group):
            plan.append({"date": (today + timedelta(days=day_index)).isoformat(), "topic": topic["name"],
                         "priority": topic["priority"], "minutes": base_minutes + (1 if topic_index < extra_minutes else 0)})
    return {"exam": exam, "days_left": (due - today).days, "plan": plan, "remaining": len(topics),
            "unscheduled": len(topics) - len(planned_topics)}


def month_bounds(year, month):
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    return first, last
