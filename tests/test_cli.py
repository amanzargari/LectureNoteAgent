from __future__ import annotations

# pyright: reportMissingImports=false

from pathlib import Path

import click
from click.testing import CliRunner

import lecture_note_agent.cli as cli_module


class DummyArtifacts:
    audit_json = '{"pass": true}'


def test_cli_passes_basic_arguments(monkeypatch, tmp_path: Path) -> None:
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["course_name"] == "Test Course"
    assert captured["slides_path"] == str(slides)
    assert captured["transcript_path"] == str(transcript)
    assert captured["output_path"] == str(output)
