from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from lecture_note_agent.docx_utils import write_docx_from_markdown
from lecture_note_agent.io_utils import SlideImageAsset, extract_slide_images


def test_write_docx_from_markdown_embeds_images(tmp_path: Path) -> None:
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (120, 80), color=(255, 0, 0)).save(image_path)

    output_docx = tmp_path / "notes.docx"
    write_docx_from_markdown(
        markdown_text="# Lecture\n\n- Point one\n- Point two",
        output_path=str(output_docx),
        course_name="Test Course",
        slide_images={
            1: [
                SlideImageAsset(
                    slide_number=1,
                    image_ref="img_1",
                    image_path=str(image_path),
                )
            ]
        },
    )

    assert output_docx.exists()
    with ZipFile(output_docx, "r") as archive:
        media_files = [name for name in archive.namelist() if name.startswith("word/media/")]
    assert media_files, "Expected embedded media files in generated DOCX"


def test_extract_slide_images_applies_pptx_crop(tmp_path: Path) -> None:
    source_image = tmp_path / "src.png"
    Image.new("RGB", (100, 50), color=(0, 128, 255)).save(source_image)

    pptx_path = tmp_path / "deck.pptx"
    prs = Presentation()
    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)
    picture = slide.shapes.add_picture(str(source_image), Inches(1), Inches(1), width=Inches(4))
    picture.crop_left = 0.1
    picture.crop_right = 0.1
    prs.save(str(pptx_path))

    images = extract_slide_images(str(pptx_path), artifacts_dir=str(tmp_path / "artifacts"))
    assert 1 in images
    assert images[1]

    extracted_path = Path(images[1][0].image_path)
    assert extracted_path.exists()

    with Image.open(extracted_path) as cropped:
        cropped_width, _ = cropped.size

    assert cropped_width < 100
