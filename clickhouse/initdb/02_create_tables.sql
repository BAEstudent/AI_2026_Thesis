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
