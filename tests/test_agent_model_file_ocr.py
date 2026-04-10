from __future__ import annotations

import json
from pathlib import Path

from lecture_note_agent.agent import LectureNoteAgent
from lecture_note_agent.models import SlideUnit, TranscriptSegment


def _build_agent() -> LectureNoteAgent:
    # Avoid __init__ (which requires a real API key/client)
    return LectureNoteAgent.__new__(LectureNoteAgent)


def test_parse_page_json_strict_and_wrapped_json() -> None:
    agent = _build_agent()

    strict = '{"pages": [{"page": 1, "text": "Alpha"}, {"page": 2, "text": "Beta"}]}'
    assert agent._parse_page_json(strict) == {1: "Alpha", 2: "Beta"}

    wrapped = "some preface\n" + strict + "\ntrailer"
    assert agent._parse_page_json(wrapped) == {1: "Alpha", 2: "Beta"}


def test_merge_model_ocr_text_whole_mode_non_pdf_passthrough(tmp_path: Path) -> None:
    agent = _build_agent()
    slides = [SlideUnit(slide_number=1, title="A", text="hello", image_refs=[])]
    in_path = tmp_path / "slides.txt"
    in_path.write_text("x", encoding="utf-8")

    out = agent._merge_model_ocr_text(str(in_path), slides, mode="whole")
    assert out[0].text == "hello"
    assert out[0].image_refs == []


def test_merge_model_ocr_text_whole_mode_merges_and_marks(tmp_path: Path, monkeypatch) -> None:
    agent = _build_agent()

    def fake_whole(_pdf_path: str, total_pages: int) -> dict[int, str]:
        assert total_pages == 2
        return {1: "Scanned page one text", 2: "x = y + 1"}

    monkeypatch.setattr(agent, "_ocr_pdf_via_model_whole", fake_whole)
    monkeypatch.setattr(agent, "_ocr_pdf_via_model_per_page", lambda _p, _n: {})

    slides = [
        SlideUnit(slide_number=1, title="", text="", image_refs=[]),
        SlideUnit(slide_number=2, title="Title 2", text="Already extracted text from parser", image_refs=[]),
    ]

    pdf_path = tmp_path / "slides.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub\n")

    out = agent._merge_model_ocr_text(str(pdf_path), slides, mode="whole")

    assert out[0].text == "Scanned page one text"
    assert out[0].title == "Scanned page one text"
    assert "model_file_ocr_pdf" in out[0].image_refs

    assert "[MODEL_FILE_OCR]" in out[1].text
    assert "x = y + 1" in out[1].text
    assert "model_file_ocr_pdf" in out[1].image_refs
    assert any("x = y + 1" in f for f in out[1].formula_candidates)


def test_merge_model_ocr_text_auto_mode_uses_page_fallback(tmp_path: Path, monkeypatch) -> None:
    agent = _build_agent()

    # page 1 & 2 are weak initially
    slides = [
        SlideUnit(slide_number=1, text="", image_refs=[]),
        SlideUnit(slide_number=2, text="", image_refs=[]),
    ]

    monkeypatch.setattr(agent, "_ocr_pdf_via_model_whole", lambda _p, total_pages: {1: ""})

    requested_pages: list[int] = []

    def fake_page(_p: str, page_numbers: list[int]) -> dict[int, str]:
        requested_pages.extend(page_numbers)
        return {2: "Fallback page two"}

    monkeypatch.setattr(agent, "_ocr_pdf_via_model_per_page", fake_page)

    pdf_path = tmp_path / "slides.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%stub\n")

    out = agent._merge_model_ocr_text(str(pdf_path), slides, mode="auto")

    assert requested_pages == [1, 2]
    assert out[0].text == ""
    assert out[1].text == "Fallback page two"


def test_extract_response_text_supports_output_and_output_text() -> None:
    agent = _build_agent()

    class TextNode:
        def __init__(self, text: str):
            self.text = text

    class OutItem:
        def __init__(self, content):
            self.content = content

    class RespA:
        output_text = "Direct text"

    class RespB:
        output_text = ""
        output = [OutItem([TextNode("chunk one"), TextNode("chunk two")])]

    assert agent._extract_response_text(RespA()) == "Direct text"
    assert agent._extract_response_text(RespB()) == "chunk one\nchunk two"


def test_chat_continues_when_length_finish_reason(monkeypatch) -> None:
    agent = _build_agent()

    class Cfg:
        max_continuation_calls = 2
        model = "qwen/qwen3.5-flash-02-23"

    agent.config = Cfg()

    responses = [
        ("## Section\n- Item A\n- Three-P", "length"),
        ("hase reading\n- Item C\n", "stop"),
    ]

    def fake_chat_once(*_args, **_kwargs):
        return responses.pop(0)

    monkeypatch.setattr(agent, "_chat_once", fake_chat_once)

    out = agent._chat(
        system_prompt="system",
        user_prompt="user",
        temperature=0.1,
        model="qwen/qwen3.5-flash-02-23",
        allow_continuation=True,
    )

    assert "Three-Phase" in out
    assert "Item C" in out


def test_run_stops_repair_when_model_limit_reached(tmp_path: Path, monkeypatch) -> None:
    agent = _build_agent()

    class Cfg:
        max_repair_loops = 3
        max_model_calls = 2
        max_continuation_calls = 0
        model = "dummy"
        model_ocr = "dummy"
        model_checklist = "dummy"
        model_draft = "dummy"
        model_audit = "dummy"
        model_repair = "dummy"
        max_output_tokens = 256
        max_input_chars = 10000

    agent.config = Cfg()
    agent._model_calls = 0
    agent._prompt_tokens = 0
    agent._completion_tokens = 0
    agent._total_tokens = 0

    monkeypatch.setattr(
        "lecture_note_agent.agent.parse_slides",
        lambda _p: [SlideUnit(slide_number=1, title="S1", text="content", image_refs=[])],
    )
    monkeypatch.setattr(
        "lecture_note_agent.agent.parse_transcript",
        lambda _p: [TranscriptSegment(segment_id="T1", speaker="Teacher", text="hello")],
    )
    monkeypatch.setattr("lecture_note_agent.agent.build_source_payload", lambda *_a, **_k: "payload")
    monkeypatch.setattr("lecture_note_agent.agent.extract_slide_images", lambda **_k: {})

    def fake_write_docx_from_markdown(*, output_path: str, **_kwargs) -> None:
        Path(output_path).write_bytes(b"docx")

    monkeypatch.setattr("lecture_note_agent.agent.write_docx_from_markdown", fake_write_docx_from_markdown)

    def fake_chat_once(**_kwargs):
        agent._enforce_call_limit()
        agent._model_calls += 1
        return "ok", "stop"

    monkeypatch.setattr(agent, "_chat_once", fake_chat_once)
    monkeypatch.setattr(
        agent,
        "_audit_notes",
        lambda *_a, **_k: {
            "coverage_percent": 80,
            "missing_items": ["C-1"],
            "weak_items": [],
            "issues": [],
            "pass": False,
        },
    )

    output_path = tmp_path / "notes.docx"
    artifacts = agent.run(
        course_name="Test Course",
        slides_path=str(tmp_path / "slides.txt"),
        transcript_path=str(tmp_path / "transcript.txt"),
        output_path=str(output_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )

    assert output_path.exists()
    assert artifacts.model_calls == 2

    audit = json.loads(artifacts.audit_json)
    issues = [str(x).lower() for x in audit.get("issues", [])]
    assert any("model call limit reached" in msg for msg in issues)


def test_run_fast_mode_skips_audit_and_repair(tmp_path: Path, monkeypatch) -> None:
    agent = _build_agent()

    class Cfg:
        fast_mode = True
        pdf_ocr_mode = "auto"
        max_repair_loops = 3
        max_model_calls = 10
        max_continuation_calls = 0
        model = "dummy"
        model_ocr = "dummy"
        model_checklist = "dummy"
        model_draft = "dummy"
        model_audit = "dummy"
        model_repair = "dummy"
        max_output_tokens = 256
        max_input_chars = 10000

    agent.config = Cfg()
    agent._model_calls = 0
    agent._prompt_tokens = 0
    agent._completion_tokens = 0
    agent._total_tokens = 0

    monkeypatch.setattr(
        "lecture_note_agent.agent.parse_slides",
        lambda _p: [SlideUnit(slide_number=1, title="S1", text="content", image_refs=[])],
    )
    monkeypatch.setattr(
        "lecture_note_agent.agent.parse_transcript",
        lambda _p: [TranscriptSegment(segment_id="T1", speaker="Teacher", text="hello")],
    )
    monkeypatch.setattr("lecture_note_agent.agent.build_source_payload", lambda *_a, **_k: "payload")
    monkeypatch.setattr("lecture_note_agent.agent.extract_slide_images", lambda **_k: {})

    def fake_write_docx_from_markdown(*, output_path: str, **_kwargs) -> None:
        Path(output_path).write_bytes(b"docx")

    monkeypatch.setattr("lecture_note_agent.agent.write_docx_from_markdown", fake_write_docx_from_markdown)

    def fake_chat_once(**_kwargs):
        agent._enforce_call_limit()
        agent._model_calls += 1
        return "ok", "stop"

    monkeypatch.setattr(agent, "_chat_once", fake_chat_once)

    def _should_not_be_called(*_args, **_kwargs):
        raise AssertionError("_audit_notes should not run in fast mode")

    monkeypatch.setattr(agent, "_audit_notes", _should_not_be_called)

    output_path = tmp_path / "notes.docx"
    artifacts = agent.run(
        course_name="Fast Course",
        slides_path=str(tmp_path / "slides.txt"),
        transcript_path=str(tmp_path / "transcript.txt"),
        output_path=str(output_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )

    assert output_path.exists()
    assert artifacts.model_calls == 2

    audit = json.loads(artifacts.audit_json)
    assert any("fast mode enabled" in str(msg).lower() for msg in audit.get("issues", []))


def test_run_stops_repair_when_output_repeats(tmp_path: Path, monkeypatch) -> None:
    agent = _build_agent()

    class Cfg:
        fast_mode = False
        pdf_ocr_mode = "auto"
        max_repair_loops = 3
        max_model_calls = 10
        max_continuation_calls = 0
        max_repair_no_progress = 1
        request_timeout_seconds = 60
        model = "dummy"
        model_ocr = "dummy"
        model_checklist = "dummy"
        model_draft = "dummy"
        model_audit = "dummy"
        model_repair = "dummy"
        max_output_tokens = 256
        max_input_chars = 10000

    agent.config = Cfg()
    agent._model_calls = 0
    agent._prompt_tokens = 0
    agent._completion_tokens = 0
    agent._total_tokens = 0

    monkeypatch.setattr(
        "lecture_note_agent.agent.parse_slides",
        lambda _p: [SlideUnit(slide_number=1, title="S1", text="content", image_refs=[])],
    )
    monkeypatch.setattr(
        "lecture_note_agent.agent.parse_transcript",
        lambda _p: [TranscriptSegment(segment_id="T1", speaker="Teacher", text="hello")],
    )
    monkeypatch.setattr("lecture_note_agent.agent.build_source_payload", lambda *_a, **_k: "payload")
    monkeypatch.setattr("lecture_note_agent.agent.extract_slide_images", lambda **_k: {})

    def fake_write_docx_from_markdown(*, output_path: str, **_kwargs) -> None:
        Path(output_path).write_bytes(b"docx")

    monkeypatch.setattr("lecture_note_agent.agent.write_docx_from_markdown", fake_write_docx_from_markdown)

    def fake_chat(system_prompt: str, _user_prompt: str, **_kwargs) -> str:
        if "coverage checklist" in system_prompt.lower() or "academic lecture auditor" in system_prompt.lower():
            return "checklist"
        if "lecture-note agent" in system_prompt.lower():
            return "BASE NOTES"
        return "BASE NOTES"

    monkeypatch.setattr(agent, "_chat", fake_chat)

    audit_calls = {"count": 0}

    def fake_audit(*_args, **_kwargs):
        audit_calls["count"] += 1
        return {
            "coverage_percent": 70,
            "missing_items": ["C-1"],
            "weak_items": [],
            "issues": [],
            "pass": False,
        }

    monkeypatch.setattr(agent, "_audit_notes", fake_audit)

    output_path = tmp_path / "notes.docx"
    artifacts = agent.run(
        course_name="Repeat Course",
        slides_path=str(tmp_path / "slides.txt"),
        transcript_path=str(tmp_path / "transcript.txt"),
        output_path=str(output_path),
        artifacts_dir=str(tmp_path / "artifacts"),
    )

    assert output_path.exists()
    assert audit_calls["count"] == 1

    audit = json.loads(artifacts.audit_json)
    issues = [str(msg).lower() for msg in audit.get("issues", [])]
    assert any("repair appears stuck" in msg for msg in issues)


def test_sanitize_final_markdown_removes_internal_refs_and_placeholder_section() -> None:
    agent = _build_agent()
    dirty = (
        "## Topic [S52] [C-S-52-1]\n"
        "Text line [T10].\n\n"
        "## Image Placeholders to Add Later\n"
        "- img_ref_1\n"
        "- img_ref_2\n\n"
        "## Next\n"
        "Content"
    )

    cleaned = agent._sanitize_final_markdown(dirty)
    assert "[S52]" not in cleaned
    assert "[T10]" not in cleaned
    assert "[C-S-52-1]" not in cleaned
    assert "Image Placeholders to Add Later" not in cleaned
    assert "## Next" in cleaned
