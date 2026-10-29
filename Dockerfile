# Decision service + web app.
#   docker build -t upi-stepup .
#   docker run --rm -p 8000:8000 upi-stepup        then open http://localhost:8000/
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UPI_CONFIG=/app/configs/default.yaml \
    UPI_DECISION_LOG=/app/data/decisions.jsonl

WORKDIR /app

# Dependencies first so code edits do not invalidate this layer.
COPY pyproject.toml README.md ./
RUN mkdir -p common sim policy baselines eval service tools \
    && pip install . \
    && pip uninstall -y upi-stepup

COPY common ./common
COPY sim ./sim
COPY policy ./policy
COPY baselines ./baselines
COPY eval ./eval
COPY service ./service
COPY tools ./tools
COPY configs ./configs

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

CMD ["uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8000"]
