from __future__ import annotations

import os
from pathlib import Path

from flask import Flask
from flask_login import LoginManager

from .database import DEFAULT_MODEL_PRICES, GlobalSettings, ModelPricing, User, UserSettings, db

_ADMIN_USERNAME = "admin"
_ADMIN_PASSWORD = "Admin@LectureAI2024"


def create_app(data_dir: str | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    base_dir = Path(data_dir) if data_dir else Path.home() / ".lecture-note-agent"
    base_dir.mkdir(parents=True, exist_ok=True)

    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "lecture-ai-secret-change-in-prod")
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{base_dir / 'lecture_notes.db'}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_DIR"] = str(base_dir / "uploads")
    app.config["OUTPUT_DIR"] = str(base_dir / "outputs")
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to continue."
    login_manager.login_message_category = "warning"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        return db.session.get(User, int(user_id))

    from .admin import admin_bp
    from .auth import auth_bp
    from .routes import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    with app.app_context():
        db.create_all()
        _migrate_db()
        _seed_admin_and_globals()
        _seed_model_pricing()

    return app


def _seed_admin_and_globals() -> None:
    if not User.query.filter_by(username=_ADMIN_USERNAME).first():
        admin = User(username=_ADMIN_USERNAME, email="admin@lectureai.local", is_admin=True)
        admin.set_password(_ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.flush()

        db.session.add(UserSettings(user_id=admin.id))

        _global_defaults = {
            "api_key": os.getenv("OPENAI_API_KEY", ""),
            "api_base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            "model_fallback": os.getenv("OPENAI_MODEL", ""),
            "model_ocr": os.getenv("OPENAI_MODEL_OCR", ""),
            "model_checklist": os.getenv("OPENAI_MODEL_CHECKLIST", ""),
            "model_draft": os.getenv("OPENAI_MODEL_DRAFT", ""),
            "model_audit": os.getenv("OPENAI_MODEL_AUDIT", ""),
            "model_repair": os.getenv("OPENAI_MODEL_REPAIR", ""),
            "model_image_selection": os.getenv("OPENAI_MODEL_IMAGE_SELECTION", ""),
            "max_repair_loops": os.getenv("MAX_REPAIR_LOOPS", "3"),
            "max_model_calls": os.getenv("MAX_MODEL_CALLS", "15"),
            "max_output_tokens": os.getenv("MAX_OUTPUT_TOKENS", "8000"),
        }
        for key, value in _global_defaults.items():
            db.session.add(GlobalSettings(key=key, value=value or ""))

        db.session.commit()


def _migrate_db() -> None:
    """Idempotent migration: add any missing columns to existing tables."""
    conn = db.engine.raw_connection()
    cur = conn.cursor()

    def _has_col(table: str, col: str) -> bool:
        cur.execute(f"PRAGMA table_info({table})")
        return any(row[1] == col for row in cur.fetchall())

    # ALL columns ever added to projects (safe to run on any schema version)
    project_cols = [
        ("progress_stage",        "VARCHAR(64)"),
        ("progress_pct",          "FLOAT DEFAULT 0.0"),
        ("error_message",         "TEXT"),
        ("notes_markdown",        "TEXT"),
        ("checklist_markdown",    "TEXT"),
        ("audit_json",            "TEXT"),
        ("docx_path",             "VARCHAR(512)"),
        ("pdf_path",              "VARCHAR(512)"),
        ("token_prompt",          "INTEGER DEFAULT 0"),
        ("token_completion",      "INTEGER DEFAULT 0"),
        ("token_total",           "INTEGER DEFAULT 0"),
        ("model_calls",           "INTEGER DEFAULT 0"),
        ("elapsed_seconds",       "FLOAT"),
        ("slides_object_key",     "VARCHAR(512)"),
        ("transcript_object_key", "VARCHAR(512)"),
        ("docx_object_key",       "VARCHAR(512)"),
        ("pdf_object_key",        "VARCHAR(512)"),
        ("cost_usd",              "FLOAT DEFAULT 0.0"),
        ("model_usage_json",      "TEXT"),
    ]
    for col, typedef in project_cols:
        if not _has_col("projects", col):
            cur.execute(f"ALTER TABLE projects ADD COLUMN {col} {typedef}")

    # user_settings columns
    user_settings_cols = [
        ("model_image_selection",          "VARCHAR(128)"),
        ("fast_mode",                      "BOOLEAN DEFAULT 0"),
        ("enable_image_selection_refine",  "BOOLEAN DEFAULT 1"),
        ("ocr_mode",                       "VARCHAR(16) DEFAULT 'auto'"),
        ("slide_weight",                   "FLOAT DEFAULT 0.6"),
    ]
    for col, typedef in user_settings_cols:
        if not _has_col("user_settings", col):
            cur.execute(f"ALTER TABLE user_settings ADD COLUMN {col} {typedef}")

    conn.commit()
    conn.close()


def _seed_model_pricing() -> None:
    for model_name, (inp, out) in DEFAULT_MODEL_PRICES.items():
        if not ModelPricing.query.filter_by(model_name=model_name).first():
            db.session.add(ModelPricing(model_name=model_name, input_per_1m=inp, output_per_1m=out))
    db.session.commit()


def run() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="LectureNoteAgent Web UI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()

    app = create_app(args.data_dir)
    print(f"\n  LectureNoteAgent Web UI")
    print(f"  Running at http://localhost:{args.port}")
    print(f"  Admin login → username: {_ADMIN_USERNAME}  password: {_ADMIN_PASSWORD}\n")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
