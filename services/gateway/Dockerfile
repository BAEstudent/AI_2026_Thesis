FROM python:3.12-slim AS builder

WORKDIR /app

ADD https://github.com/astral-sh/uv/releases/download/0.5.24/uv-x86_64-unknown-linux-gnu.tar.gz /tmp/uv.tar.gz
RUN tar -xzf /tmp/uv.tar.gz -C /usr/local/bin/ --strip-components=1 && \
    rm /tmp/uv.tar.gz && \
    chmod +x /usr/local/bin/uv

COPY pyproject.toml uv.lock ./


RUN uv venv /app/.venv && \
    uv sync --frozen --no-default-groups --group app --python /app/.venv/bin/python

FROM python:3.12-slim

WORKDIR /app

ADD https://github.com/astral-sh/uv/releases/download/0.5.24/uv-x86_64-unknown-linux-gnu.tar.gz /tmp/uv.tar.gz
RUN tar -xzf /tmp/uv.tar.gz -C /usr/local/bin/ --strip-components=1 && \
    rm /tmp/uv.tar.gz && \
    chmod +x /usr/local/bin/uv

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"
ENV UV_PROJECT_ENVIRONMENT="/app/.venv"

COPY ./app /app/app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]