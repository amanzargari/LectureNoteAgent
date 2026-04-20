from __future__ import annotations

import re
from pathlib import Path

import markdown
from weasyprint import HTML

from .docx_utils import _build_image_index, _resolve_image_asset
from .io_utils import SlideImageAsset

def write_pdf_from_markdown(
    *,
    markdown_text: str,
    output_path: str,
    course_name: str,
    slide_images: dict[int, list[SlideImageAsset]] | None = None,
) -> None:
    """Generate a PDF document from Markdown, resolving inner image references."""
    image_index = _build_image_index(slide_images or {})

    def replacer(match: re.Match) -> str:
        caption = match.group("caption")
        ref = match.group("target")
        if ref.lower().startswith("image_ref:"):
            ref_key = ref[10:].strip()
            asset = _resolve_image_asset(ref_key, image_index)
            if asset and Path(asset.image_path).exists():
                abs_path = Path(asset.image_path).absolute()
                return f"![{caption}](file://{abs_path})"
        return match.group(0)

    # Resolve image placeholders to local file URIs
    # Regex matches ![caption](target)
    processed_markdown = re.sub(
        r"!\[(?P<caption>[^\]]*)\]\((?P<target>[^)]+)\)", 
        replacer, 
        markdown_text
    )

    # Convert Markdown to HTML
    html_body = markdown.markdown(
        processed_markdown,
        extensions=["tables", "fenced_code", "sane_lists"]
    )

    # Basic CSS for professional look
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{course_name}</title>
        <style>
            @page {{
                margin: 1in;
            }}
            body {{
                font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
                font-size: 14px;
                line-height: 1.6;
                color: #333;
            }}
            h1, h2, h3, h4 {{
                color: #222;
                margin-top: 1.5em;
                margin-bottom: 0.5em;
            }}
            h1 {{ font-size: 24px; border-bottom: 1px solid #ccc; padding-bottom: 10px; }}
            h2 {{ font-size: 20px; }}
            h3 {{ font-size: 16px; }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 1em 0;
            }}
            table, th, td {{
                border: 1px solid #ddd;
            }}
            th, td {{
                padding: 8px;
                text-align: left;
            }}
            th {{
                background-color: #f2f2f2;
                font-weight: bold;
            }}
            img {{
                max-width: 100%;
                height: auto;
                display: block;
                margin: 1.5em auto;
                box-shadow: 0 0 5px rgba(0,0,0,0.1);
            }}
            pre, code {{
                background-color: #f8f8f8;
                border-radius: 4px;
                font-family: Consolas, monospace;
            }}
            pre {{
                padding: 12px;
                overflow-x: auto;
                border: 1px solid #eee;
            }}
            blockquote {{
                border-left: 4px solid #ccc;
                margin: 1.5em 0;
                padding-left: 1em;
                color: #666;
            }}
        </style>
    </head>
    <body>
        <h1>{course_name}</h1>
        {html_body}
    </body>
    </html>
    """

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    
    HTML(string=html_content).write_pdf(str(out))
