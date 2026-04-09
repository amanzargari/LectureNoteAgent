FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY src ./src
COPY .env ./.env

ENV PYTHONPATH=/app/src
EXPOSE 8501

CMD ["streamlit", "run", "src/lecture_note_agent/ui.py", "--server.address=0.0.0.0", "--server.port=8501"]
