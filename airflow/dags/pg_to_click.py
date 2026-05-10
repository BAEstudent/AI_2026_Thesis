from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow_clickhouse_plugin.hooks.clickhouse import ClickHouseHook
from datetime import datetime, timezone
import pandas as pd
import json
import time
import ast


POSTGRES_CONN_ID = "seller_conn"
CLICKHOUSE_CONN_ID = "clickhouse_conn"

SCHEMA_NAME = "seller"
CH_DATABASE = "analytics"

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
    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    result = pg_hook.get_first(
        f"SELECT source_file FROM {SCHEMA_NAME}.{table_name} ORDER BY loaded_at DESC LIMIT 1"
    )
    return result[0] if result else None


def _truncate_ch_table(table_name: str):
    ch_hook = ClickHouseHook(clickhouse_conn_id=CLICKHOUSE_CONN_ID)
    ch_hook.execute(f"TRUNCATE TABLE {CH_DATABASE}.{table_name}")


def _insert_df_to_ch(df: pd.DataFrame, table_name: str):
    if df.empty:
        print(f"⚠️ {table_name}: пустой датафрейм, вставка пропущена")
        return
    ch_hook = ClickHouseHook(clickhouse_conn_id=CLICKHOUSE_CONN_ID)
    ch_hook.execute(
        f"INSERT INTO {CH_DATABASE}.{table_name} VALUES",
        df.values.tolist()
    )
    print(f"✅ {table_name}: вставлено {len(df)} строк")


def _get_processed_sources(table_name: str) -> set[str]:
    ch_hook = ClickHouseHook(clickhouse_conn_id=CLICKHOUSE_CONN_ID)
    result = ch_hook.execute(
        f"SELECT source_file FROM {CH_DATABASE}.etl_source_log WHERE table_name = '{table_name}'"
    )
    return {row[0] for row in result}


def _mark_sources_processed(table_name: str, source_files: list[str]):
    if not source_files:
        return
    ch_hook = ClickHouseHook(clickhouse_conn_id=CLICKHOUSE_CONN_ID)
    rows = [[table_name, sf] for sf in source_files]
    ch_hook.execute(
        f"INSERT INTO {CH_DATABASE}.etl_source_log (table_name, source_file) VALUES",
        rows
    )



def sync_items(**context):
    pg_latest = _get_pg_last_source_file("items")
    ch_latest = _get_last_loaded_file("items")

    if pg_latest and pg_latest == ch_latest:
        print(f"⏭️ items: файл '{pg_latest}' уже актуален, пропускаем")
        return

    print(f"🔄 items: Postgres='{pg_latest}', ClickHouse='{ch_latest}' → перезагружаем")
    _truncate_ch_table("items")

    query = f"""
        SELECT
            id,
            item_id,
            itemname,
            attributes::text    AS attributes,
            catalogid,
            variant_id,
            model_id,
            source_file,
            loaded_at
        FROM {SCHEMA_NAME}.items
    """

    BATCH_SIZE = 100_000
    total_rows = 0

    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)

    with pg_hook.get_conn() as conn:
        with conn.cursor(name="items_cursor") as cursor:
            cursor.itersize = BATCH_SIZE
            cursor.execute(query)
            columns = None

            while True:
                rows = cursor.fetchmany(BATCH_SIZE)
                if not rows:
                    break

                if columns is None:
                    columns = [desc[0] for desc in cursor.description]

                df = pd.DataFrame(rows, columns=columns)

                df["attributes"] = df["attributes"].fillna("{}").astype(str)
                
                df["catalogid"] = pd.to_numeric(df["catalogid"], errors="coerce").fillna(0).astype("int64")
                
                df["variant_id"] = df["variant_id"].fillna(0).astype("int64")
                df["model_id"] = df["model_id"].fillna(0).astype("int64")
                df["id"] = df["id"].astype("int64")
                df["item_id"] = df["item_id"].astype(str)
                df["itemname"] = df["itemname"].astype(str)
                df["source_file"] = df["source_file"].astype(str)
                df["loaded_at"] = pd.to_datetime(df["loaded_at"])

                df = df[[
                    "id", "item_id", "itemname", "attributes",
                    "catalogid", "variant_id", "model_id", "source_file", "loaded_at"
                ]]

                _insert_df_to_ch(df, "items")
                total_rows += len(df)
                print(f"  ↳ вставлено {total_rows} строк...")

    print(f"✅ items: перелито {total_rows} строк")


def sync_order_facts(**context):
    pg_latest = _get_pg_last_source_file("orders")
    ch_latest = _get_last_loaded_file("order_facts")

    if pg_latest and pg_latest == ch_latest:
        print(f"⏭️ order_facts: файл '{pg_latest}' уже актуален, пропускаем")
        return

    print(f"🔄 order_facts: Postgres='{pg_latest}', ClickHouse='{ch_latest}' → перезагружаем")
    _truncate_ch_table("order_facts")

    query = f"""
        SELECT
            o.id                    AS order_id,
            o.item_id               AS item_id,
            o.user_id               AS user_id,
            o.created_timestamp     AS created_timestamp,
            o.last_status           AS last_status,
            o.last_status_timestamp AS last_status_timestamp,
            o.created_date          AS created_date,
            o.source_file           AS source_file,
            i.catalogid             AS catalog_id_raw,
            c.catalogpath::text     AS catalog_path_raw
        FROM {SCHEMA_NAME}.orders o
        LEFT JOIN {SCHEMA_NAME}.items i ON o.item_id = i.item_id
        LEFT JOIN {SCHEMA_NAME}.category c ON i.catalogid = c.catalogid
        ORDER BY o.source_file, o.id
    """

    BATCH_SIZE = 50_000
    epoch = pd.Timestamp("1970-01-01")
    total_rows = 0

    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)

    with pg_hook.get_conn() as conn:
        with conn.cursor(name="order_facts_cursor") as cursor:
            cursor.itersize = BATCH_SIZE
            cursor.execute(query)
            columns = None

            while True:
                rows = cursor.fetchmany(BATCH_SIZE)
                if not rows:
                    break

                if columns is None:
                    columns = [desc[0] for desc in cursor.description]

                df = pd.DataFrame(rows, columns=columns)

                df["order_id"]   = df["order_id"].astype("int64")
                df["user_id"]    = df["user_id"].fillna(0).astype("int64")
                df["item_id"]    = df["item_id"].fillna("").astype(str)
                df["last_status"] = df["last_status"].fillna("").astype(str)
                df["source_file"] = df["source_file"].astype(str)

                df["created_date"] = pd.to_datetime(df["created_date"]).dt.date
                df["created_hour"] = pd.to_datetime(df["created_timestamp"]).fillna(epoch).dt.floor("H")
                df["status_ts"]    = pd.to_datetime(df["last_status_timestamp"]).fillna(epoch)

                df["catalog_path"] = df["catalog_path_raw"].apply(lambda x: ast.literal_eval(ast.literal_eval(x)))
                df["category_level1"] = df["catalog_path"].apply(lambda x: x[-2]["name"])
                df["catalog_path"] = df["catalog_path"].astype(str)
                df["catalog_id"]      = df["catalog_id_raw"].fillna("").astype(str)

                df["is_canceled"] = (df["last_status"] == "canceled_orders").astype("uint8")
                df["loaded_at"]   = datetime.now(timezone.utc)

                df = df[[
                    "order_id", "item_id", "user_id", "catalog_id", "catalog_path",
                    "category_level1", "created_date", "created_hour", "last_status",
                    "status_ts", "is_canceled", "source_file", "loaded_at"
                ]]

                _insert_df_to_ch(df, "order_facts")
                total_rows += len(df)
                print(f"  ↳ вставлено {total_rows} строк...")

    print(f"✅ order_facts: перелито {total_rows} строк")


def sync_item_daily_stats(**context):
    import logging
    import gc
    logging.getLogger("airflow_clickhouse_plugin.hooks.clickhouse").setLevel(logging.WARNING)

    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    ch_hook = ClickHouseHook(clickhouse_conn_id=CLICKHOUSE_CONN_ID)

    # ── 1. Определяем новые файлы ─────────────────────────────────────
    processed_orders = _get_processed_sources("orders")
    processed_tracker = _get_processed_sources("tracker")

    orders_files = {
        r[0] for r in pg_hook.get_records(
            f"SELECT DISTINCT source_file FROM {SCHEMA_NAME}.orders"
        )
    }
    tracker_files = {
        r[0] for r in pg_hook.get_records(
            f"SELECT DISTINCT source_file FROM {SCHEMA_NAME}.tracker"
        )
    }

    new_orders_files = sorted(orders_files - processed_orders)
    new_tracker_files = sorted(tracker_files - processed_tracker)

    if not new_orders_files and not new_tracker_files:
        print("⏭️ item_daily_stats: нет новых source_file, пропускаем")
        return

    print(f"🆕 Новые файлы: orders={new_orders_files}, tracker={new_tracker_files}")

    # ── 2. Собираем затронутые даты ───────────────────────────────────
    affected_dates = set()

    if new_orders_files:
        rows = pg_hook.get_records(
            f"SELECT DISTINCT created_date FROM {SCHEMA_NAME}.orders WHERE source_file = ANY(%s)",
            parameters=(new_orders_files,)
        )
        affected_dates.update({r[0] for r in rows})

    if new_tracker_files:
        rows = pg_hook.get_records(
            f"SELECT DISTINCT date FROM {SCHEMA_NAME}.tracker WHERE source_file = ANY(%s)",
            parameters=(new_tracker_files,)
        )
        affected_dates.update({r[0] for r in rows})

    if not affected_dates:
        print("⚠️ Новые файлы есть, но даты не найдены")
        return

    date_list = sorted(affected_dates)
    print(f"📅 Пересчитываем даты: {date_list}")

    # ── 3. Удаляем партиции в ClickHouse ──────────────────────────────
    affected_months = {d.strftime("%Y%m") for d in date_list}
    for month in affected_months:
        print(f"🗑️ DROP PARTITION ID '{month}' в item_daily_stats")
        ch_hook.execute(
            f"ALTER TABLE {CH_DATABASE}.item_daily_stats DROP PARTITION ID '{month}'"
        )

    # ── 4. Запрос, который делает merge в Postgres, а не в pandas ─────
    # Ключевое отличие: FULL OUTER JOIN на уровне БД, потоковая выдача
    # через server-side cursor, и вставка в CH чанками без накопления.
    query = f"""
    WITH orders_agg AS (
        SELECT
            o.created_date::date   AS date,
            o.item_id              AS item_id,
            i.catalogid            AS catalog_id,
            c.catalogpath::text    AS catalog_path_raw,
            COUNT(*)::int          AS orders_cnt,
            COUNT(DISTINCT o.user_id)::int AS users_cnt
        FROM {SCHEMA_NAME}.orders o
        LEFT JOIN {SCHEMA_NAME}.items i ON o.item_id = i.item_id
        LEFT JOIN {SCHEMA_NAME}.category c ON i.catalogid = c.catalogid
        WHERE o.created_date = %s
        GROUP BY o.created_date::date, o.item_id, i.catalogid, c.catalogpath
    ),
    tracker_agg AS (
        SELECT
            t.date::date           AS date,
            t.item_id              AS item_id,
            i.catalogid            AS catalog_id,
            c.catalogpath::text    AS catalog_path_raw,
            COUNT(*)::int          AS views_cnt
        FROM {SCHEMA_NAME}.tracker t
        LEFT JOIN {SCHEMA_NAME}.items i ON t.item_id = i.item_id
        LEFT JOIN {SCHEMA_NAME}.category c ON i.catalogid = c.catalogid
        WHERE t.date = %s AND t.action_type = 'page_view'
        GROUP BY t.date::date, t.item_id, i.catalogid, c.catalogpath
    )
    SELECT
        COALESCE(o.date, t.date) AS date,
        COALESCE(o.item_id, t.item_id) AS item_id,
        COALESCE(o.catalog_id, t.catalog_id) AS catalog_id,
        COALESCE(o.catalog_path_raw, t.catalog_path_raw) AS catalog_path_raw,
        COALESCE(o.orders_cnt, 0) AS orders_cnt,
        COALESCE(o.users_cnt, 0) AS users_cnt,
        COALESCE(t.views_cnt, 0) AS views_cnt
    FROM orders_agg o
    FULL OUTER JOIN tracker_agg t
        ON o.date = t.date
        AND o.item_id = t.item_id
        AND COALESCE(o.catalog_id, '') = COALESCE(t.catalog_id, '')
        AND COALESCE(o.catalog_path_raw, '') = COALESCE(t.catalog_path_raw, '')
    """

    # ── 5. Обрабатываем дату за датой ─────────────────────────────────
    total_inserted = 0
    CHUNK_SIZE = 50_000  # сколько строк на одну вставку в ClickHouse

    for current_date in date_list:
        print(f"📆 Обработка даты {current_date}...")
        t0 = time.time()

        conn = pg_hook.get_conn()
        cursor = conn.cursor(
            name=f"stats_{current_date}_{datetime.now().timestamp():.0f}"
        )
        cursor.itersize = 100_000
        cursor.execute(query, (current_date, current_date))

        date_inserted = 0
        buffer = []

        while True:
            rows = cursor.fetchmany(100_000)
            if not rows:
                break

            # Трансформируем сразу в плоский список для ClickHouse,
            # без создания pandas DataFrame
            for row in rows:
                date, item_id, catalog_id, catalog_path_raw, orders_cnt, users_cnt, views_cnt = row
                category_path = ast.literal_eval(catalog_path_raw)

                buffer.append([
                    date,
                    str(item_id) if item_id is not None else "",
                    str(catalog_id) if catalog_id is not None else "",
                    category_path,
                    int(orders_cnt or 0),
                    int(users_cnt or 0),
                    int(views_cnt or 0),
                ])

                if len(buffer) >= CHUNK_SIZE:
                    ch_hook.execute(
                        f"INSERT INTO {CH_DATABASE}.item_daily_stats VALUES",
                        buffer
                    )
                    date_inserted += len(buffer)
                    total_inserted += len(buffer)
                    buffer = []
                    gc.collect()

        # Дожигаем остаток
        if buffer:
            ch_hook.execute(
                f"INSERT INTO {CH_DATABASE}.item_daily_stats VALUES",
                buffer
            )
            date_inserted += len(buffer)
            total_inserted += len(buffer)
            buffer = []

        cursor.close()
        conn.close()
        gc.collect()

        print(
            f"   ✅ {current_date}: вставлено {date_inserted:,} строк "
            f"за {time.time() - t0:.1f}s"
        )

    # ── 6. Помечаем файлы обработанными ───────────────────────────────
    _mark_sources_processed("orders", new_orders_files)
    _mark_sources_processed("tracker", new_tracker_files)

    print(f"✅ item_daily_stats: всего вставлено {total_inserted:,} строк за даты {date_list}")


def sync_category(**context):
    """
    Loads the entire category table from Postgres to ClickHouse.
    Compares the latest source_file between the two systems and reloads
    only when a newer file exists in Postgres.
    """
    pg_latest = _get_pg_last_source_file("category")
    ch_latest = _get_last_loaded_file("category")   # expects column source_file exists in CH table

    if pg_latest and pg_latest == ch_latest:
        print(f"⏭️ category: файл '{pg_latest}' уже актуален, пропускаем")
        return

    print(f"🔄 category: Postgres='{pg_latest}', ClickHouse='{ch_latest}' → перезагружаем")
    _truncate_ch_table("category")

    query = f"""
        SELECT
            catalogid,
            catalogpath::text   AS catalogpath,   -- convert JSONB to text
            ids::text           AS ids,           -- PG array to text, e.g. '{{1,2,3}}'
            source_file,
            loaded_at
        FROM {SCHEMA_NAME}.category
    """

    pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    df = pg_hook.get_pandas_df(query)

    def parse_pg_array(array_str):
        if pd.isna(array_str) or array_str is None:
            return []
        cleaned = array_str.strip('{}')
        if cleaned == '':
            return []
        return [int(x) for x in cleaned.split(',')]

    df["ids"] = df["ids"].apply(parse_pg_array)
    df["catalogpath"] = df["catalogpath"].apply(lambda x: ast.literal_eval(x))
    df["category_level1"] = df["catalogpath"].apply(lambda x: ast.literal_eval(x)[-2]["name"])
    df["catalogid"] = df["catalogid"].fillna("").astype(str)

    _insert_df_to_ch(df[['catalogid', 'catalogpath', 'ids', 'category_level1', 'source_file', 'loaded_at']], "category")
    print(f"✅ category: перелито {len(df)} строк")


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

    sync_order_facts_task = PythonOperator(
        task_id="sync_order_facts_to_clickhouse",
        python_callable=sync_order_facts,
    )

    sync_item_daily_stats_task = PythonOperator(
        task_id="sync_item_daily_stats_to_clickhouse",
        python_callable=sync_item_daily_stats,
    )

    sync_category_task = PythonOperator(
        task_id="sync_category_to_clickhouse",
        python_callable=sync_category,
    )

    sync_items_task >> sync_order_facts_task >> sync_item_daily_stats_task >> sync_category_task