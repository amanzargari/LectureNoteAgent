from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class AgentConfig:
    model: str = os.getenv("OPENAI_MODEL", "qwen/qwen3.5-flash-02-23")
    model_ocr: str = os.getenv("OPENAI_MODEL_OCR", "qwen/qwen3.5-122b-a10b")
    model_checklist: str = os.getenv("OPENAI_MODEL_CHECKLIST", os.getenv("OPENAI_MODEL", "qwen/qwen3.5-flash-02-23"))
    model_draft: str = os.getenv("OPENAI_MODEL_DRAFT", os.getenv("OPENAI_MODEL", "qwen/qwen3.5-flash-02-23"))
    model_audit: str = os.getenv("OPENAI_MODEL_AUDIT", os.getenv("OPENAI_MODEL", "qwen/qwen3.5-flash-02-23"))
    model_repair: str = os.getenv("OPENAI_MODEL_REPAIR", os.getenv("OPENAI_MODEL", "qwen/qwen3.5-flash-02-23"))
    api_key: str | None = os.getenv("OPENAI_API_KEY")
    base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    max_repair_loops: int = int(os.getenv("MAX_REPAIR_LOOPS", "3"))
    max_model_calls: int = int(os.getenv("MAX_MODEL_CALLS", "6"))
    max_continuation_calls: int = int(os.getenv("MAX_CONTINUATION_CALLS", "2"))
    max_output_tokens: int = int(os.getenv("MAX_OUTPUT_TOKENS", "3500"))
    max_input_chars: int = int(os.getenv("MAX_INPUT_CHARS", "300000"))


def ensure_api_key(config: AgentConfig) -> None:
    if not config.api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Add it to your environment or .env file."
        )
