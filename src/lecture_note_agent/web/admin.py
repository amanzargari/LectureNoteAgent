from __future__ import annotations

from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .database import GlobalSettings, Project, User, UserSettings, db

admin_bp = Blueprint("admin", __name__)


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)

    return decorated


@admin_bp.route("/")
@admin_required
def index():
    total_users = User.query.count()
    total_projects = Project.query.count()
    completed = Project.query.filter_by(status="completed").count()
    running = Project.query.filter_by(status="running").count()
    failed = Project.query.filter_by(status="failed").count()
    recent = Project.query.order_by(Project.created_at.desc()).limit(10).all()
    return render_template(
        "admin/index.html",
        total_users=total_users,
        total_projects=total_projects,
        completed=completed,
        running=running,
        failed=failed,
        recent=recent,
    )


@admin_bp.route("/users")
@admin_required
def users():
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=all_users)


@admin_bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def new_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip() or None
        password = request.form.get("password", "")
        is_admin = "is_admin" in request.form

        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("admin.new_user"))

        if User.query.filter_by(username=username).first():
            flash(f'Username "{username}" already exists.', "danger")
            return redirect(url_for("admin.new_user"))

        user = User(username=username, email=email, is_admin=is_admin)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        db.session.add(UserSettings(user_id=user.id))
        db.session.commit()

        flash(f'User "{username}" created.', "success")
        return redirect(url_for("admin.users"))

    return render_template("admin/edit_user.html", user=None, globals={})


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip() or None
        is_admin_flag = "is_admin" in request.form
        is_active = "is_active" in request.form
        password = request.form.get("password", "").strip()

        if not username:
            flash("Username is required.", "danger")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        conflict = User.query.filter_by(username=username).first()
        if conflict and conflict.id != user_id:
            flash(f'Username "{username}" is already taken.', "danger")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        user.username = username
        user.email = email
        user.is_admin = is_admin_flag
        user.is_active = is_active
        if password:
            user.set_password(password)

        s = user.settings
        if not s:
            s = UserSettings(user_id=user.id)
            db.session.add(s)

        s.model_fallback = request.form.get("model_fallback", "").strip() or None
        s.model_ocr = request.form.get("model_ocr", "").strip() or None
        s.model_checklist = request.form.get("model_checklist", "").strip() or None
        s.model_draft = request.form.get("model_draft", "").strip() or None
        s.model_audit = request.form.get("model_audit", "").strip() or None
        s.model_repair = request.form.get("model_repair", "").strip() or None
        s.fast_mode = "fast_mode" in request.form
        s.enable_image_selection_refine = "enable_image_selection_refine" in request.form

        try:
            raw_loops = request.form.get("max_repair_loops", "").strip()
            raw_calls = request.form.get("max_model_calls", "").strip()
            raw_tokens = request.form.get("max_output_tokens", "").strip()
            s.max_repair_loops = int(raw_loops) if raw_loops else None
            s.max_model_calls = int(raw_calls) if raw_calls else None
            s.max_output_tokens = int(raw_tokens) if raw_tokens else None
        except ValueError:
            flash("Limits must be integers.", "danger")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        db.session.commit()
        flash(f'User "{user.username}" updated.', "success")
        return redirect(url_for("admin.users"))

    globals_ = {gs.key: gs.value for gs in GlobalSettings.query.all()}
    return render_template("admin/edit_user.html", user=user, globals=globals_)


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int):
    if user_id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("admin.users"))
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users"))
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{user.username}" deleted.', "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def global_settings():
    keys = [
        "api_key", "api_base_url",
        "model_fallback", "model_ocr", "model_checklist",
        "model_draft", "model_audit", "model_repair", "model_image_selection",
        "max_repair_loops", "max_model_calls", "max_output_tokens",
    ]
    if request.method == "POST":
        for key in keys:
            val = request.form.get(key, "").strip()
            gs = GlobalSettings.query.filter_by(key=key).first()
            if gs:
                gs.value = val
            else:
                db.session.add(GlobalSettings(key=key, value=val))
        db.session.commit()
        flash("Global settings saved.", "success")
        return redirect(url_for("admin.global_settings"))

    settings = {gs.key: gs.value for gs in GlobalSettings.query.all()}
    return render_template("admin/global_settings.html", settings=settings)
