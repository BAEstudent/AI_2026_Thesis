from datetime import datetime
from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator

def func():
    print("The dag is working")

with DAG(
    dag_id='test_dag',
    start_date=datetime(2024, 1, 1),
    catchup=False
) as dag:
    
    hello_task = PythonOperator(
        task_id='print_hello_task',
        python_callable=func
    )

    dummy = EmptyOperator(task_id='dummy')

    dummy >> hello_task