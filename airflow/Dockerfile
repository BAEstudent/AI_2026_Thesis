FROM apache/airflow:3.2.1-python3.12 AS builder

USER root
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

USER airflow
RUN pip install --no-cache-dir uv

WORKDIR /opt/airflow

ENV UV_PROJECT_ENVIRONMENT="/opt/airflow/.venv-custom"
ENV UV_NO_CACHE=1

COPY --chown=airflow:airflow ./pyproject.toml ./uv.lock ./
RUN uv sync --frozen --only-group airflow --no-compile

FROM apache/airflow:3.2.1-python3.12

USER airflow
WORKDIR /opt/airflow

COPY --from=builder --chown=airflow:airflow /opt/airflow/.venv-custom /opt/airflow/.venv-custom

ENV PATH="/opt/airflow/.venv-custom/bin:$PATH"

COPY --chown=airflow:airflow ./dags /opt/airflow/dags
COPY --chown=airflow:airflow ./plugins /opt/airflow/plugins
