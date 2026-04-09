from __future__ import annotations

# pyright: reportMissingImports=false

from pathlib import Path

import click
from click.testing import CliRunner

import lecture_note_agent.cli as cli_module


class DummyArtifacts:
    audit_json = '{"pass": true}'


def test_cli_passes_model_file_ocr_options(monkeypatch, tmp_path: Path) -> None:
    captured: dict = {}

    class DummyAgent:
        def run(self, **kwargs):
            captured.update(kwargs)
            return DummyArtifacts()

    monkeypatch.setattr(cli_module, "LectureNoteAgent", DummyAgent)

    slides = tmp_path / "slides.pdf"
    transcript = tmp_path / "transcript.txt"
    output = tmp_path / "notes.md"

    slides.write_bytes(b"%PDF-1.4\n%stub\n")
    transcript.write_text("Teacher: hello", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli_module.main,
        [
            "--course-name",
            "Test Course",
            "--slides",
            str(slides),
            "--transcript",
            str(transcript),
            "--output",
            str(output),
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
            "--model-file-ocr",
            "--model-file-ocr-mode",
            "page",
            "--pdf-ocr",
            "--ocr-lang",
            "eng",
            "--ocr-dpi",
            "240",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["course_name"] == "Test Course"
    assert captured["enable_model_file_ocr"] is True
    assert captured["model_file_ocr_mode"] == "page"
    assert captured["enable_pdf_ocr"] is True
    assert captured["ocr_lang"] == "eng"
    assert captured["ocr_dpi"] == 240
