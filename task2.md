# Данную работу сделали студенты группы РИ-411050
- Ермолов Евгений
- Смирнов Василий 

# Часть 2
## Инструкция по развертыванию тестовой системы
1. Требования
- Развернутый отказоустойчивый кластер PostgreSQL (из части 1)
- Установленные пакеты: psycopg2-binary, concurrent-log-handler
- SSH-доступ между узлами без пароля (для инжекции сбоев)

2. Настройка тестового окружения
Создайте тестовую БД:

```bash
psql -h pg-master -U postgres -c "CREATE DATABASE test_db"
Установите зависимости:
```
```bash
pip install psycopg2-binary concurrent-log-handler

```
Настройте SSH-ключи для доступа к узлам:
```bash
ssh-keygen -t rsa
ssh-copy-id postgres@pg-master
ssh-copy-id postgres@pg-replica
```
3. Запуск тестов
Базовый тест:

```python
tester = FailoverTester()
tester.run_test()
```
Полный цикл тестов с разными настройками:

```python
tester.run_sync_commit_tests()
```
### Анализ результатов
Пример отчета:
=== Failover Test Report ===
Test configuration:
- Duration: 300 seconds
- Workers: 50
- Failure injected at: 142s
- synchronous_commit: on

Results:
- Attempted inserts: 1,245,678
- Successful inserts: 1,245,678
- Records in DB: 1,245,678
- Lost records: 0
- Unexpected records: 0

Failover metrics:
- Detection time: 12.3s
- Recovery time: 8.7s
- Downtime: 21.0s
Интерпретация результатов

1) Безопасные настройки (synchronous_commit=on/remote_apply):
- Не должно быть потерь данных
- Возможно увеличение времени отклика
- Рекомендуется для критичных данных

 2) Небезопасные настройки (synchronous_commit=off/local):
- Возможны потери данных при сбоях
- Более высокая производительность
- Подходит для не критичных данных

### Дополнительные тестовые сценарии
1. Тестирование сетевых разделений
```python
def test_network_partitions(self):
    scenarios = [
        ("master-replica", ["pg-master", "pg-replica"]),
        ("master-arbiter", ["pg-master", "pg-arbiter"]),
        ("replica-arbiter", ["pg-replica", "pg-arbiter"])
    ]
    
    for name, hosts in scenarios:
        self.logger.info(f"Testing {name} partition")
        # Создание разделения сети
        subprocess.run(["ssh", hosts[0], f"sudo iptables -A INPUT -p tcp -s {hosts[1]} -j DROP"])
        subprocess.run(["ssh", hosts[1], f"sudo iptables -A INPUT -p tcp -s {hosts[0]} -j DROP"])
        
        # Запуск теста
        self.run_test()
        
        # Восстановление
        subprocess.run(["ssh", hosts[0], f"sudo iptables -D INPUT -p tcp -s {hosts[1]} -j DROP"])
        subprocess.run(["ssh", hosts[1], f"sudo iptables -D INPUT -p tcp -s {hosts[0]} -j DROP"])
```
2. Тестирование длительных сбоев
```python
def test_extended_failures(self):
    durations = [60, 300, 600]  # seconds
    
    for duration in durations:
        self.logger.info(f"Testing {duration}s master outage")
        subprocess.run(["ssh", "pg-master", "sudo systemctl stop postgresql"])
        
        # Запуск теста во время сбоя
        time.sleep(duration/2)
        self.run_test()
        
        # Восстановление
        subprocess.run(["ssh", "pg-master", "sudo systemctl start postgresql"])
        time.sleep(60)  # Wait for recovery
```
Данная система тестирования позволяет комплексно проверить отказоустойчивость кластера PostgreSQL и гарантировать, что подтвержденные транзакции не будут потеряны при различных сценариях сбоев.

1. Архитектура тестовой системы
```python
import psycopg2
import random
import time
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
import logging

class FailoverTester:
    def __init__(self):
        self.config = self.load_config()
        self.logger = self.setup_logger()
        self.inserted_ids = set()
        self.lock = threading.Lock()
        
    def load_config(self):
        return {
            'master_host': 'pg-master',
            'replica_host': 'pg-replica',
            'arbiter_host': 'pg-arbiter',
            'dbname': 'test_db',
            'user': 'postgres',
            'password': 'password',
            'test_duration': 300,
            'failure_window': (100, 200),
            'num_workers': 50
        }
    
    def setup_logger(self):
        logger = logging.getLogger('failover_tester')
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler('failover_test.log')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger
```
2. Тестовый сценарий
```python
    def run_test(self):
        # 1. Развертывание кластера
        self.deploy_clean_cluster()
        
        # 2. Создание тестовой таблицы
        self.create_test_table()
        
        # 3. Запуск асинхронных запросов
        with ThreadPoolExecutor(max_workers=self.config['num_workers']) as executor:
            # Запуск потока для инжекции сбоев
            failure_thread = threading.Thread(target=self.inject_failures)
            failure_thread.start()
            
            # Запуск рабочих потоков для вставки данных
            futures = [executor.submit(self.insert_data_worker) 
                      for _ in range(self.config['num_workers'])]
            
            # Ожидание завершения теста
            time.sleep(self.config['test_duration'])
            
        # 4. Верификация данных
        self.verify_data_integrity()
        
        # Генерация отчета
        self.generate_report()
```
3. Методы инжекции сбоев
```python
    def inject_failures(self):
        # Ждем случайное время в указанном окне
        delay = random.randint(*self.config['failure_window'])
        time.sleep(delay)
        
        self.logger.info("Injecting network failure on master node")
        
        # Варианты сбоев:
        choice = random.choice([1, 2, 3])
        
        if choice == 1:
            # Разрыв сети на мастер-узле
            subprocess.run(["ssh", "pg-master", "sudo iptables -A INPUT -p tcp --dport 5432 -j DROP"])
        elif choice == 2:
            # Остановка PostgreSQL на мастере
            subprocess.run(["ssh", "pg-master", "sudo systemctl stop postgresql"])
        else:
            # Перегрузка CPU на мастере
            subprocess.run(["ssh", "pg-master", "dd if=/dev/zero of=/dev/null &"])
        
        # Восстановление через 60 секунд
        time.sleep(60)
        self.recover_master()
    
    def recover_master(self):
        self.logger.info("Recovering master node")
        subprocess.run(["ssh", "pg-master", "sudo iptables -D INPUT -p tcp --dport 5432 -j DROP"])
        subprocess.run(["ssh", "pg-master", "sudo systemctl start postgresql"])
        subprocess.run(["ssh", "pg-master", "pkill -f 'dd if=/dev/zero'"])
```
4. Рабочий процесс вставки данных
```python
    def insert_data_worker(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        while time.time() < self.test_end_time:
            try:
                # Генерация случайного ID
                record_id = random.randint(1, 10**9)
                
                # Вставка данных
                cursor.execute("INSERT INTO test_table (id) VALUES (%s)", (record_id,))
                conn.commit()
                
                # Запись успешной вставки
                with self.lock:
                    self.inserted_ids.add(record_id)
                    
                # Случайная задержка
                time.sleep(random.uniform(0.001, 0.1))
                
            except Exception as e:
                self.logger.warning(f"Insert failed: {str(e)}")
                conn.rollback()
                time.sleep(1)
                
        cursor.close()
        conn.close()
    
    def get_connection(self):
        # Подключение с автоматическим выбором мастера
        hosts = f"{self.config['master_host']},{self.config['replica_host']}"
        return psycopg2.connect(
            host=hosts,
            dbname=self.config['dbname'],
            user=self.config['user'],
            password=self.config['password'],
            target_session_attrs="read-write"
        )
```
5. Верификация данных
```python
    def verify_data_integrity(self):
        self.logger.info("Starting data verification")
        
        # Получение всех записей из БД
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM test_table")
        db_ids = {row[0] for row in cursor.fetchall()}
        cursor.close()
        conn.close()
        
        # Проверка на отсутствие потерянных записей
        lost_records = self.inserted_ids - db_ids
        if lost_records:
            self.logger.error(f"DATA LOSS DETECTED! Lost {len(lost_records)} records")
        else:
            self.logger.info("No data loss detected")
        
        # Проверка на лишние записи
        extra_records = db_ids - self.inserted_ids
        if extra_records:
            self.logger.warning(f"Found {len(extra_records)} unexpected records")
        
        # Статистика
        self.logger.info(f"Verification complete. Expected: {len(self.inserted_ids)}, Found: {len(db_ids)}")
```
6. Запуск тестов с разными настройками synchronous_commit
```python
    def run_sync_commit_tests(self):
        modes = ['off', 'local', 'remote_write', 'on', 'remote_apply']
        
        for mode in modes:
            self.logger.info(f"Starting test with synchronous_commit = {mode}")
            
            # Установка режима синхронизации
            self.set_sync_commit_mode(mode)
            
            # Запуск теста
            self.run_test()
            
            # Очистка перед следующим тестом
            self.cleanup()
    
    def set_sync_commit_mode(self, mode):
        conn = psycopg2.connect(
            host=self.config['master_host'],
            dbname=self.config['dbname'],
            user=self.config['user'],
            password=self.config['password']
        )
        cursor = conn.cursor()
        cursor.execute(f"ALTER SYSTEM SET synchronous_commit TO '{mode}'")
        cursor.execute("SELECT pg_reload_conf()")
        conn.commit()
        cursor.close()
        conn.close()
        
        self.logger.info(f"Set synchronous_commit to {mode}")
```
