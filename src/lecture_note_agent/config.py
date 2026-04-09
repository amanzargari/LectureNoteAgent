from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentConfig:
    model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    api_key: str | None = os.getenv("OPENAI_API_KEY")
    base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    max_repair_loops: int = int(os.getenv("MAX_REPAIR_LOOPS", "3"))
    max_model_calls: int = int(os.getenv("MAX_MODEL_CALLS", "6"))
    max_output_tokens: int = int(os.getenv("MAX_OUTPUT_TOKENS", "3500"))
    max_input_chars: int = int(os.getenv("MAX_INPUT_CHARS", "300000"))
    enable_pdf_ocr: bool = _env_bool("ENABLE_PDF_OCR", True)
    enable_model_file_ocr: bool = _env_bool("ENABLE_MODEL_FILE_OCR", False)
    model_file_ocr_mode: str = os.getenv("MODEL_FILE_OCR_MODE", "auto")
    ocr_lang: str = os.getenv("OCR_LANG", "eng")
    ocr_dpi: int = int(os.getenv("OCR_DPI", "220"))


def ensure_api_key(config: AgentConfig) -> None:
    if not config.api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add it to your environment or .env file."
        )
