from __future__ import annotations

import click
from rich.console import Console

from .agent import LectureNoteAgent


console = Console()


@click.command()
@click.option("--course-name", required=True, help="Course name for note title/context.")
@click.option("--slides", "slides_path", required=True, type=click.Path(exists=True), help="Path to slides (.pdf, .pptx, .md, .txt).")
@click.option("--transcript", "transcript_path", required=True, type=click.Path(exists=True), help="Path to transcript text file.")
@click.option("--output", "output_path", required=True, type=click.Path(), help="Output markdown file path.")
@click.option("--artifacts-dir", default="./artifacts", show_default=True, type=click.Path(), help="Directory for checklist/audit artifacts.")
@click.option("--pdf-ocr/--no-pdf-ocr", default=None, help="Enable OCR fallback for scanned PDFs.")
@click.option("--model-file-ocr/--no-model-file-ocr", default=None, help="Use file-capable model OCR for PDFs.")
@click.option("--model-file-ocr-mode", default=None, type=click.Choice(["auto", "whole", "page"]), help="Model OCR strategy: whole file, per-page, or auto.")
@click.option("--ocr-lang", default=None, help="OCR language for Tesseract (e.g., eng).")
@click.option("--ocr-dpi", default=None, type=int, help="Render DPI used for PDF OCR.")
def main(
    course_name: str,
    slides_path: str,
    transcript_path: str,
    output_path: str,
    artifacts_dir: str,
    pdf_ocr: bool | None,
    model_file_ocr: bool | None,
    model_file_ocr_mode: str | None,
    ocr_lang: str | None,
    ocr_dpi: int | None,
) -> None:
    """Generate comprehensive lecture notes from slides + class transcript."""
    console.print("[bold cyan]Running Lecture Note Agent...[/bold cyan]")
    agent = LectureNoteAgent()
    artifacts = agent.run(
        course_name=course_name,
        slides_path=slides_path,
        transcript_path=transcript_path,
        output_path=output_path,
        artifacts_dir=artifacts_dir,
        enable_pdf_ocr=pdf_ocr,
        enable_model_file_ocr=model_file_ocr,
        model_file_ocr_mode=model_file_ocr_mode,
        ocr_lang=ocr_lang,
        ocr_dpi=ocr_dpi,
    )
    console.print("[green]Done.[/green]")
    console.print(f"Output: [bold]{output_path}[/bold]")
    console.print(
        "Usage: "
        f"calls={getattr(artifacts, 'model_calls', 0)}, "
        f"prompt_tokens={getattr(artifacts, 'prompt_tokens', 0)}, "
        f"completion_tokens={getattr(artifacts, 'completion_tokens', 0)}, "
        f"total_tokens={getattr(artifacts, 'total_tokens', 0)}"
    )
    console.print("Audit:")
    console.print(artifacts.audit_json)


if __name__ == "__main__":
    main()
