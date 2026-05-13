from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.postgres.hooks.postgres import PostgresHook
from datetime import datetime
from sqlalchemy import Text, Date, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, ARRAY, BIGINT, DOUBLE_PRECISION
from sqlalchemy import create_engine
import pandas as pd
import pyarrow.parquet as pq
import json
import os
import io

MINIO_CONN_ID = "minio_conn"
POSTGRES_CONN_ID = "seller_conn"

BUCKET_NAME = os.getenv("DATA_BUCKET_NAME")
SCHEMA_NAME = os.getenv("SELLER_SCHEMA")

default_args = {
    "owner": "airflow",
    "start_date": datetime(2026, 1, 1),
    "retries": 0,
}


def _get_loaded_files(table_name):
    """Возвращает множество уже загруженных source_file для таблицы."""
    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    records = pg_hook.get_records(
        f"SELECT DISTINCT source_file FROM {SCHEMA_NAME}.{table_name} WHERE source_file IS NOT NULL"
    )
    return {r[0] for r in records}


def _get_pending_files(table_name):
    """
    Возвращает список (s3_key, file_name) файлов, которые ещё не загружены в Postgres.
    Отсортированы по времени модификации (от старых к новым).
    """
    s3_hook = S3Hook(aws_conn_id=MINIO_CONN_ID)
    prefix = f"{table_name}/"

    s3_client = s3_hook.get_conn()
    response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)

    if "Contents" not in response or not response["Contents"]:
        return []

    loaded = _get_loaded_files(table_name)

    pending = []
    for obj in response["Contents"]:
        key = obj["Key"]
        file_name = os.path.basename(key)
        if file_name and file_name not in loaded:
            pending.append((key, file_name, obj["LastModified"]))

    pending.sort(key=lambda x: x[2])
    return [(key, name) for key, name, _ in pending]


def _read_parquet_by_key(key):
    """Читает конкретный parquet-файл из S3 и возвращает (ParquetFile, file_name)."""
    s3_hook = S3Hook(aws_conn_id=MINIO_CONN_ID)
    file_name = os.path.basename(key)

    print(f"📂 Читаем файл: s3://{BUCKET_NAME}/{key}")
    s3_obj = s3_hook.get_key(key=key, bucket_name=BUCKET_NAME)
    buffer = io.BytesIO(s3_obj.get()["Body"].read())
    return pq.ParquetFile(buffer), file_name


def _load_to_postgres(
    parquet_file: pq.ParquetFile,
    table_name: str,
    file_name: str,
    dtype: dict,
    transform_fn=None,
    batch_size: int = 50_000,
):
    """
    Читает parquet батчами по batch_size строк и льёт в Postgres.
    """
    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    engine = pg_hook.get_sqlalchemy_engine()

    dtype = {**dtype, "source_file": Text, "loaded_at": TIMESTAMP}
    loaded_at = datetime.now()
    total_rows = 0

    for i, batch in enumerate(parquet_file.iter_batches(batch_size=batch_size)):
        df = batch.to_pandas()

        if transform_fn is not None:
            df = transform_fn(df)

        df["source_file"] = file_name
        df["loaded_at"] = loaded_at

        print("df.head(1):", df.head(1))

        with engine.connect() as conn:
            df.to_sql(
                table_name,
                con=conn,
                schema=SCHEMA_NAME,
                if_exists="append",
                index=False,
                dtype=dtype,
            )

        total_rows += len(df)
        print(f"  📦 батч {i + 1}: загружено {len(df)} строк (итого {total_rows})")

    print(f"✅ {table_name}: всего загружено {total_rows} строк из файла '{file_name}'")


def load_category(**context):
    pending = _get_pending_files("category")

    if not pending:
        print("⏭️ category: нет новых файлов для загрузки")
        return

    for key, file_name in pending:
        parquet_file, _ = _read_parquet_by_key(key)

        def transform(df):
            df['catalogid'] = df['catalogid'].astype(str)
            df['catalogpath'] = df['catalogpath'].apply(lambda x: json.dumps(x.tolist(), ensure_ascii=False))
            df['ids'] = df['ids'].apply(lambda x: x.tolist())
            return df

        _load_to_postgres(parquet_file, "category", file_name,
            dtype={
                "catalogid": Text,
                "catalogpath": JSONB,
                "ids": ARRAY(BIGINT),
            },
            transform_fn=transform,
        )


def load_items(**context):
    pending = _get_pending_files("items")

    if not pending:
        print("⏭️ items: нет новых файлов для загрузки")
        return

    for key, file_name in pending:
        parquet_file, _ = _read_parquet_by_key(key)

        def transform(df):
            df['attributes'] = df['attributes'].apply(lambda x: json.dumps(x.tolist(), ensure_ascii=False))
            df['fclip_embed'] = df['fclip_embed'].apply(lambda x: x.tolist())
            return df

        _load_to_postgres(parquet_file, "items", file_name,
            dtype={
                "item_id": Text,
                "itemname": Text,
                "attributes": JSONB,
                "fclip_embed": ARRAY(DOUBLE_PRECISION),
                "catalogid": Text,
                "variant_id": BIGINT,
                "model_id": BIGINT,
            },
            transform_fn=transform,
        )


def load_orders(**context):
    pending = _get_pending_files("orders")

    if not pending:
        print("⏭️ orders: нет новых файлов для загрузки")
        return

    for key, file_name in pending:
        parquet_file, _ = _read_parquet_by_key(key)

        def transform(df):
            # TODO: обработка столбцов при необходимости
            return df

        _load_to_postgres(parquet_file, "orders", file_name,
            dtype={
                "order_id": Text,
                "item_id": Text,
                "user_id": BIGINT,
                "created_timestamp": TIMESTAMP,
                "last_status": Text,
                "last_status_timestamp": TIMESTAMP,
                "created_date": Date,
            },
            transform_fn=transform,
        )


def load_tracker(**context):
    pending = _get_pending_files("tracker")

    if not pending:
        print("⏭️ tracker: нет новых файлов для загрузки")
        return

    for key, file_name in pending:
        parquet_file, _ = _read_parquet_by_key(key)

        def transform(df):
            # TODO: обработка столбцов при необходимости
            return df

        _load_to_postgres(parquet_file, "tracker", file_name,
            dtype={
                "event_id": Text,
                "item_id": Text,
                "user_id": BIGINT,
                "timestamp": TIMESTAMP,
                "action_type": Text,
                "action_widget": Text,
                "date": Date,
            },
            transform_fn=transform,
        )


with DAG(
    dag_id="data_ingestion_pipeline",
    default_args=default_args,
    catchup=False,
    tags=["etl"],
) as dag:

    load_category_task = PythonOperator(
        task_id="load_category_to_postgres",
        python_callable=load_category,
    )

    load_items_task = PythonOperator(
        task_id="load_items_to_postgres",
        python_callable=load_items,
    )

    load_orders_task = PythonOperator(
        task_id="load_orders_to_postgres",
        python_callable=load_orders,
    )

    load_tracker_task = PythonOperator(
        task_id="load_tracker_to_postgres",
        python_callable=load_tracker,
    )

    load_category_task >> load_items_task >> load_orders_task >> load_tracker_task
