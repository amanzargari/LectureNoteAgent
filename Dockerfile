FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY src ./src
RUN pip install --no-deps -e .

ENV PYTHONPATH=/app/src
EXPOSE 5000

CMD ["slideagent-web", "--host", "0.0.0.0", "--port", "5000", "--data-dir", "/app/data"]
