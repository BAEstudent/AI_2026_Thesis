CREATE TABLE IF NOT EXISTS analytics.items
(
    id            Int64,
    item_id       String,
    itemname      String,
    attributes    String,
    fclip_embed   Array(Float64),
    catalogid     Int64,
    variant_id    Int64,
    model_id      Int64,
    source_file   String,
    loaded_at     DateTime
)
ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY (item_id)
SETTINGS index_granularity = 8192;


CREATE TABLE IF NOT EXISTS analytics.orders
(
    id                     Int64,
    item_id                String,
    user_id                Int64,
    created_timestamp      DateTime,
    last_status            String,
    last_status_timestamp  DateTime,
    created_date           Date,
    source_file            String,
    loaded_at              DateTime
)
ENGINE = ReplacingMergeTree(loaded_at)
PARTITION BY toYYYYMM(created_date)
ORDER BY (created_date, user_id, id)
SETTINGS index_granularity = 8192;


CREATE TABLE analytics.order_facts
(
    order_id          Int64,
    item_id           String,
    user_id           Int64,
    catalog_id        String,
    catalog_path      String,
    category_level1   String,
    created_date      Date,
    created_hour      DateTime,
    last_status       LowCardinality(String),
    status_ts         DateTime,
    is_canceled       UInt8,
    source_file       String,
    loaded_at         DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(created_date)
ORDER BY (created_date, catalog_id, last_status)
SETTINGS index_granularity = 8192;



CREATE TABLE analytics.item_daily_stats
(
    date          Date,
    item_id       String,
    catalog_id    String,
    category_path String,
    orders_cnt    UInt32,
    users_cnt     UInt32,
    views_cnt     UInt32
)
ENGINE = SummingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (date, catalog_id, item_id)
SETTINGS index_granularity = 8192;


CREATE TABLE IF NOT EXISTS analytics.etl_source_log
(
    table_name LowCardinality(String),
    source_file String,
    loaded_at  DateTime DEFAULT now()
)
ENGINE = MergeTree()
ORDER BY (table_name, source_file);


CREATE TABLE analytics.forecasts
(
    date              Date,
    item_id           String,
    category_level1   String,
    metric            LowCardinality(String),
    forecast          Float64,
    lower_80          Float64,
    upper_80          Float64,
    model_version     String,
    generated_at      DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (category_level1, item_id, metric, date);


CREATE TABLE IF NOT EXISTS analytics.category (
    catalogid    String,
    catalogpath  String,
    ids          Array(Int64),
    category_level1 String,
    source_file  String,
    loaded_at    DateTime
)
ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY (catalogid);


CREATE TABLE IF NOT EXISTS analytics.forecasts
(
    granularity  LowCardinality(String),
    freq         LowCardinality(String),
    series_id    String,
    model        LowCardinality(String),
    ds           Date,
    yhat         Float64,
    computed_at  DateTime
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (granularity, freq, series_id, model, ds);
 
 
CREATE TABLE IF NOT EXISTS analytics.forecast_metrics
(
    granularity  LowCardinality(String),
    freq         LowCardinality(String),
    series_id    String,
    model        LowCardinality(String),
    MAE          Float64,
    RMSE         Float64,
    MAPE         Float64,
    SMAPE        Float64,
    train_size   UInt32,
    computed_at  DateTime
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (granularity, freq, series_id, model);
