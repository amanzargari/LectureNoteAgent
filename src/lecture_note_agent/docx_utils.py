from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.shared import Inches

from .io_utils import SlideImageAsset


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")


def _clean_inline_markdown(text: str) -> str:
    out = text
    out = re.sub(r"`([^`]+)`", r"\1", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"\1", out)
    out = re.sub(r"__([^_]+)__", r"\1", out)
    out = re.sub(r"\*([^*]+)\*", r"\1", out)
    out = re.sub(r"_([^_]+)_", r"\1", out)
    out = out.replace("\\[", "[").replace("\\]", "]")
    return out.strip()


def _add_markdown_body(document: Document, markdown_text: str) -> None:
    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            document.add_paragraph("")
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = min(len(heading_match.group(1)), 6)
            text = _clean_inline_markdown(heading_match.group(2))
            document.add_heading(text, level=level)
            continue

        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            document.add_paragraph(_clean_inline_markdown(bullet_match.group(1)), style="List Bullet")
            continue

        numbered_match = _NUMBERED_RE.match(line)
        if numbered_match:
            document.add_paragraph(_clean_inline_markdown(numbered_match.group(1)), style="List Number")
            continue

        if line.lstrip().startswith("|"):
            document.add_paragraph(_clean_inline_markdown(line))
            continue

        document.add_paragraph(_clean_inline_markdown(line))


def _add_images_section(document: Document, slide_images: dict[int, list[SlideImageAsset]]) -> None:
    if not slide_images:
        return

    document.add_page_break()
    document.add_heading("Attached Slide Images", level=1)

    for slide_number in sorted(slide_images.keys()):
        assets = slide_images.get(slide_number, [])
        if not assets:
            continue

        document.add_heading(f"Slide {slide_number}", level=2)
        for asset in assets:
            img_path = Path(asset.image_path)
            if not img_path.exists():
                continue

            document.add_paragraph(f"ImageRef: {asset.image_ref}")
            pic_paragraph = document.add_paragraph()
            run = pic_paragraph.add_run()
            run.add_picture(str(img_path), width=Inches(6.0))


def write_docx_from_markdown(
    *,
    markdown_text: str,
    output_path: str,
    course_name: str,
    slide_images: dict[int, list[SlideImageAsset]] | None = None,
) -> None:
    doc = Document()
    title = (course_name or "Lecture Notes").strip()
    doc.add_heading(title, level=0)

    _add_markdown_body(doc, markdown_text)
    _add_images_section(doc, slide_images or {})

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
