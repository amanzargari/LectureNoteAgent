from __future__ import annotations

import importlib
import json
import sys
import tempfile
import time
from pathlib import Path

try:
    from .agent import LectureNoteAgent
    from .config import AgentConfig
except ImportError:
    src_root = Path(__file__).resolve().parents[1]
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from lecture_note_agent.agent import LectureNoteAgent
    from lecture_note_agent.config import AgentConfig


_STAGE_LABELS = {
    "ingest":       "📂 Parsing slides & transcript",
    "ocr":          "🔍 OCR — extracting text from PDF",
    "source":       "🗂  Building source payload",
    "checklist":    "📋 Generating coverage checklist",
    "draft":        "✍️  Drafting lecture notes",
    "image-refine": "🖼  Refining image placement",
    "audit":        "🔎 Auditing coverage",
    "repair":       "🛠  Repair pass",
    "re-audit":     "🔎 Re-auditing after repair",
    "write":        "💾 Writing DOCX & artifacts",
    "done":         "✅ Done",
}

_STAGE_TIPS = {
    "checklist": "~30–60 s — model reads the full source bundle",
    "draft":     "~1–3 min — longest step; streaming output shown below",
    "repair":    "~1–2 min — regenerating missing sections",
    "audit":     "~20–40 s — strict JSON coverage check",
}


def _save_uploaded(upload, path: Path) -> None:
    path.write_bytes(upload.getbuffer())


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.1f}m"


def app() -> None:
    st = importlib.import_module("streamlit")
    st.set_page_config(page_title="LectureNoteAgent", page_icon="🧠", layout="wide")
    st.title("🧠 LectureNoteAgent")

    default_cfg = AgentConfig()

    if "last_run" not in st.session_state:
        st.session_state["last_run"] = None

    with st.sidebar:
        st.header("Models")
        model = st.text_input("Fallback model", value=default_cfg.model)
        model_ocr = st.text_input("OCR model", value=default_cfg.model_ocr)
        model_checklist = st.text_input("Checklist model", value=default_cfg.model_checklist)
        model_draft = st.text_input("Draft model", value=default_cfg.model_draft)
        model_audit = st.text_input("Audit model", value=default_cfg.model_audit)
        model_repair = st.text_input("Repair model", value=default_cfg.model_repair)
        st.divider()
        st.header("Pipeline")
        fast_mode = st.toggle(
            "Fast mode (skip audit & repair — ~2× faster)",
            value=default_cfg.fast_mode,
            help="Skips coverage audit and repair loops. Saves 2–4 minutes.",
        )
        max_repair_loops = st.slider("Max repair loops", min_value=0, max_value=4, value=default_cfg.max_repair_loops)
        max_model_calls = st.slider("Max model calls", min_value=3, max_value=20, value=default_cfg.max_model_calls)
        max_output_tokens = st.slider(
            "Max output tokens per call",
            min_value=512, max_value=8000, value=default_cfg.max_output_tokens, step=256,
            help="Lower = faster per call. 4000 ≈ 1–2 min draft; 8000 ≈ 2–4 min draft.",
        )
        st.caption(
            "**Speed guide:** Fast mode = ~2 min total. "
            "Normal mode = ~5–10 min. "
            "Each LLM call takes 30 s–3 min depending on output length."
        )

    course_name = st.text_input("Course name", value="My Course")
    col_a, col_b = st.columns(2)
    with col_a:
        slides_file = st.file_uploader("Slides (.pdf / .pptx / .md / .txt)", type=["pdf", "pptx", "md", "txt"])
    with col_b:
        transcript_file = st.file_uploader("Transcript (.txt / .md / .srt)", type=["txt", "md", "srt"])

    generate = st.button("🚀 Generate Lecture Notes", type="primary", use_container_width=True)

    if generate:
        if slides_file is None or transcript_file is None:
            st.error("Please upload both slides and transcript files.")
            return
        if not (default_cfg.api_key and default_cfg.api_key.strip()):
            st.error("OPENAI_API_KEY is missing. Please set it in your .env file.")
            return

        # ── Progress UI ────────────────────────────────────────────────────────
        progress_bar = st.progress(0.0, text="Starting…")
        step_log_area = st.empty()
        streaming_header = st.empty()
        streaming_area = st.empty()

        run_start = time.time()
        completed_steps: list[str] = []
        current: dict = {"stage": None, "label": None, "start": None}
        stream_buf: list[str] = []
        token_counter = [0]
        STREAM_UPDATE_EVERY = 40  # update preview every N tokens

        def _flush_log(extra_line: str = "") -> None:
            lines = list(completed_steps[-10:])
            if extra_line:
                lines.append(extra_line)
            step_log_area.markdown("\n\n".join(lines))

        def _on_progress(event: dict) -> None:
            # ── Streaming token ──────────────────────────────────────────────
            if event.get("type") == "token":
                stage = event.get("stage", "")
                tok = event.get("text", "")
                if stage in ("draft", "repair"):
                    stream_buf.append(tok)
                    token_counter[0] += 1
                    if token_counter[0] % STREAM_UPDATE_EVERY == 0:
                        preview = "".join(stream_buf)
                        streaming_header.markdown("**Live output preview:**")
                        streaming_area.markdown(preview[:4000] + ("…" if len(preview) > 4000 else ""))
                return

            # ── Stage transition ─────────────────────────────────────────────
            now = time.time()

            # Close out the previous step
            if current["stage"] is not None and current["start"] is not None:
                elapsed = now - current["start"]
                prev_label = current["label"] or current["stage"]
                completed_steps.append(f"✅ **{prev_label}** — {_fmt_elapsed(elapsed)}")
                # Clear streaming area when moving past draft/repair
                if current["stage"] in ("draft", "repair") and stream_buf:
                    streaming_header.empty()
                    streaming_area.empty()
                    stream_buf.clear()
                    token_counter[0] = 0

            stage = event.get("stage", "")
            msg = str(event.get("message", "Working…"))
            step_current = int(event.get("current", 0))
            step_total = max(1, int(event.get("total", 1)))
            label = _STAGE_LABELS.get(stage, msg)

            current["stage"] = stage
            current["label"] = label
            current["start"] = now

            total_elapsed = now - run_start
            tip = _STAGE_TIPS.get(stage, "")
            tip_str = f" *(tip: {tip})*" if tip else ""
            running_line = f"⏳ **{label}**{tip_str}  •  total elapsed: {_fmt_elapsed(total_elapsed)}"

            progress_bar.progress(step_current / step_total, text=f"{label}…")
            _flush_log(running_line)

        # ── Run agent ──────────────────────────────────────────────────────────
        with tempfile.TemporaryDirectory(prefix="slideagent_ui_") as tmp_dir:
            tmp = Path(tmp_dir)
            slides_path = tmp / slides_file.name
            transcript_path = tmp / transcript_file.name
            output_path = tmp / "lecture_notes.docx"
            artifacts_dir = tmp / "artifacts"

            _save_uploaded(slides_file, slides_path)
            _save_uploaded(transcript_file, transcript_path)

            config = AgentConfig(
                model=model.strip(),
                model_ocr=model_ocr.strip() or model.strip(),
                model_checklist=model_checklist.strip() or model.strip(),
                model_draft=model_draft.strip() or model.strip(),
                model_audit=model_audit.strip() or model.strip(),
                model_repair=model_repair.strip() or model.strip(),
                max_repair_loops=max_repair_loops,
                max_model_calls=max_model_calls,
                max_output_tokens=max_output_tokens,
                max_input_chars=300_000,
                fast_mode=fast_mode,
            )

            agent = LectureNoteAgent(config=config)

            try:
                artifacts = agent.run(
                    course_name=course_name.strip() or "Untitled Course",
                    slides_path=str(slides_path),
                    transcript_path=str(transcript_path),
                    output_path=str(output_path),
                    artifacts_dir=str(artifacts_dir),
                    progress_callback=_on_progress,
                )
            except RuntimeError as exc:
                st.error(str(exc))
                st.info(
                    "Tip: increase **Max model calls** in the sidebar or enable **Fast mode** to skip the repair loop."
                )
                return

            total_time = time.time() - run_start
            # Close the last step
            if current["stage"] is not None and current["start"] is not None:
                elapsed = time.time() - current["start"]
                completed_steps.append(f"✅ **{current['label'] or current['stage']}** — {_fmt_elapsed(elapsed)}")
            completed_steps.append(f"🏁 **Total time: {_fmt_elapsed(total_time)}**")

            progress_bar.progress(1.0, text=f"Done in {_fmt_elapsed(total_time)}")
            _flush_log()
            streaming_header.empty()
            streaming_area.empty()

            note_text = artifacts.final_markdown
            output_docx = output_path.read_bytes()
            
            pdf_path = output_path.with_suffix(".pdf")
            output_pdf = pdf_path.read_bytes() if pdf_path.exists() else b""

            st.session_state["last_run"] = {
                "note_text": note_text,
                "output_docx": output_docx,
                "output_pdf": output_pdf,
                "audit_json": artifacts.audit_json,
                "checklist_text": artifacts.checklist_markdown,
                "model_calls": int(getattr(artifacts, "model_calls", 0) or 0),
                "prompt_tokens": int(getattr(artifacts, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(artifacts, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(artifacts, "total_tokens", 0) or 0),
                "total_time": total_time,
            }

    # ── Results ────────────────────────────────────────────────────────────────
    last_run = st.session_state.get("last_run")
    if isinstance(last_run, dict):
        st.success(f"✅ Lecture notes generated in {_fmt_elapsed(last_run.get('total_time', 0))}.")

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Model Calls", last_run.get("model_calls", 0))
        m2.metric("Prompt Tokens", f"{last_run.get('prompt_tokens', 0):,}")
        m3.metric("Completion Tokens", f"{last_run.get('completion_tokens', 0):,}")
        m4.metric("Total Tokens", f"{last_run.get('total_tokens', 0):,}")
        m5.metric("Total Time", _fmt_elapsed(last_run.get("total_time", 0)))

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Lecture Notes")
            st.markdown(last_run.get("note_text", ""))
            st.download_button(
                "⬇️ Download lecture_notes.docx",
                data=last_run.get("output_docx", b""),
                file_name="lecture_notes.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
                key="download_lecture_notes",
            )
            if last_run.get("output_pdf"):
                st.download_button(
                    "⬇️ Download lecture_notes.pdf",
                    data=last_run.get("output_pdf", b""),
                    file_name="lecture_notes.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key="download_lecture_notes_pdf",
                )

        with col2:
            st.subheader("Audit Report")
            try:
                parsed = json.loads(last_run.get("audit_json", "{}"))
                coverage = parsed.get("coverage_percent", "?")
                passed = parsed.get("pass", False)
                badge = "✅ Pass" if passed else "⚠️ Partial"
                st.metric("Coverage", f"{coverage}%", badge)
            except Exception:
                pass
            st.code(last_run.get("audit_json", ""), language="json")
            st.download_button(
                "⬇️ Download audit.json",
                data=last_run.get("audit_json", ""),
                file_name="audit.json",
                mime="application/json",
                use_container_width=True,
                key="download_audit_json",
            )

            checklist_text = last_run.get("checklist_text", "")
            if checklist_text:
                with st.expander("Coverage Checklist"):
                    st.markdown(checklist_text)
                st.download_button(
                    "⬇️ Download checklist.md",
                    data=checklist_text,
                    file_name="checklist.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="download_checklist_md",
                )


def run() -> None:
    app()


if __name__ == "__main__":
    run()
