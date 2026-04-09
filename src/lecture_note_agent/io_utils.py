from __future__ import annotations

import re
from pathlib import Path
from typing import List

from pypdf import PdfReader
from pptx import Presentation

from .models import SlideUnit, TranscriptSegment


MATH_PATTERNS = [
    r"\b\d+\s*[+\-*/=]\s*\d+\b",
    r"\b(?:sin|cos|tan|log|ln|exp|lim|sum|prod|int)\s*\(",
    r"\b[A-Za-z]\s*=\s*[^\n]{1,40}",
    r"\$[^$]+\$",
]


def _extract_formula_candidates(text: str) -> List[str]:
    matches: list[str] = []
    for pattern in MATH_PATTERNS:
        for m in re.finditer(pattern, text):
            token = m.group(0).strip()
            if token and token not in matches:
                matches.append(token)
    return matches[:30]


def extract_formula_candidates(text: str) -> List[str]:
    return _extract_formula_candidates(text)


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _has_meaningful_text(text: str, min_alnum: int = 30) -> bool:
    alnum_count = sum(ch.isalnum() for ch in text)
    return alnum_count >= min_alnum


def has_meaningful_text(text: str, min_alnum: int = 30) -> bool:
    return _has_meaningful_text(text, min_alnum=min_alnum)


def parse_slides(
    slides_path: str,
) -> List[SlideUnit]:
    path = Path(slides_path)
    ext = path.suffix.lower()

    if ext in {".md", ".txt"}:
        text = _read_text_file(path)
        chunks = [c.strip() for c in re.split(r"\n---+\n|\n#{1,2}\s+Slide", text) if c.strip()]
        slides: list[SlideUnit] = []
        for idx, chunk in enumerate(chunks, 1):
            lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            title = lines[0][:140] if lines else f"Slide {idx}"
            body = "\n".join(lines[1:]) if len(lines) > 1 else chunk
            slides.append(
                SlideUnit(
                    slide_number=idx,
                    title=title,
                    text=body,
                    image_refs=[],
                    formula_candidates=_extract_formula_candidates(chunk),
                )
            )
        return slides

    if ext == ".pdf":
        reader = PdfReader(str(path))
        slides = []
        for i, page in enumerate(reader.pages, 1):
            page_text = (page.extract_text() or "").strip()
            full_text = page_text
            image_refs: list[str] = []
            images = getattr(page, "images", []) or []
            for j, img in enumerate(images, 1):
                ref = getattr(img, "name", "") or f"pdf_page_{i}_image_{j}"
                image_refs.append(ref)
            title = full_text.splitlines()[0][:140] if full_text else f"Slide {i}"
            slides.append(
                SlideUnit(
                    slide_number=i,
                    title=title,
                    text=full_text,
                    image_refs=image_refs,
                    formula_candidates=_extract_formula_candidates(full_text),
                )
            )
        return slides

    if ext == ".pptx":
        prs = Presentation(str(path))
        slides: list[SlideUnit] = []
        for i, slide in enumerate(prs.slides, 1):
            text_parts: list[str] = []
            image_refs: list[str] = []
            title = ""
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.text:
                    shape_text = shape.text.strip()
                    if shape_text:
                        text_parts.append(shape_text)
                        if not title:
                            title = shape_text.splitlines()[0][:140]
                if getattr(shape, "shape_type", None) == 13:  # Picture
                    shape_name = getattr(shape, "name", "") or f"pptx_slide_{i}_image_{len(image_refs)+1}"
                    image_refs.append(shape_name)
            text = "\n".join(text_parts).strip()
            slides.append(
                SlideUnit(
                    slide_number=i,
                    title=title or f"Slide {i}",
                    text=text,
                    image_refs=image_refs,
                    formula_candidates=_extract_formula_candidates(text),
                )
            )
        return slides

    raise ValueError(f"Unsupported slides format: {ext}. Use .pdf, .pptx, .md, or .txt")


def parse_transcript(transcript_path: str) -> List[TranscriptSegment]:
    path = Path(transcript_path)
    text = _read_text_file(path)

    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    segments: list[TranscriptSegment] = []

    ts_pattern = re.compile(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*(.*)$")
    speaker_pattern = re.compile(r"^([A-Za-z][A-Za-z0-9_\- ]{1,30}):\s*(.*)$")

    for i, line in enumerate(lines, 1):
        timestamp = ""
        speaker = "Teacher"
        content = line

        ts_match = ts_pattern.match(content)
        if ts_match:
            timestamp = ts_match.group(1)
            content = ts_match.group(2).strip()

        sp_match = speaker_pattern.match(content)
        if sp_match:
            speaker = sp_match.group(1).strip()
            content = sp_match.group(2).strip()

        segments.append(
            TranscriptSegment(
                segment_id=f"T{i}",
                timestamp=timestamp,
                speaker=speaker,
                text=content,
            )
        )

    return segments


def build_source_payload(course_name: str, slides: List[SlideUnit], transcript: List[TranscriptSegment]) -> str:
    slide_blocks: list[str] = []
    for s in slides:
        img_line = ", ".join(s.image_refs) if s.image_refs else "None"
        formula_line = ", ".join(s.formula_candidates) if s.formula_candidates else "None"
        slide_blocks.append(
            "\n".join(
                [
                    f"[S{s.slide_number}] Title: {s.title}",
                    f"Text: {s.text[:3000]}",
                    f"ImageRefs: {img_line}",
                    f"FormulaCandidates: {formula_line}",
                ]
            )
        )

    transcript_blocks = [
        f"[{t.segment_id}] ({t.timestamp or 'NA'}) {t.speaker}: {t.text}"
        for t in transcript
    ]

    payload = (
        f"Course: {course_name}\n\n"
        f"## Slides ({len(slides)})\n" + "\n\n".join(slide_blocks) + "\n\n"
        f"## Transcript Segments ({len(transcript)})\n" + "\n".join(transcript_blocks)
    )
    return payload
