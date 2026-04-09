from __future__ import annotations

import importlib
import json
import sys
import tempfile
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


def _save_uploaded(upload, path: Path) -> None:
    path.write_bytes(upload.getbuffer())


def app() -> None:
    st = importlib.import_module("streamlit")
    st.set_page_config(page_title="SlideAGENT", page_icon="🧠", layout="wide")
    st.title("🧠 SlideAGENT — Lecture Notes from Slides + Transcript")
    st.caption("OpenAI-only pipeline: uploads slides + transcript, generates validated lecture notes, and shows progress + token usage.")

    default_cfg = AgentConfig()

    with st.sidebar:
        st.subheader("Model Settings")
        st.info("API key and base URL are read from .env only (OPENAI_API_KEY / OPENAI_BASE_URL).")
        model = st.text_input("OPENAI_MODEL", value=default_cfg.model)
        max_repair_loops = st.slider("Max repair loops", min_value=1, max_value=6, value=default_cfg.max_repair_loops)
        max_model_calls = st.slider("Max model calls", min_value=3, max_value=20, value=default_cfg.max_model_calls)
        max_output_tokens = st.slider("Max output tokens per call", min_value=512, max_value=8000, value=default_cfg.max_output_tokens, step=128)

        st.subheader("OCR Settings")
        enable_pdf_ocr = st.checkbox("Enable OCR for scanned PDFs", value=default_cfg.enable_pdf_ocr)
        enable_model_file_ocr = st.checkbox(
            "Enable model file OCR (PDF upload to model)",
            value=default_cfg.enable_model_file_ocr,
            help="Best for image-heavy/scanned PDFs when your model supports file input.",
        )
        model_file_ocr_mode = st.selectbox(
            "Model file OCR mode",
            options=["auto", "whole", "page"],
            index=["auto", "whole", "page"].index(default_cfg.model_file_ocr_mode if default_cfg.model_file_ocr_mode in {"auto", "whole", "page"} else "auto"),
            help="auto: try whole-file then page fallback; whole: one file call; page: per-page file calls.",
        )
        ocr_lang = st.text_input("OCR language", value=default_cfg.ocr_lang)
        ocr_dpi = st.slider("OCR DPI", min_value=120, max_value=400, value=default_cfg.ocr_dpi, step=10)

    course_name = st.text_input("Course name", value="My Course")
    slides_file = st.file_uploader("Upload slides (.pdf/.pptx/.md/.txt)", type=["pdf", "pptx", "md", "txt"])
    transcript_file = st.file_uploader("Upload transcript (.txt/.md)", type=["txt", "md"])

    generate = st.button("Generate Lecture Notes", type="primary", use_container_width=True)

    if generate:
        if slides_file is None or transcript_file is None:
            st.error("Please upload both slides and transcript files.")
            return

        if not (default_cfg.api_key and default_cfg.api_key.strip()):
            st.error("OPENAI_API_KEY is missing. Please set it in your .env file.")
            return

        progress_bar = st.progress(0.0, text="Starting...")
        progress_text = st.empty()

        with st.spinner("Running agent pipeline (ingest → draft → validate → repair)..."):
            with tempfile.TemporaryDirectory(prefix="slideagent_ui_") as tmp_dir:
                tmp = Path(tmp_dir)
                slides_path = tmp / slides_file.name
                transcript_path = tmp / transcript_file.name
                output_path = tmp / "lecture_notes.md"
                artifacts_dir = tmp / "artifacts"

                _save_uploaded(slides_file, slides_path)
                _save_uploaded(transcript_file, transcript_path)

                config = AgentConfig(
                    model=model.strip(),
                    max_repair_loops=max_repair_loops,
                    max_model_calls=max_model_calls,
                    max_output_tokens=max_output_tokens,
                    max_input_chars=300000,
                    enable_pdf_ocr=enable_pdf_ocr,
                    enable_model_file_ocr=enable_model_file_ocr,
                    model_file_ocr_mode=model_file_ocr_mode,
                    ocr_lang=ocr_lang.strip() or "eng",
                    ocr_dpi=ocr_dpi,
                )

                agent = LectureNoteAgent(config=config)

                def _on_progress(event: dict) -> None:
                    current = int(event.get("current", 0))
                    total = max(1, int(event.get("total", 1)))
                    ratio = min(1.0, max(0.0, current / total))
                    msg = str(event.get("message", "Working..."))
                    progress_bar.progress(ratio, text=msg)
                    progress_text.caption(f"{current}/{total} • {event.get('stage', 'run')}: {msg}")

                artifacts = agent.run(
                    course_name=course_name.strip() or "Untitled Course",
                    slides_path=str(slides_path),
                    transcript_path=str(transcript_path),
                    output_path=str(output_path),
                    artifacts_dir=str(artifacts_dir),
                    enable_pdf_ocr=enable_pdf_ocr,
                    enable_model_file_ocr=enable_model_file_ocr,
                    model_file_ocr_mode=model_file_ocr_mode,
                    ocr_lang=ocr_lang.strip() or "eng",
                    ocr_dpi=ocr_dpi,
                    progress_callback=_on_progress,
                )
                progress_bar.progress(1.0, text="Completed")

                note_text = output_path.read_text(encoding="utf-8")

                st.success("Lecture notes generated.")
                usage_col1, usage_col2, usage_col3, usage_col4 = st.columns(4)
                usage_col1.metric("Model Calls", str(getattr(artifacts, "model_calls", 0)))
                usage_col2.metric("Prompt Tokens", str(getattr(artifacts, "prompt_tokens", 0)))
                usage_col3.metric("Completion Tokens", str(getattr(artifacts, "completion_tokens", 0)))
                usage_col4.metric("Total Tokens", str(getattr(artifacts, "total_tokens", 0)))

                col1, col2 = st.columns(2)

                with col1:
                    st.subheader("Lecture Notes")
                    st.markdown(note_text)
                    st.download_button(
                        "Download lecture_notes.md",
                        data=note_text,
                        file_name="lecture_notes.md",
                        mime="text/markdown",
                        use_container_width=True,
                    )

                with col2:
                    st.subheader("Audit JSON")
                    st.code(artifacts.audit_json, language="json")
                    st.download_button(
                        "Download audit.json",
                        data=artifacts.audit_json,
                        file_name="audit.json",
                        mime="application/json",
                        use_container_width=True,
                    )

                    checklist_path = artifacts_dir / "checklist.md"
                    if checklist_path.exists():
                        checklist_text = checklist_path.read_text(encoding="utf-8")
                        with st.expander("Checklist"):
                            st.markdown(checklist_text)
                        st.download_button(
                            "Download checklist.md",
                            data=checklist_text,
                            file_name="checklist.md",
                            mime="text/markdown",
                            use_container_width=True,
                        )

                with st.expander("Run Summary"):
                    try:
                        parsed = json.loads(artifacts.audit_json)
                        st.json(parsed)
                    except Exception:
                        st.text(artifacts.audit_json)


def run() -> None:
    app()


if __name__ == "__main__":
    run()
