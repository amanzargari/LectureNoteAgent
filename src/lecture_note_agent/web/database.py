from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    projects = db.relationship("Project", backref="user", lazy=True, cascade="all, delete-orphan")
    settings = db.relationship("UserSettings", backref="user", uselist=False, cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class UserSettings(db.Model):
    __tablename__ = "user_settings"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    api_key = db.Column(db.String(512))
    api_base_url = db.Column(db.String(256))
    model_fallback = db.Column(db.String(128))
    model_ocr = db.Column(db.String(128))
    model_checklist = db.Column(db.String(128))
    model_draft = db.Column(db.String(128))
    model_audit = db.Column(db.String(128))
    model_repair = db.Column(db.String(128))
    model_image_selection = db.Column(db.String(128))
    max_repair_loops = db.Column(db.Integer)
    max_model_calls = db.Column(db.Integer)
    max_output_tokens = db.Column(db.Integer)
    fast_mode = db.Column(db.Boolean, default=False)
    enable_image_selection_refine = db.Column(db.Boolean, default=True)
    ocr_mode = db.Column(db.String(16), default="auto")


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    course_name = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(32), default="pending")
    progress_stage = db.Column(db.String(64))
    progress_pct = db.Column(db.Float, default=0.0)
    slides_filename = db.Column(db.String(256))
    transcript_filename = db.Column(db.String(256))
    error_message = db.Column(db.Text)
    notes_markdown = db.Column(db.Text)
    checklist_markdown = db.Column(db.Text)
    audit_json = db.Column(db.Text)
    docx_path = db.Column(db.String(512))
    pdf_path = db.Column(db.String(512))
    token_prompt = db.Column(db.Integer, default=0)
    token_completion = db.Column(db.Integer, default=0)
    token_total = db.Column(db.Integer, default=0)
    model_calls = db.Column(db.Integer, default=0)
    elapsed_seconds = db.Column(db.Float)


class GlobalSettings(db.Model):
    __tablename__ = "global_settings"
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(128), unique=True, nullable=False)
    value = db.Column(db.Text, default="")
