from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from .database import GlobalSettings, Project, UserSettings, db

main_bp = Blueprint("main", __name__)

# In-memory live progress: project_id -> {stage, message, pct}
_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_global(key: str, fallback: str = "") -> str:
    gs = GlobalSettings.query.filter_by(key=key).first()
    return (gs.value or "") if gs else fallback


def _build_agent_config(user):
    from ..config import AgentConfig

    s: UserSettings | None = user.settings

    def pick(user_attr: str, global_key: str, env_var: str = "") -> str:
        if s and getattr(s, user_attr, None):
            return getattr(s, user_attr)
        g = _get_global(global_key)
        if g:
            return g
        return os.getenv(env_var, "") if env_var else ""

    fallback = pick("model_fallback", "model_fallback", "OPENAI_MODEL")
    api_key = pick("api_key", "api_key", "OPENAI_API_KEY")
    base_url = pick("api_base_url", "api_base_url", "OPENAI_BASE_URL") or "https://api.openai.com/v1"

    def _int(user_attr: str, global_key: str, default: int) -> int:
        if s and getattr(s, user_attr, None) is not None:
            return int(getattr(s, user_attr))
        g = _get_global(global_key)
        return int(g) if g else default

    return AgentConfig(
        api_key=api_key,
        base_url=base_url,
        model=fallback or os.getenv("OPENAI_MODEL", ""),
        model_ocr=pick("model_ocr", "model_ocr", "OPENAI_MODEL_OCR") or fallback,
        model_checklist=pick("model_checklist", "model_checklist", "OPENAI_MODEL_CHECKLIST") or fallback,
        model_draft=pick("model_draft", "model_draft", "OPENAI_MODEL_DRAFT") or fallback,
        model_audit=pick("model_audit", "model_audit", "OPENAI_MODEL_AUDIT") or fallback,
        model_repair=pick("model_repair", "model_repair", "OPENAI_MODEL_REPAIR") or fallback,
        model_image_selection=pick("model_image_selection", "model_image_selection", "OPENAI_MODEL_IMAGE_SELECTION") or fallback,
        max_repair_loops=_int("max_repair_loops", "max_repair_loops", 3),
        max_model_calls=_int("max_model_calls", "max_model_calls", 15),
        max_output_tokens=_int("max_output_tokens", "max_output_tokens", 8000),
        fast_mode=bool(s.fast_mode if s and s.fast_mode is not None else False),
        enable_image_selection_refine=bool(
            s.enable_image_selection_refine if s and s.enable_image_selection_refine is not None else True
        ),
        pdf_ocr_mode=s.ocr_mode if s and s.ocr_mode else "auto",
    )


def _run_project_bg(app, project_id: int, slides_path: str, transcript_path: str,
                    output_path: str, artifacts_dir: str, config) -> None:
    with app.app_context():
        start = time.time()
        try:
            from ..agent import LectureNoteAgent

            agent = LectureNoteAgent(config=config)

            def on_progress(data: dict) -> None:
                pct = data["current"] / max(1, data["total"]) * 100
                with _progress_lock:
                    _progress[project_id] = {
                        "stage": data["stage"],
                        "message": data["message"],
                        "pct": round(pct, 1),
                    }
                row = db.session.get(Project, project_id)
                if row:
                    row.progress_stage = data["stage"]
                    row.progress_pct = pct
                    db.session.commit()

            row = db.session.get(Project, project_id)
            if row:
                row.status = "running"
                db.session.commit()

            artifacts = agent.run(
                course_name=row.course_name,
                slides_path=slides_path,
                transcript_path=transcript_path,
                output_path=output_path,
                artifacts_dir=artifacts_dir,
                progress_callback=on_progress,
            )

            elapsed = time.time() - start
            row = db.session.get(Project, project_id)
            if row:
                row.status = "completed"
                row.notes_markdown = artifacts.final_markdown
                row.checklist_markdown = artifacts.checklist_markdown
                row.audit_json = artifacts.audit_json
                row.docx_path = output_path
                row.pdf_path = str(Path(output_path).with_suffix(".pdf"))
                row.token_prompt = artifacts.prompt_tokens
                row.token_completion = artifacts.completion_tokens
                row.token_total = artifacts.total_tokens
                row.model_calls = artifacts.model_calls
                row.elapsed_seconds = elapsed
                row.progress_pct = 100.0
                db.session.commit()

        except Exception as exc:
            elapsed = time.time() - start
            row = db.session.get(Project, project_id)
            if row:
                row.status = "failed"
                row.error_message = str(exc)
                row.elapsed_seconds = elapsed
                db.session.commit()
        finally:
            with _progress_lock:
                _progress.pop(project_id, None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    projects = (
        Project.query.filter_by(user_id=current_user.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    return render_template("dashboard.html", projects=projects)


@main_bp.route("/project/new", methods=["GET", "POST"])
@login_required
def new_project():
    if request.method == "POST":
        course_name = request.form.get("course_name", "").strip()
        if not course_name:
            flash("Course name is required.", "danger")
            return redirect(url_for("main.new_project"))

        slides_file = request.files.get("slides")
        if not slides_file or not slides_file.filename:
            flash("Slides file is required.", "danger")
            return redirect(url_for("main.new_project"))

        transcript_file = request.files.get("transcript")

        project = Project(user_id=current_user.id, course_name=course_name, status="pending")
        db.session.add(project)
        db.session.flush()

        upload_dir = Path(current_app.config["UPLOAD_DIR"]) / str(current_user.id) / str(project.id)
        output_dir = Path(current_app.config["OUTPUT_DIR"]) / str(current_user.id) / str(project.id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        slides_filename = secure_filename(slides_file.filename)
        slides_path = str(upload_dir / slides_filename)
        slides_file.save(slides_path)
        project.slides_filename = slides_filename

        if transcript_file and transcript_file.filename:
            transcript_filename = secure_filename(transcript_file.filename)
            transcript_path = str(upload_dir / transcript_filename)
            transcript_file.save(transcript_path)
            project.transcript_filename = transcript_filename
        else:
            transcript_path = str(upload_dir / "_empty.txt")
            Path(transcript_path).write_text("")
            project.transcript_filename = None

        db.session.commit()

        output_path = str(output_dir / "lecture_notes.docx")
        artifacts_dir = str(output_dir / "artifacts")
        config = _build_agent_config(current_user)

        thread = threading.Thread(
            target=_run_project_bg,
            args=(
                current_app._get_current_object(),
                project.id,
                slides_path,
                transcript_path,
                output_path,
                artifacts_dir,
                config,
            ),
            daemon=True,
        )
        thread.start()

        flash("Generation started! This may take a few minutes.", "success")
        return redirect(url_for("main.project", project_id=project.id))

    return render_template("new_project.html")


@main_bp.route("/project/<int:project_id>")
@login_required
def project(project_id: int):
    p = Project.query.filter_by(id=project_id, user_id=current_user.id).first_or_404()
    audit_data = None
    if p.audit_json:
        try:
            audit_data = json.loads(p.audit_json)
        except Exception:
            pass
    return render_template("project.html", project=p, audit=audit_data)


@main_bp.route("/project/<int:project_id>/status")
@login_required
def project_status(project_id: int):
    p = Project.query.filter_by(id=project_id, user_id=current_user.id).first_or_404()
    with _progress_lock:
        live = _progress.get(project_id, {})
    return jsonify(
        {
            "status": p.status,
            "stage": live.get("stage", p.progress_stage or ""),
            "message": live.get("message", ""),
            "pct": live.get("pct", p.progress_pct or 0),
            "error": p.error_message,
        }
    )


@main_bp.route("/project/<int:project_id>/download")
@login_required
def project_download(project_id: int):
    p = Project.query.filter_by(id=project_id, user_id=current_user.id).first_or_404()
    if p.status != "completed" or not p.docx_path or not Path(p.docx_path).exists():
        flash("Download not available.", "danger")
        return redirect(url_for("main.project", project_id=project_id))
    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in p.course_name)
    return send_file(p.docx_path, as_attachment=True, download_name=f"{safe_name}_notes.docx")


@main_bp.route("/project/<int:project_id>/download_pdf")
@login_required
def project_download_pdf(project_id: int):
    p = Project.query.filter_by(id=project_id, user_id=current_user.id).first_or_404()
    if p.status != "completed" or not getattr(p, "pdf_path", None) or not Path(p.pdf_path).exists():
        flash("PDF Download not available.", "danger")
        return redirect(url_for("main.project", project_id=project_id))
    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in p.course_name)
    return send_file(p.pdf_path, as_attachment=True, download_name=f"{safe_name}_notes.pdf")


@main_bp.route("/project/<int:project_id>/delete", methods=["POST"])
@login_required
def project_delete(project_id: int):
    p = Project.query.filter_by(id=project_id, user_id=current_user.id).first_or_404()
    if p.status == "running":
        flash("Cannot delete a running project.", "warning")
        return redirect(url_for("main.project", project_id=project_id))
    db.session.delete(p)
    db.session.commit()
    flash("Project deleted.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    s = current_user.settings
    if not s:
        s = UserSettings(user_id=current_user.id)
        db.session.add(s)
        db.session.commit()

    if request.method == "POST":
        s.api_key = request.form.get("api_key", "").strip() or None
        s.api_base_url = request.form.get("api_base_url", "").strip() or None
        s.model_fallback = request.form.get("model_fallback", "").strip() or None
        s.model_ocr = request.form.get("model_ocr", "").strip() or None
        s.model_checklist = request.form.get("model_checklist", "").strip() or None
        s.model_draft = request.form.get("model_draft", "").strip() or None
        s.model_audit = request.form.get("model_audit", "").strip() or None
        s.model_repair = request.form.get("model_repair", "").strip() or None
        s.model_image_selection = request.form.get("model_image_selection", "").strip() or None
        s.ocr_mode = request.form.get("ocr_mode", "auto")
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
            flash("Max loops/calls/tokens must be integers.", "danger")
            return redirect(url_for("main.settings"))

        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("main.settings"))

    globals_ = {gs.key: gs.value for gs in GlobalSettings.query.all()}
    return render_template("settings.html", settings=s, globals=globals_)
