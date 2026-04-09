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

    if "last_run" not in st.session_state:
        st.session_state["last_run"] = None

    with st.sidebar:
        model = st.text_input("OPENAI_MODEL (fallback)", value=default_cfg.model)
        model_ocr = st.text_input("OPENAI_MODEL_OCR", value=default_cfg.model_ocr)
        model_checklist = st.text_input("OPENAI_MODEL_CHECKLIST", value=default_cfg.model_checklist)
        model_draft = st.text_input("OPENAI_MODEL_DRAFT", value=default_cfg.model_draft)
        model_audit = st.text_input("OPENAI_MODEL_AUDIT", value=default_cfg.model_audit)
        model_repair = st.text_input("OPENAI_MODEL_REPAIR", value=default_cfg.model_repair)
        max_repair_loops = st.slider("Max repair loops", min_value=1, max_value=6, value=default_cfg.max_repair_loops)
        max_model_calls = st.slider("Max model calls", min_value=3, max_value=20, value=default_cfg.max_model_calls)
        max_output_tokens = st.slider("Max output tokens per call", min_value=512, max_value=8000, value=default_cfg.max_output_tokens, step=128)

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
                    model_ocr=model_ocr.strip() or model.strip(),
                    model_checklist=model_checklist.strip() or model.strip(),
                    model_draft=model_draft.strip() or model.strip(),
                    model_audit=model_audit.strip() or model.strip(),
                    model_repair=model_repair.strip() or model.strip(),
                    max_repair_loops=max_repair_loops,
                    max_model_calls=max_model_calls,
                    max_output_tokens=max_output_tokens,
                    max_input_chars=300000,
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
                    progress_callback=_on_progress,
                )
                progress_bar.progress(1.0, text="Completed")

                note_text = output_path.read_text(encoding="utf-8")

                st.session_state["last_run"] = {
                    "note_text": note_text,
                    "audit_json": artifacts.audit_json,
                    "checklist_text": artifacts.checklist_markdown,
                    "model_calls": int(getattr(artifacts, "model_calls", 0) or 0),
                    "prompt_tokens": int(getattr(artifacts, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(artifacts, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(artifacts, "total_tokens", 0) or 0),
                }

    last_run = st.session_state.get("last_run")
    if isinstance(last_run, dict):
        st.success("Lecture notes generated.")
        usage_col1, usage_col2, usage_col3, usage_col4 = st.columns(4)
        usage_col1.metric("Model Calls", str(last_run.get("model_calls", 0)))
        usage_col2.metric("Prompt Tokens", str(last_run.get("prompt_tokens", 0)))
        usage_col3.metric("Completion Tokens", str(last_run.get("completion_tokens", 0)))
        usage_col4.metric("Total Tokens", str(last_run.get("total_tokens", 0)))

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Lecture Notes")
            st.markdown(last_run.get("note_text", ""))
            st.download_button(
                "Download lecture_notes.md",
                data=last_run.get("note_text", ""),
                file_name="lecture_notes.md",
                mime="text/markdown",
                use_container_width=True,
                key="download_lecture_notes",
            )

        with col2:
            st.subheader("Audit JSON")
            st.code(last_run.get("audit_json", ""), language="json")
            st.download_button(
                "Download audit.json",
                data=last_run.get("audit_json", ""),
                file_name="audit.json",
                mime="application/json",
                use_container_width=True,
                key="download_audit_json",
            )

            checklist_text = last_run.get("checklist_text", "")
            if checklist_text:
                with st.expander("Checklist"):
                    st.markdown(checklist_text)
                st.download_button(
                    "Download checklist.md",
                    data=checklist_text,
                    file_name="checklist.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key="download_checklist_md",
                )

        with st.expander("Run Summary"):
            try:
                parsed = json.loads(last_run.get("audit_json", "{}"))
                st.json(parsed)
            except Exception:
                st.text(last_run.get("audit_json", ""))


def run() -> None:
    app()


if __name__ == "__main__":
    run()
