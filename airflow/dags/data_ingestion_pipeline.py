from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.postgres.hooks.postgres import PostgresHook
from datetime import datetime
from sqlalchemy import Text, Date, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, ARRAY, BIGINT, DOUBLE_PRECISION
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


def _read_latest_parquet(table_name: str) -> tuple[pq.ParquetFile, str]:
    """Возвращает PyArrow ParquetFile (для батч-чтения) и имя файла"""
    s3_hook = S3Hook(aws_conn_id=MINIO_CONN_ID)
    prefix = f"{table_name}/"

    keys = s3_hook.list_keys(bucket_name=BUCKET_NAME, prefix=prefix)
    if not keys:
        raise FileNotFoundError(f"Нет файлов по пути s3://{BUCKET_NAME}/{prefix}")

    s3_client = s3_hook.get_conn()
    objects = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)
    latest_key = max(objects["Contents"], key=lambda obj: obj["LastModified"])["Key"]
    print(f"📂 Читаем файл: s3://{BUCKET_NAME}/{latest_key}")

    s3_obj = s3_hook.get_key(key=latest_key, bucket_name=BUCKET_NAME)
    buffer = io.BytesIO(s3_obj.get()["Body"].read())

    file_name = os.path.basename(latest_key)
    parquet_file = pq.ParquetFile(buffer)
    return parquet_file, file_name


def _is_already_loaded(table_name: str, file_name: str) -> bool:
    """Проверяет, есть ли уже строки с этим source_file в таблице"""
    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    result = pg_hook.get_first(
        f"SELECT 1 FROM {SCHEMA_NAME}.{table_name} WHERE source_file = %s LIMIT 1",
        parameters=(file_name,)
    )
    return result is not None


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
    transform_fn — опциональная функция обработки столбцов: принимает df, возвращает df.
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

        df.to_sql(
            table_name,
            con=engine,
            schema=SCHEMA_NAME,
            if_exists="append",
            index=False,
            dtype=dtype,
        )

        total_rows += len(df)
        print(f"  📦 батч {i + 1}: загружено {len(df)} строк (итого {total_rows})")

    print(f"✅ {table_name}: всего загружено {total_rows} строк из файла '{file_name}'")


def load_category(**context):
    parquet_file, file_name = _read_latest_parquet("category")

    if _is_already_loaded("category", file_name):
        print(f"⏭️ category: файл '{file_name}' уже загружен, пропускаем")
        return

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
    parquet_file, file_name = _read_latest_parquet("items")

    if _is_already_loaded("items", file_name):
        print(f"⏭️ items: файл '{file_name}' уже загружен, пропускаем")
        return

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
            "catalogid": BIGINT,
            "variant_id": BIGINT,
            "model_id": BIGINT,
        },
        transform_fn=transform,
    )


def load_orders(**context):
    parquet_file, file_name = _read_latest_parquet("orders")

    if _is_already_loaded("orders", file_name):
        print(f"⏭️ orders: файл '{file_name}' уже загружен, пропускаем")
        return

    def transform(df):
        # TODO: обработка столбцов
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
    parquet_file, file_name = _read_latest_parquet("tracker")

    if _is_already_loaded("tracker", file_name):
        print(f"⏭️ tracker: файл '{file_name}' уже загружен, пропускаем")
        return

    def transform(df):
        # TODO: обработка столбцов
        return df

    _load_to_postgres(parquet_file, "tracker", file_name,
        dtype={
            "event_id": Text,
            "item_id": BIGINT,
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
    tags=["recsys", "etl"],
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
