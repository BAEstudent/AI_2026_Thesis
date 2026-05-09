FROM python:3.12-slim AS builder

WORKDIR /app

ADD https://github.com/astral-sh/uv/releases/download/0.5.24/uv-x86_64-unknown-linux-gnu.tar.gz /tmp/uv.tar.gz
RUN tar -xzf /tmp/uv.tar.gz -C /usr/local/bin/ --strip-components=1 && \
    rm /tmp/uv.tar.gz && \
    chmod +x /usr/local/bin/uv

COPY pyproject.toml uv.lock ./

ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN uv venv "$VIRTUAL_ENV" && uv sync --frozen --no-default-groups --group anomaly_detection

RUN find "$VIRTUAL_ENV" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY ./services/anomaly_detection/app /app/app

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]