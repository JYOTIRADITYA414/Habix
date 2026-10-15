"""Session-based registration and login."""
from datetime import datetime, timezone
import re

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)


@bp.route("/register", methods=("GET", "POST"))
def register():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        error = None
        if not name or len(name) > 80:
            error = "Enter your name (up to 80 characters)."
        elif not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
            error = "Enter a valid email address."
        elif len(password) < 8 or len(password) > 128:
            error = "Use a password between 8 and 128 characters."
        elif get_db().execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            error = "An account with that email already exists."
        if error:
            flash(error, "error")
        else:
            db = get_db()
            cursor = db.execute("INSERT INTO users(name, email, password_hash, created_at) VALUES (?, ?, ?, ?) RETURNING id",
                                (name, email, generate_password_hash(password), datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")))
            user_id = cursor.fetchone()["id"]
            db.execute("INSERT INTO preferences(user_id) VALUES (?)", (user_id,))
            db.commit()
            session.clear()
            session["user_id"] = user_id
            session.permanent = True
            flash("Your HABIX account is ready.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth.html", mode="register")


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Email or password is incorrect.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            flash("Welcome back.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth.html", mode="login")


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))
