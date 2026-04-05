FROM apache/airflow:2.10.3-python3.12

USER root

ADD https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz /tmp/uv.tar.gz
RUN tar -xzf /tmp/uv.tar.gz -C /usr/local/bin/ --strip-components=1 uv-x86_64-unknown-linux-gnu/uv && \
    rm /tmp/uv.tar.gz && \
    chmod +x /usr/local/bin/uv

USER airflow

COPY ./pyproject.toml ./uv.lock* ./

RUN uv export --format requirements-txt > requirements.txt

RUN uv pip install -r requirements.txt

RUN rm -rf /home/airflow/.cache/uv
