from __future__ import annotations

import hashlib
import json
import importlib
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .config import AgentConfig, ensure_api_key
from .docx_utils import write_docx_from_markdown
from .io_utils import (
    build_source_payload,
    extract_slide_images,
    extract_formula_candidates,
    has_meaningful_text,
    parse_slides,
    parse_transcript,
)
from .models import GenerationArtifacts, SourceBundle
from .prompts import AUDIT_PROMPT, CHECKLIST_PROMPT, DRAFT_NOTES_PROMPT, IMAGE_SELECTION_PROMPT, REPAIR_PROMPT


class LectureNoteAgent:
    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        ensure_api_key(self.config)
        openai_module = importlib.import_module("openai")
        OpenAI = getattr(openai_module, "OpenAI")
        self.client = OpenAI(api_key=self.config.api_key, base_url=self.config.base_url)
        self._model_calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0

    def _emit_progress(
        self,
        callback: Callable[[dict], None] | None,
        stage: str,
        message: str,
        current: int,
        total: int,
    ) -> None:
        if callback is None:
            return
        callback(
            {
                "stage": stage,
                "message": message,
                "current": current,
                "total": max(1, total),
            }
        )

    def _enforce_call_limit(self) -> None:
        if self._model_calls >= self.config.max_model_calls:
            raise RuntimeError(
                f"Model call limit reached ({self.config.max_model_calls}). "
                "Increase MAX_MODEL_CALLS only if needed."
            )

    def _accumulate_usage(self, usage: object) -> None:
        if usage is None:
            return

        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        total = getattr(usage, "total_tokens", None)

        if prompt is None:
            prompt = getattr(usage, "input_tokens", 0)
        if completion is None:
            completion = getattr(usage, "output_tokens", 0)
        if total is None:
            total = (prompt or 0) + (completion or 0)

        self._prompt_tokens += int(prompt or 0)
        self._completion_tokens += int(completion or 0)
        self._total_tokens += int(total or 0)

    def _chat_once(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        model: str | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> tuple[str, str | None]:
        self._enforce_call_limit()
        self._model_calls += 1
        resolved_model = (model or self.config.model).strip()
        timeout_seconds = max(1, int(getattr(self.config, "request_timeout_seconds", 180) or 180))
        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt[: self.config.max_input_chars]},
        ]

        if on_token is not None:
            try:
                content = ""
                finish_reason = None
                with self.client.chat.completions.create(
                    model=resolved_model,
                    temperature=temperature,
                    max_tokens=self.config.max_output_tokens,
                    timeout=timeout_seconds,
                    stream=True,
                    messages=msgs,
                ) as stream:
                    for chunk in stream:
                        choice = chunk.choices[0] if chunk.choices else None
                        if not choice:
                            continue
                        delta = (choice.delta.content or "") if choice.delta else ""
                        if delta:
                            content += delta
                            on_token(delta)
                        if getattr(choice, "finish_reason", None):
                            finish_reason = choice.finish_reason
                return content, finish_reason
            except Exception:
                # Fall back to non-streaming if the model/provider rejects stream=True
                pass

        response = self.client.chat.completions.create(
            model=resolved_model,
            temperature=temperature,
            max_tokens=self.config.max_output_tokens,
            timeout=timeout_seconds,
            messages=msgs,
        )
        self._accumulate_usage(getattr(response, "usage", None))
        choice = response.choices[0]
        content = choice.message.content or ""
        finish_reason = getattr(choice, "finish_reason", None)
        return content, finish_reason

    def _chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        model: str | None = None,
        allow_continuation: bool = False,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        text, finish_reason = self._chat_once(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            model=model,
            on_token=on_token,
        )
        if not allow_continuation:
            return text

        combined = text or ""
        continuation_calls = 0
        while (
            finish_reason == "length"
            and continuation_calls < max(0, int(self.config.max_continuation_calls))
        ):
            continuation_calls += 1
            tail = combined[-4000:]
            continuation_prompt = (
                "Continue the SAME markdown document from exactly where it stopped. "
                "Do not restart. Do not repeat existing content. "
                "Start from the first incomplete sentence or heading and continue to completion.\n\n"
                "Document tail:\n"
                f"{tail}"
            )
            try:
                next_chunk, finish_reason = self._chat_once(
                    system_prompt=system_prompt,
                    user_prompt=continuation_prompt,
                    temperature=temperature,
                    model=model,
                    on_token=on_token,
                )
            except RuntimeError as exc:
                if "Model call limit reached" in str(exc):
                    break
                raise
            if not next_chunk.strip():
                break
            combined += next_chunk.lstrip()

        return combined

    def _extract_response_text(self, response: object) -> str:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        output = getattr(response, "output", None)
        if isinstance(output, list):
            text_bits: list[str] = []
            for item in output:
                content = getattr(item, "content", None)
                if not content:
                    continue
                for c in content:
                    txt = getattr(c, "text", None)
                    if isinstance(txt, str) and txt.strip():
                        text_bits.append(txt.strip())
            if text_bits:
                return "\n".join(text_bits)

        return ""

    def _file_ocr_call(self, prompt: str, file_path: str, model: str | None = None) -> str:
        uploaded = None
        try:
            with open(file_path, "rb") as f:
                uploaded = self.client.files.create(file=f, purpose="user_data")

            file_id = getattr(uploaded, "id", None)
            if not file_id:
                return ""

            self._enforce_call_limit()
            self._model_calls += 1
            resolved_model = (model or self.config.model_ocr or self.config.model).strip()
            timeout_seconds = max(1, int(getattr(self.config, "request_timeout_seconds", 180) or 180))
            response = self.client.responses.create(
                model=resolved_model,
                max_output_tokens=self.config.max_output_tokens,
                timeout=timeout_seconds,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_file", "file_id": file_id},
                        ],
                    }
                ],
            )
            self._accumulate_usage(getattr(response, "usage", None))
            return self._extract_response_text(response)
        except Exception:
            return ""
        finally:
            if uploaded is not None:
                try:
                    self.client.files.delete(getattr(uploaded, "id"))
                except Exception:
                    pass

    def _parse_page_json(self, raw: str) -> dict[int, str]:
        if not raw.strip():
            return {}

        candidate = raw
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return {}
            try:
                data = json.loads(raw[start : end + 1])
            except Exception:
                return {}

        pages = data.get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, list):
            return {}

        out: dict[int, str] = {}
        for item in pages:
            if not isinstance(item, dict):
                continue
            page = item.get("page")
            text = item.get("text")
            if isinstance(page, int) and isinstance(text, str) and text.strip():
                out[page] = text.strip()
        return out

    def _ocr_pdf_via_model_whole(self, pdf_path: str, total_pages: int) -> dict[int, str]:
        prompt = (
            "Extract text from this lecture PDF with high fidelity, including scanned/image text and formulas. "
            "Return ONLY strict JSON with this schema: "
            '{"pages":[{"page":1,"text":"..."}]}. '
            f"The PDF has {total_pages} pages. Include every page from 1..{total_pages}."
        )
        raw = self._file_ocr_call(prompt, pdf_path, model=self.config.model_ocr)
        return self._parse_page_json(raw)

    def _ocr_pdf_via_model_per_page(self, pdf_path: str, page_numbers: list[int]) -> dict[int, str]:
        if not page_numbers:
            return {}

        page_map: dict[int, str] = {}
        reader = PdfReader(pdf_path)

        for page_number in page_numbers:
            page_idx = page_number - 1
            if page_idx < 0 or page_idx >= len(reader.pages):
                continue

            with tempfile.NamedTemporaryFile(suffix=f"_p{page_number}.pdf", delete=True) as tmp:
                writer = PdfWriter()
                writer.add_page(reader.pages[page_idx])
                writer.write(tmp)
                tmp.flush()

                prompt = (
                    "Extract all text from this single lecture slide page with high fidelity, including formulas and symbols. "
                    "Return ONLY the plain extracted text for this page."
                )
                text = self._file_ocr_call(prompt, tmp.name, model=self.config.model_ocr)
                if text.strip():
                    page_map[page_number] = text.strip()

        return page_map

    def _merge_model_ocr_text(
        self,
        slides_path: str,
        slides: list,
        mode: str,
    ) -> list:
        pdf_path = Path(slides_path)
        if pdf_path.suffix.lower() != ".pdf":
            return slides

        mode_norm = (mode or "auto").strip().lower()
        total_pages = len(slides)

        weak_pages = [s.slide_number for s in slides if not has_meaningful_text(s.text)]

        page_text_map: dict[int, str] = {}
        if mode_norm in {"auto", "whole"}:
            page_text_map = self._ocr_pdf_via_model_whole(str(pdf_path), total_pages=total_pages)

        if mode_norm == "page":
            target_pages = weak_pages or list(range(1, total_pages + 1))
            page_text_map = self._ocr_pdf_via_model_per_page(str(pdf_path), target_pages)

        if mode_norm == "auto":
            missing_after_whole = [
                p for p in weak_pages if not has_meaningful_text(page_text_map.get(p, ""))
            ]
            if missing_after_whole:
                page_fallback = self._ocr_pdf_via_model_per_page(str(pdf_path), missing_after_whole)
                page_text_map.update(page_fallback)

        for s in slides:
            model_text = page_text_map.get(s.slide_number, "").strip()
            if not model_text:
                continue

            if has_meaningful_text(s.text):
                merged_text = f"{s.text}\n\n[MODEL_FILE_OCR]\n{model_text}".strip()
            else:
                merged_text = model_text

            s.text = merged_text
            if not s.title and merged_text:
                s.title = merged_text.splitlines()[0][:140]
            if "model_file_ocr_pdf" not in s.image_refs:
                s.image_refs.append("model_file_ocr_pdf")
            s.formula_candidates = extract_formula_candidates(merged_text)

        return slides

    def _audit_notes(self, checklist_md: str, source_payload: str, notes_md: str) -> dict:
        payload = (
            "## Checklist\n"
            f"{checklist_md}\n\n"
            "## Source Bundle\n"
            f"{source_payload}\n\n"
            "## Lecture Notes\n"
            f"{notes_md}"
        )
        raw = self._chat(AUDIT_PROMPT, payload, temperature=0, model=self.config.model_audit)

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(raw[start : end + 1])
            return {
                "coverage_percent": 0,
                "missing_items": ["PARSER_ERROR"],
                "weak_items": [],
                "issues": ["Audit response was not valid JSON."],
                "pass": False,
            }

    def _normalize_text_for_compare(self, text: str) -> str:
        return " ".join((text or "").split())

    def _text_fingerprint(self, text: str) -> str:
        normalized = self._normalize_text_for_compare(text)
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    def _sanitize_final_markdown(self, notes_md: str) -> str:
        text = notes_md or ""

        # Remove internal source/checklist tags that are useful for auditing but noisy for users.
        text = re.sub(r"\[(?:S\d+(?:,S\d+)*|T\d+|C-[A-Z]+-[^\]]+)\]", "", text)

        # Remove old placeholder section if model still emits it.
        text = re.sub(
            r"(?is)\n##\s*Image Placeholders to Add Later\b.*?(?=\n##\s|\Z)",
            "\n",
            text,
        )

        # Cleanup whitespace artifacts after tag removals.
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Guarantee the three required closing sections exist.
        required_sections = [
            ("## Special Mentions from Instructor", "No special mentions in this lecture."),
            ("## Formula Sheet", "No formulas in this lecture."),
        ]
        for heading, fallback in required_sections:
            if heading not in text:
                text = text.rstrip() + f"\n\n{heading}\n\n{fallback}\n"

        # Key Takeaways: only add the heading, let repair pass fill it
        if "## Key Takeaways" not in text:
            text = text.rstrip() + "\n\n## Key Takeaways\n\n(_missing — repair pass should fill this_)\n"

        return text.strip()

    def run(
        self,
        course_name: str,
        slides_path: str,
        transcript_path: str,
        output_path: str,
        artifacts_dir: str | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> GenerationArtifacts:
        fast_mode = bool(getattr(self.config, "fast_mode", False))
        allow_continuation = not fast_mode

        if fast_mode:
            estimated_steps = 6
        else:
            estimated_steps = max(9, 9 + (self.config.max_repair_loops * 2))

        step = 1
        self._emit_progress(progress_callback, "ingest", "Parsing slides and transcript", step, estimated_steps)
        slides = parse_slides(slides_path)
        if Path(slides_path).suffix.lower() == ".pdf":
            ocr_mode = str(getattr(self.config, "pdf_ocr_mode", "auto") or "auto").strip().lower()
            if ocr_mode not in {"auto", "whole", "page"}:
                ocr_mode = "auto"
            if fast_mode:
                ocr_mode = "whole"

            step += 1
            self._emit_progress(progress_callback, "ocr", "Running model OCR on PDF", step, estimated_steps)
            slides = self._merge_model_ocr_text(
                slides_path=slides_path,
                slides=slides,
                mode=ocr_mode,
            )

        transcript = parse_transcript(transcript_path)
        source = SourceBundle(course_name=course_name, slides=slides, transcript=transcript)

        step += 1
        self._emit_progress(progress_callback, "source", "Building source payload", step, estimated_steps)
        source_payload = build_source_payload(course_name, slides, transcript)

        step += 1
        self._emit_progress(progress_callback, "checklist", "Generating coverage checklist", step, estimated_steps)
        checklist_md = self._chat(
            CHECKLIST_PROMPT,
            f"Generate coverage checklist from this source bundle:\n\n{source_payload}",
            temperature=0,
            model=self.config.model_checklist,
            allow_continuation=allow_continuation,
        )

        draft_input = (
            f"## Checklist\n{checklist_md}\n\n"
            f"## Source Bundle\n{source_payload}\n"
        )
        def _token_cb(stage_name: str) -> Callable[[str], None] | None:
            if progress_callback is None:
                return None
            def _cb(tok: str) -> None:
                progress_callback({"type": "token", "stage": stage_name, "text": tok})
            return _cb

        step += 1
        self._emit_progress(progress_callback, "draft", "Drafting lecture notes", step, estimated_steps)
        notes_md = self._chat(
            DRAFT_NOTES_PROMPT,
            draft_input,
            temperature=0.2,
            model=self.config.model_draft,
            allow_continuation=allow_continuation,
            on_token=_token_cb("draft"),
        )

        has_any_image_refs = any(getattr(s, "image_refs", None) for s in slides)
        if (
            not fast_mode
            and has_any_image_refs
            and bool(getattr(self.config, "enable_image_selection_refine", True))
            and self._model_calls < self.config.max_model_calls
        ):
            step += 1
            self._emit_progress(
                progress_callback,
                "image-refine",
                "Refining inline image selection and captions",
                step,
                estimated_steps,
            )
            refine_input = (
                f"## Source Bundle\n{source_payload}\n\n"
                f"## Current Notes\n{notes_md}"
            )
            try:
                refined_notes = self._chat(
                    IMAGE_SELECTION_PROMPT,
                    refine_input,
                    temperature=0.1,
                    model=getattr(self.config, "model_image_selection", self.config.model_draft),
                    allow_continuation=False,
                )
                if isinstance(refined_notes, str) and refined_notes.strip():
                    notes_md = refined_notes
            except Exception:
                pass

        if fast_mode:
            audit = {
                "coverage_percent": 0,
                "missing_items": [],
                "weak_items": [],
                "issues": ["Fast mode enabled: skipped audit and repair loop for speed."],
                "pass": False,
            }
        else:
            step += 1
            self._emit_progress(progress_callback, "audit", "Auditing coverage and quality", step, estimated_steps)
            audit = self._audit_notes(checklist_md, source_payload, notes_md)

            seen_repair_fingerprints: set[str] = {self._text_fingerprint(notes_md)}
            no_progress_passes = 0
            max_no_progress = max(1, int(getattr(self.config, "max_repair_no_progress", 1) or 1))

            for i in range(self.config.max_repair_loops):
                if audit.get("pass") is True:
                    break

                if self._model_calls >= self.config.max_model_calls:
                    issues = list(audit.get("issues") or [])
                    issues.append(
                        f"Stopped repair loop early: model call limit reached ({self.config.max_model_calls})."
                    )
                    audit["issues"] = issues
                    break

                step += 1
                self._emit_progress(
                    progress_callback,
                    "repair",
                    f"Repair pass {i + 1}: regenerating missing sections",
                    step,
                    estimated_steps,
                )
                repair_input = (
                    f"## Audit JSON\n{json.dumps(audit, ensure_ascii=False, indent=2)}\n\n"
                    f"## Checklist\n{checklist_md}\n\n"
                    f"## Source Bundle\n{source_payload}\n\n"
                    f"## Current Notes\n{notes_md}"
                )
                previous_notes_md = notes_md
                try:
                    repaired_notes_md = self._chat(
                        REPAIR_PROMPT,
                        repair_input,
                        temperature=0.1,
                        model=self.config.model_repair,
                        allow_continuation=allow_continuation,
                        on_token=_token_cb("repair"),
                    )
                except RuntimeError as exc:
                    if "Model call limit reached" in str(exc):
                        issues = list(audit.get("issues") or [])
                        issues.append(
                            f"Stopped repair generation: model call limit reached ({self.config.max_model_calls})."
                        )
                        audit["issues"] = issues
                        break
                    raise
                except Exception as exc:
                    issues = list(audit.get("issues") or [])
                    issues.append(f"Stopped repair generation due to model timeout/error: {exc}")
                    audit["issues"] = issues
                    break

                repaired_notes_md = (repaired_notes_md or "").strip()
                if not repaired_notes_md:
                    issues = list(audit.get("issues") or [])
                    issues.append("Repair appears stuck: model returned empty content.")
                    audit["issues"] = issues
                    break

                repaired_fingerprint = self._text_fingerprint(repaired_notes_md)
                if repaired_fingerprint in seen_repair_fingerprints:
                    issues = list(audit.get("issues") or [])
                    issues.append("Repair appears stuck: repeated output detected.")
                    audit["issues"] = issues
                    break

                if self._normalize_text_for_compare(repaired_notes_md) == self._normalize_text_for_compare(previous_notes_md):
                    no_progress_passes += 1
                    if no_progress_passes >= max_no_progress:
                        issues = list(audit.get("issues") or [])
                        issues.append("Repair appears stuck: model returned unchanged content.")
                        audit["issues"] = issues
                        break
                else:
                    no_progress_passes = 0

                seen_repair_fingerprints.add(repaired_fingerprint)
                notes_md = repaired_notes_md

                if self._model_calls >= self.config.max_model_calls:
                    issues = list(audit.get("issues") or [])
                    issues.append(
                        f"Skipped re-audit: model call limit reached ({self.config.max_model_calls})."
                    )
                    audit["issues"] = issues
                    break

                step += 1
                self._emit_progress(
                    progress_callback,
                    "re-audit",
                    f"Repair pass {i + 1}: re-auditing notes",
                    step,
                    estimated_steps,
                )
                try:
                    audit = self._audit_notes(checklist_md, source_payload, notes_md)
                except RuntimeError as exc:
                    if "Model call limit reached" in str(exc):
                        issues = list(audit.get("issues") or [])
                        issues.append(
                            f"Stopped re-audit: model call limit reached ({self.config.max_model_calls})."
                        )
                        audit["issues"] = issues
                        break
                    raise
                except Exception as exc:
                    issues = list(audit.get("issues") or [])
                    issues.append(f"Stopped re-audit due to timeout/error: {exc}")
                    audit["issues"] = issues
                    break

        final_notes_md = self._sanitize_final_markdown(notes_md)

        step += 1
        self._emit_progress(progress_callback, "write", "Writing output and artifacts", step, estimated_steps)
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            slide_images = extract_slide_images(slides_path=slides_path, artifacts_dir=artifacts_dir)
        except Exception:
            slide_images = {}

        write_docx_from_markdown(
            markdown_text=final_notes_md,
            output_path=str(out_path),
            course_name=course_name,
            slide_images=slide_images,
        )

        if artifacts_dir:
            art = Path(artifacts_dir)
            art.mkdir(parents=True, exist_ok=True)
            (art / "source_bundle.json").write_text(source.model_dump_json(indent=2), encoding="utf-8")
            (art / "checklist.md").write_text(checklist_md, encoding="utf-8")
            (art / "audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
            (art / "draft_or_final_notes.md").write_text(final_notes_md, encoding="utf-8")

        self._emit_progress(
            progress_callback,
            "done",
            "Completed generation",
            estimated_steps,
            estimated_steps,
        )

        return GenerationArtifacts(
            checklist_markdown=checklist_md,
            draft_markdown=notes_md,
            final_markdown=final_notes_md,
            audit_json=json.dumps(audit, indent=2),
            model_calls=self._model_calls,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._total_tokens,
        )
