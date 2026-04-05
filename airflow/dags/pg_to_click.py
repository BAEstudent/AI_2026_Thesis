from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow_clickhouse_plugin.hooks.clickhouse import ClickHouseHook
from datetime import datetime
import pandas as pd
import json

POSTGRES_CONN_ID = "seller_conn"
CLICKHOUSE_CONN_ID = "clickhouse_conn"

SCHEMA_NAME = "seller"
CH_DATABASE = "analytics"
BATCH_SIZE = 50_000

default_args = {
    "owner": "airflow",
    "start_date": datetime(2026, 1, 1),
    "retries": 0,
}


def _get_last_loaded_file(table_name: str) -> str | None:
    ch_hook = ClickHouseHook(clickhouse_conn_id=CLICKHOUSE_CONN_ID)
    result = ch_hook.execute(
        f"SELECT source_file FROM {CH_DATABASE}.{table_name} ORDER BY loaded_at DESC LIMIT 1"
    )
    return result[0][0] if result else None


def _get_pg_last_source_file(table_name: str) -> str | None:
    """Возвращает последний source_file в Postgres (т.е. последнюю загруженную партию)"""
    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    result = pg_hook.get_first(
        f"SELECT source_file FROM {SCHEMA_NAME}.{table_name} ORDER BY loaded_at DESC LIMIT 1"
    )
    return result[0] if result else None


def _load_pg_to_clickhouse(
        table_name: str,
        query: str,
        transform_fn=None,
        batch_size: int = BATCH_SIZE,
        ):
    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    ch_hook = ClickHouseHook(clickhouse_conn_id=CLICKHOUSE_CONN_ID)

    conn = pg_hook.get_conn()
    cursor = conn.cursor(name=f"fetch_{table_name}_{datetime.now().timestamp():.0f}")
    cursor.itersize = batch_size
    cursor.execute(query)

    total_rows = 0
    batch_num = 0
    columns = None

    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break

        if columns is None:
            columns = [desc[0] for desc in cursor.description]

        df = pd.DataFrame(rows, columns=columns)

        if transform_fn is not None:
            df = transform_fn(df)

        ch_hook.execute(
            f"INSERT INTO {CH_DATABASE}.{table_name} VALUES",
            df.values.tolist()
        )

        total_rows += len(df)
        batch_num += 1
        print(f"  📦 батч {batch_num}: {len(df)} строк (итого {total_rows})")

    cursor.close()
    conn.close()
    print(f"✅ {table_name}: перелито {total_rows} строк в ClickHouse")


def sync_items(**context):
    pg_latest = _get_pg_last_source_file("items")
    ch_latest = _get_last_loaded_file("items")

    if pg_latest and pg_latest == ch_latest:
        print(f"⏭️ items: файл '{pg_latest}' уже в ClickHouse, пропускаем")
        return

    print(f"🔄 items: Postgres='{pg_latest}', ClickHouse='{ch_latest}' → синхронизируем")

    query = f"""
        SELECT
            item_id,
            itemname,
            attributes::text    AS attributes,   -- JSONB → text для ClickHouse String
            fclip_embed::text   AS fclip_embed,  -- float[] → обработаем в transform
            catalogid,
            variant_id,
            model_id,
            source_file,
            loaded_at
        FROM {SCHEMA_NAME}.items
    """

    def transform(df: pd.DataFrame) -> pd.DataFrame:
        df["fclip_embed"] = df["fclip_embed"].apply(
            lambda x: [float(v) for v in x.strip("{}").split(",")] if x else []
        )
        df["attributes"] = df["attributes"].fillna("{}")
        df["catalogid"] = df["catalogid"].fillna(0).astype("int64")
        df["variant_id"] = df["variant_id"].fillna(0).astype("int64")
        df["model_id"] = df["model_id"].fillna(0).astype("int64")
        return df

    _load_pg_to_clickhouse("items", query, transform_fn=transform)


def sync_orders(**context):
    pg_latest = _get_pg_last_source_file("orders")
    ch_latest = _get_last_loaded_file("orders")

    if pg_latest and pg_latest == ch_latest:
        print(f"⏭️ orders: файл '{pg_latest}' уже в ClickHouse, пропускаем")
        return

    print(f"🔄 orders: Postgres='{pg_latest}', ClickHouse='{ch_latest}' → синхронизируем")

    query = f"""
        SELECT
            id,
            item_id,
            user_id,
            created_timestamp,
            last_status,
            last_status_timestamp,
            created_date,
            source_file,
            loaded_at
        FROM {SCHEMA_NAME}.orders
    """

    def transform(df: pd.DataFrame) -> pd.DataFrame:
        df["id"] = df["id"]
        df["user_id"] = df["user_id"].fillna(0).astype("int64")
        df["last_status"] = df["last_status"].fillna("")
        epoch = pd.Timestamp("1970-01-01")
        df["created_timestamp"] = df["created_timestamp"].fillna(epoch)
        df["last_status_timestamp"] = df["last_status_timestamp"].fillna(epoch)
        return df

    _load_pg_to_clickhouse("orders", query, transform_fn=transform)


with DAG(
    dag_id="postgres_to_clickhouse_sync",
    default_args=default_args,
    catchup=False,
    tags=["recsys", "etl", "clickhouse"],
) as dag:

    sync_items_task = PythonOperator(
        task_id="sync_items_to_clickhouse",
        python_callable=sync_items,
    )

    sync_orders_task = PythonOperator(
        task_id="sync_orders_to_clickhouse",
        python_callable=sync_orders,
    )

    sync_items_task >> sync_orders_task