# SlideAGENT — Full Lecture Note Generator

This project builds a **full AI agent** that takes:

- course slides (`.pdf`, `.pptx`, `.md`, `.txt`)
- class transcript (`.txt` / `.md`)

and generates a **comprehensive markdown lecture note** that:

- covers slide content + teacher speech
- keeps a clean, structured format
- highlights special mentions from instructor
- includes exact image references for later insertion
- extracts and preserves formulas
- validates coverage and repairs missing points automatically

## Features

- **Multi-source ingestion** for slides and transcripts
- **Model-only OCR for PDFs** (automatic whole-file + page fallback)
- **Coverage checklist generation** (atomic items with IDs)
- **Lecture-note drafting** with references like `[S3]`, `[T17]`
- **Strict validation pass** with JSON audit
- **Auto-repair loop** to fill dropped/missing content
- **Artifacts export** (`checklist.md`, `audit.json`, `source_bundle.json`)
- **Light Streamlit UI** for easy upload/run/download flow

## Project Structure

- `src/lecture_note_agent/io_utils.py` — parsing slides/transcript and source payload build
- `src/lecture_note_agent/prompts.py` — generation/audit/repair prompts
- `src/lecture_note_agent/agent.py` — orchestration pipeline + iterative validation
- `src/lecture_note_agent/cli.py` — command line interface
- `src/lecture_note_agent/ui.py` — lightweight Streamlit web UI
- `Dockerfile` + `docker-compose.yml` — one-command containerized run

## Setup

1. Install dependencies:

   `pip install -r requirements.txt`

2. Configure `.env`:

   - `OPENAI_API_KEY=your_openai_api_key_here`
   - `OPENAI_BASE_URL=https://openrouter.ai/api/v1`
   - `OPENAI_MODEL=openai/gpt-5.4-mini` (fallback)
   - `OPENAI_MODEL_OCR=openai/gpt-5.4-mini`
   - `OPENAI_MODEL_CHECKLIST=mistralai/mistral-small-2603`
   - `OPENAI_MODEL_DRAFT=mistralai/mistral-small-2603`
   - `OPENAI_MODEL_AUDIT=openai/gpt-5.4-mini`
   - `OPENAI_MODEL_REPAIR=openai/gpt-5.4-mini`
   - `MAX_REPAIR_LOOPS=3`
   - `MAX_MODEL_CALLS=6`
   - `MAX_OUTPUT_TOKENS=3500`

The app now uses an OpenAI-compatible client only. Keep credentials in `.env` only.

## Usage

Run from project root:

`python -m lecture_note_agent --course-name "Data Structures" --slides ./input/week1.pdf --transcript ./input/week1_transcript.txt --output ./output/week1_lecture_notes.md --artifacts-dir ./artifacts/week1`

Or after editable install (`pip install -e .`):

`slideagent --course-name "Data Structures" --slides ./input/week1.pdf --transcript ./input/week1_transcript.txt --output ./output/week1_lecture_notes.md --artifacts-dir ./artifacts/week1`

### PDF OCR strategy (automatic)

- `whole`: uploads the full PDF once and asks the model to return per-page JSON text.
- `page`: uploads one-page PDFs and extracts each page separately.
- `auto`: tries `whole` first, then falls back to `page` for weak/missing pages.

This strategy is always used for PDFs; no OCR toggles are required in UI/CLI/env.

### Multi-model by phase

SlideAGENT can route each phase to a different model:

- OCR phase → `OPENAI_MODEL_OCR`
- Checklist phase → `OPENAI_MODEL_CHECKLIST`
- Draft phase → `OPENAI_MODEL_DRAFT`
- Audit phase → `OPENAI_MODEL_AUDIT`
- Repair phase → `OPENAI_MODEL_REPAIR`

If a phase model is not provided, `OPENAI_MODEL` is used as fallback.

## Web UI

Run locally:

`streamlit run src/lecture_note_agent/ui.py`

Or with script (after `pip install -e .`):

`slideagent-ui`

## Docker / Compose

Build and run UI with Docker Compose:

`docker compose up --build`

Then open `http://localhost:8501`.

## Output Quality Contract

The generated markdown is designed to include:

1. Full lecture structure (headings/subheadings)
2. All concepts from slides and transcript
3. Special instructor instructions/reminders
4. Image placeholder section with exact refs from source
5. Formula sheet with exact formula text
6. Inline source references for traceability

Validation ensures high coverage, then repair loop attempts to fix any missing items before final output is written.

## Notes

- For best results, provide clean transcript text (timestamps/speaker names are supported).
- PDF image extraction depends on available image metadata in the PDF.
- PPTX image references use shape names from slides.
