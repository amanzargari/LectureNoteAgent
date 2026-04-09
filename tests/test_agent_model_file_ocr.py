from __future__ import annotations

from pathlib import Path

from lecture_note_agent.agent import LectureNoteAgent
from lecture_note_agent.models import SlideUnit


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
