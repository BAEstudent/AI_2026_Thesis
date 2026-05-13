


# Marketplace Analytics Platform

Магистерская диссертация Юдайкина К.В., НИУ ВШЭ, 2026.  
Платформа аналитики для продавцов маркетплейсов с NLQ-интерфейсом через Telegram-бота.


## Содержание

- 💬 **Text‑to‑SQL** – запросы на естественном языке → SQL → ответ из ClickHouse
- 📈 **Прогнозирование** – daily/weekly прогнозы заказов и просмотров на уровнях товар/категория/глобально
- 🚨 **Аномалии** – ежедневное обнаружение отклонений метрик (z‑score по скользящему окну)

## Структура проекта

``` 
├── airflow/                # DAG'и и конфигурация Airflow
│   ├── dags/
│   │   ├── load_to_postgres.py
│   │   ├── transform_to_clickhouse.py
│   │   ├── forecast_training.py
│   │   └── anomaly_detection_dag.py
│   └── docker-compose.airflow.yaml
├── services/               # Микросервисы аналитики
│   ├── text2sql/           # NLQ-модуль (TriSQL)
│   ├── forecast/           # Прогнозирование
│   └── anomaly/            # Детекция аномалий
├── bot/                    # Telegram-бот
│   ├── main.py
│   ├── handlers/
│   ├── keyboards.py
│   └── services/           # Клиенты к REST API и ClickHouse
├── data/                   # Исходные датасеты (Ozon) и конфигурация загрузки
├── docker-compose.yml      # Основной стек (PG, CH, MinIO, Redis, сервисы)
└── README.md
```

## Быстрый старт

1. **Клонировать репозиторий**  
   ```bash
   git clone https://github.com/BAEstudent/AI_2026_Thesis.git
   cd AI_2026_Thesis && git checkout main
   ```
2. **Настроить переменные окружения**  
   - Создать `.env` в корне проекта, указав параметры подключения к ClickHouse и Telegram‑боту
3. **Запустить сервис**  
    ```bash 
    docker-compose up --build
    ```
   - Сервис будет доступен по адресу `http://localhost:8002` для прогнозирования, `http://localhost:8002` для Text‑to‑SQL и `http://localhost:8080` для Airflow UI

