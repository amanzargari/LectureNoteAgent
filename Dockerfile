FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# WeasyPrint system dependencies (Pango, Cairo, GDK-Pixbuf, fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY src ./src
RUN pip install --no-deps -e .

ENV PYTHONPATH=/app/src

COPY wsgi.py ./

EXPOSE 8000

# 1 worker keeps in-memory state (progress tracking, cancel flags) consistent.
# 4 threads handle concurrent HTTP requests within that worker.
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:8000", \
     "--timeout", "600", "--keep-alive", "5", "wsgi:app"]
