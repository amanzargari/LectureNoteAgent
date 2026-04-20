from __future__ import annotations

import json
import os
from functools import wraps

import urllib.request
import urllib.error

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from .database import GlobalSettings, ModelPricing, Project, User, UserSettings, db

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

    total_cost = db.session.query(func.sum(Project.cost_usd)).scalar() or 0.0
    total_tokens = db.session.query(func.sum(Project.token_total)).scalar() or 0

    # Per-user cost summary: (user_id, username, cost, tokens, count)
    user_costs_raw = (
        db.session.query(User.id, User.username, func.sum(Project.cost_usd), func.sum(Project.token_total), func.count(Project.id))
        .join(Project, Project.user_id == User.id)
        .group_by(User.id)
        .order_by(func.sum(Project.cost_usd).desc())
        .all()
    )

    return render_template(
        "admin/index.html",
        total_users=total_users,
        total_projects=total_projects,
        completed=completed,
        running=running,
        failed=failed,
        recent=recent,
        total_cost=total_cost,
        total_tokens=total_tokens,
        user_costs=user_costs_raw,
    )


@admin_bp.route("/users/<int:user_id>/projects")
@admin_required
def user_projects(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users"))
    projects = Project.query.filter_by(user_id=user_id).order_by(Project.created_at.desc()).all()
    return render_template("admin/user_projects.html", target_user=user, projects=projects)


@admin_bp.route("/openrouter-credit")
@admin_required
def openrouter_credit():
    """Fetch OpenRouter account credit/balance and return as JSON."""
    api_key = None
    gs = GlobalSettings.query.filter_by(key="api_key").first()
    if gs and gs.value:
        api_key = gs.value
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY", "")

    if not api_key:
        return jsonify({"error": "No API key configured"}), 400

    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return jsonify(data)
    except urllib.error.HTTPError as e:
        return jsonify({"error": f"HTTP {e.code}: {e.reason}"}), e.code
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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
            raw_weight = request.form.get("slide_weight", "0.6").strip()
            s.max_repair_loops = int(raw_loops) if raw_loops else None
            s.max_model_calls = int(raw_calls) if raw_calls else None
            s.max_output_tokens = int(raw_tokens) if raw_tokens else None
            s.slide_weight = max(0.0, min(1.0, float(raw_weight))) if raw_weight else 0.6
        except ValueError:
            flash("Limits must be integers; slide weight must be 0–1.", "danger")
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


@admin_bp.route("/pricing/sync", methods=["POST"])
@admin_required
def pricing_sync():
    """Fetch fresh pricing from OpenRouter API and upsert into ModelPricing table."""
    from .app import fetch_openrouter_pricing
    gs = GlobalSettings.query.filter_by(key="api_key").first()
    api_key = (gs.value if gs else None) or os.getenv("OPENAI_API_KEY", "") or None
    rows = fetch_openrouter_pricing(api_key)
    if not rows:
        flash("Could not fetch pricing from OpenRouter API (check API key).", "danger")
        return redirect(url_for("admin.pricing"))
    for model_name, inp, out in rows:
        existing = ModelPricing.query.filter_by(model_name=model_name).first()
        if existing:
            existing.input_per_1m = inp
            existing.output_per_1m = out
        else:
            db.session.add(ModelPricing(model_name=model_name, input_per_1m=inp, output_per_1m=out))
    db.session.commit()
    flash(f"Synced pricing for {len(rows)} models from OpenRouter.", "success")
    return redirect(url_for("admin.pricing"))


def _build_provider_config_json(form) -> str:
    """Serialize provider routing fields from a form into a JSON string (or empty string)."""
    order_raw = form.get("provider_order", "").strip()
    order = [p.strip() for p in order_raw.split(",") if p.strip()] if order_raw else []
    only_raw = form.get("provider_only", "").strip()
    only = [p.strip() for p in only_raw.split(",") if p.strip()] if only_raw else []
    ignore_raw = form.get("provider_ignore", "").strip()
    ignore = [p.strip() for p in ignore_raw.split(",") if p.strip()] if ignore_raw else []
    quant_raw = form.get("provider_quantizations", "").strip()
    quantizations = [q.strip() for q in quant_raw.split(",") if q.strip()] if quant_raw else []
    sort_val = form.get("provider_sort", "").strip()
    data_collection = form.get("provider_data_collection", "").strip()
    allow_fallbacks = form.get("provider_allow_fallbacks") == "1"

    routing: dict = {}
    if order:
        routing["order"] = order
        routing["allow_fallbacks"] = allow_fallbacks
    if only:
        routing["only"] = only
    if ignore:
        routing["ignore"] = ignore
    if quantizations:
        routing["quantizations"] = quantizations
    if sort_val:
        routing["sort"] = sort_val
    if data_collection:
        routing["data_collection"] = data_collection

    return json.dumps(routing) if routing else ""


@admin_bp.route("/pricing", methods=["GET", "POST"])
@admin_required
def pricing():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            pid = request.form.get("id")
            row = db.session.get(ModelPricing, int(pid))
            if row:
                db.session.delete(row)
                db.session.commit()
                flash(f'Pricing for "{row.model_name}" deleted.', "success")
        elif action in ("add", "edit"):
            model_name = request.form.get("model_name", "").strip()
            try:
                inp = float(request.form.get("input_per_1m", "0"))
                out = float(request.form.get("output_per_1m", "0"))
            except ValueError:
                flash("Prices must be numbers.", "danger")
                return redirect(url_for("admin.pricing"))
            provider_config = _build_provider_config_json(request.form)
            row = ModelPricing.query.filter_by(model_name=model_name).first()
            if row:
                row.input_per_1m = inp
                row.output_per_1m = out
                row.provider_config = provider_config
            else:
                db.session.add(ModelPricing(
                    model_name=model_name,
                    input_per_1m=inp,
                    output_per_1m=out,
                    provider_config=provider_config,
                ))
            db.session.commit()
            flash(f'Pricing for "{model_name}" saved.', "success")
        elif action == "set_provider":
            pid = request.form.get("id")
            row = db.session.get(ModelPricing, int(pid))
            if row:
                row.provider_config = _build_provider_config_json(request.form)
                db.session.commit()
                flash(f'Provider routing for "{row.model_name}" saved.', "success")
        return redirect(url_for("admin.pricing"))

    prices = ModelPricing.query.order_by(ModelPricing.model_name).all()
    # Parse provider_config JSON for template rendering
    prices_with_routing = []
    for p in prices:
        try:
            routing = json.loads(p.provider_config) if p.provider_config else {}
        except Exception:
            routing = {}
        prices_with_routing.append((p, routing))
    return render_template("admin/pricing.html", prices=prices_with_routing)


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
