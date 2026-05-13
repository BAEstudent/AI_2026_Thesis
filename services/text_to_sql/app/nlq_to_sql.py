from typing import Dict, Any, Optional, List, Tuple
import logging
from datetime import datetime
import clickhouse_connect
import torch
from sentence_transformers import SentenceTransformer
from pathlib import Path
import json
from transformers import T5Tokenizer, T5ForConditionalGeneration
from sentence_transformers import SentenceTransformer, util
from services.nlq_to_sql.app.app_config import settings


logger = logging.getLogger(__name__)

def get_db_schema_clickhouse(host: str = 'localhost', 
                              port: int = 8123, 
                              user: str = 'default', 
                              password: str = '', 
                              database: str = 'default') -> Dict[str, List[str]]:
    """
    Извлекает схему ClickHouse: таблица -> список колонок.
    Игнорирует системные базы (system, information_schema и т.д.).
    """
    client = clickhouse_connect.get_client(host=host, port=port, username=user, password=password, database=database)
    query_tables = f"""
    SELECT name FROM system.tables 
    WHERE database = '{database}' AND is_temporary = 0
    """
    tables = client.query(query_tables).result_rows
    schema = {}
    for (table_name,) in tables:
        query_cols = f"""
        SELECT name FROM system.columns 
        WHERE database = '{database}' AND table = '{table_name}'
        """
        cols = client.query(query_cols).result_rows
        schema[table_name] = [col[0] for col in cols]
    client.close()
    return schema


def format_schema_for_prompt(schema: Dict[str, List[str]]) -> str:
    """Форматирует схему для вставки в промпт."""
    lines = []
    for table, cols in schema.items():
        lines.append(f"Table {table}: columns = {', '.join(cols)}")
    return "\n".join(lines)


def question_guided_schema_selector(
    question: str,
    full_schema: Dict[str, List[str]],
    embedder,
    lambda_coef: float = 0.8,
    tau: float = 0.6) -> Dict[str, List[str]]:
    """
    Полноценный Question-Guided Schema Selector по статье TriSQL.
    
    Параметры:
        question (str): вопрос пользователя на естественном языке.
        full_schema (dict): полная схема БД вида {таблица: [колонка1, колонка2, ...]}.
        lambda_coef (float): вес суммы колоночных relevance (λ в формуле 4).
        tau (float): порог отбора таблиц (τ в формуле 5).
        
    Возвращает:
        filtered_schema (dict): отфильтрованная схема {таблица: [все_колонки_таблицы]}.
    """
    # 1. Кодируем вопрос в вектор
    q_emb = embedder.encode(question, convert_to_tensor=True)  # shape (dim,)
    
    # 2. Кодируем названия таблиц и получаем эмбеддинги
    table_names = list(full_schema.keys())
    table_embs = embedder.encode(table_names, convert_to_tensor=True)  # (n_tables, dim)
    
    # 3. Вычисляем table-level relevance (формула 2)
    # Косинусное сходство между вопросом и каждой таблицей
    cos_sim_table = util.cos_sim(q_emb, table_embs)[0]  # (n_tables,)
    # Применяем softmax для получения вероятностного распределения
    w_table = torch.softmax(cos_sim_table, dim=0)  # (n_tables,)
    
    # 4. Для каждой таблицы вычисляем column-level relevance (формула 3)
    # и сразу агрегируем (формула 4)
    tilde_w_table = []
    for i, table in enumerate(table_names):
        columns = full_schema[table]
        if not columns:
            # Если нет колонок, вклад колонок = 0
            col_sum = 0.0
        else:
            # Кодируем названия колонок
            col_embs = embedder.encode(columns, convert_to_tensor=True)  # (n_cols, dim)
            # Косинусное сходство вопроса с каждой колонкой
            cos_sim_col = util.cos_sim(q_emb, col_embs)[0]  # (n_cols,)
            # Softmax внутри таблицы
            w_col = torch.softmax(cos_sim_col, dim=0)  # (n_cols,)
            # Суммируем relevance колонок (формула 4)
            col_sum = w_col.sum().item()
        
        # Агрегированная relevance таблицы (формула 4)
        tilde = w_table[i].item() + lambda_coef * col_sum
        tilde_w_table.append(tilde)
    
    # 5. Отбираем таблицы по порогу (формула 5)
    selected_tables = []
    for i, table in enumerate(table_names):
        if tilde_w_table[i] >= tau:
            selected_tables.append(table)
    
    # 6. Формируем отфильтрованную схему: все колонки выбранных таблиц
    filtered_schema = {table: full_schema[table] for table in selected_tables}
    
    return filtered_schema


def generate_sql(
        question: str,
        schema_subset: Dict[str, List[str]],
        model,
        tokenizer,
        device,
        max_attempts: int = 2) -> str:
    """
    Отправляет промпт модели и возвращает сгенерированный SQL.
    """
    schema_str = format_schema_for_prompt(schema_subset)
    base_prompt = f"""You are a SQL expert. Convert the user question into an SQL SELECT query.
Database schema:
{schema_str}

Question: {question}
SQL:"""
    
    inputs = tokenizer(base_prompt, return_tensors="pt", truncation=True, max_length=512).to(device)
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=150,
            do_sample=False,
            num_beams=4,
            early_stopping=True
        )
    sql = tokenizer.decode(outputs[0], skip_special_tokens=True)
    # Очистка: берём первую строку с SELECT
    lines = sql.split('\n')
    for line in lines:
        if line.strip().upper().startswith("SELECT"):
            sql = line.strip()
            break
    return sql


def execute_sql_clickhouse(sql: str, 
                           host: str = 'localhost', 
                           port: int = 8123, 
                           user: str = 'default', 
                           password: str = '', 
                           database: str = 'default') -> Tuple[bool, Optional[List[Tuple]], str]:
    """
    Выполняет SQL, возвращает (успех, результат, сообщение об ошибке).
    """
    try:
        client = clickhouse_connect.get_client(host=host, port=port, username=user, password=password, database=database)
        if sql.strip().upper().startswith("SELECT"):
            result = client.query(sql).result_rows
        else:
            client.command(sql)
            result = None
        client.close()
        return True, result, ""
    except Exception as e:
        return False, None, str(e)
    

def refine_sql(
        question: str,
        schema_subset: Dict[str, List[str]],
        error_msg: str,
        failed_sql: str,
        model,
        tokenizer,
        device) -> str:
    """
    Повторная генерация с учётом ошибки выполнения.
    """
    schema_str = format_schema_for_prompt(schema_subset)
    refine_prompt = f"""The previous SQL query failed with error: {error_msg}
Original question: {question}
Failed SQL: {failed_sql}

Database schema:
{schema_str}

Please correct the SQL query. Output only the corrected SQL statement.
Corrected SQL:"""
    
    inputs = tokenizer(refine_prompt, return_tensors="pt", truncation=True, max_length=512).to(device)
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=150,
            do_sample=False,
            num_beams=4
        )
    new_sql = tokenizer.decode(outputs[0], skip_special_tokens=True)
    for line in new_sql.split('\n'):
        if line.strip().upper().startswith("SELECT"):
            new_sql = line.strip()
            break
    return new_sql


class NLQToSQL:
    def __init__(self, 
                 host: str = 'localhost', 
                 port: int = 8123, 
                 user: str = 'default', 
                 password: str = '', 
                 database: str = 'analytics',
                 models_path: str = '/app/app/services/models'):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.full_schema = get_db_schema_clickhouse(host, port, user, password, database)
        
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.t5_model, self.t5_tokenizer = self._load_t5_model(models_path)
        self.embedder = self._load_embedder(models_path)
        self.full_schema = get_db_schema_clickhouse(
            host, port, user, password, database
        )
        
        print(f"✅ All models loaded on {self.device}")

    
    def _load_t5_model(self, models_path: str):
        """Загрузка T5 модели из локальной директории"""
        model_dir = Path(models_path) / "flan-t5-large"
        
        if not model_dir.exists():
            raise FileNotFoundError(f"T5 model directory not found: {model_dir}")
        
        print(f"🔄 Loading T5 from: {model_dir}")
        
        tokenizer = T5Tokenizer.from_pretrained(model_dir / "tokenizer")
        model = T5ForConditionalGeneration.from_pretrained(model_dir / "model")
        model = model.to(self.device)
        model.eval()
        
        return model, tokenizer
    
    def _load_embedder(self, models_path: str):
        """Загрузка SentenceTransformer из локальной директории"""
        model_dir = Path(models_path) / "all-MiniLM-L6-v2"
        
        if not model_dir.exists():
            raise FileNotFoundError(f"Embedder directory not found: {model_dir}")
        
        print(f"🔄 Loading embedder from: {model_dir}")
        
        embedder = SentenceTransformer(
            str(model_dir), 
            device=str(self.device)
        )
        embedder.eval()
        
        return embedder
    
    def ask(self, question: str, auto_refine: bool = True) -> Dict:
        """
        Основной метод: вопрос -> выполненный SQL -> результат.
        """
        # Шаг 1: селекция схемы
        # В методе ask:
        relevant_schema = question_guided_schema_selector(
            question, 
            self.full_schema,
            embedder=self.embedder,
            device=self.device,
            lambda_coef=0.8,
            tau=1.25
        )
        print(f"[Schema selected] Tables: {list(relevant_schema.keys())}")
        
        # Шаг 2: генерация SQL
        sql = generate_sql(
            question,
            relevant_schema,
            model=self.t5_model,
            tokenizer=self.t5_tokenizer,
            device=self.device)
        print(f"[Generated SQL] {sql}")
        
        # Шаг 3: выполнение
        success, result, error = execute_sql_clickhouse(
            sql, self.host, self.port, self.user, self.password, self.database
        )
        refined = False
        
        # Шаг 4: рефайн при ошибке
        if not success and auto_refine:
            print(f"[Execution error] {error}. Trying to refine...")
            refined_sql = refine_sql(
                question,
                relevant_schema,
                error,
                sql,
                model=self.t5_model,
                tokenizer=self.t5_tokenizer,
                device=self.device
            )
            print(f"[Refined SQL] {refined_sql}")
            success2, result2, error2 = execute_sql_clickhouse(
                refined_sql, self.host, self.port, self.user, self.password, self.database
            )
            if success2:
                success, result, error, sql, refined = True, result2, "", refined_sql, True
            else:
                error = f"Original error: {error}. Refine error: {error2}"
        
        return {
            "sql": sql,
            "success": success,
            "result": result,
            "error": error,
            "refined": refined
        }


class NLQToSQLService:
    """Сервис-обёртка для NLQToSQL с метриками и логированием"""
    
    def __init__(self, host: str, port: int, user: str, 
                 password: str, database: str, models_path: str):
        self.nlq = NLQToSQL(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database
        )
        self.models_path = models_path
        self.metrics = {
            "total_queries": 0,
            "successful_queries": 0,
            "refined_queries": 0,
            "failed_queries": 0,
            "avg_latency_ms": 0.0
        }
        self._latencies = []
        
        logger.info(f"📦 Models path: {models_path}")
    
    def ask(self, question: str, auto_refine: bool = True) -> Dict[str, Any]:
        """Основной метод с метриками"""
        start_time = datetime.now()
        
        try:
            result = self.nlq.ask(question=question, auto_refine=auto_refine)
            
            self.metrics["total_queries"] += 1
            if result["success"]:
                self.metrics["successful_queries"] += 1
            else:
                self.metrics["failed_queries"] += 1
            if result["refined"]:
                self.metrics["refined_queries"] += 1
            
            latency = (datetime.now() - start_time).total_seconds() * 1000
            self._latencies.append(latency)
            self.metrics["avg_latency_ms"] = sum(self._latencies) / len(self._latencies)
            
            return result
            
        except Exception as e:
            logger.error(f"Query failed: {str(e)}")
            self.metrics["failed_queries"] += 1
            raise
    
    def get_schema_summary(self) -> Dict[str, Any]:
        """Краткая информация о схеме"""
        return {
            "tables": list(self.nlq.full_schema.keys()),
            "total_columns": sum(len(cols) for cols in self.nlq.full_schema.values())
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Текущие метрики сервиса"""
        return {
            **self.metrics,
            "uptime": "active"
        }
