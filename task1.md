# Данную работу сделали студенты группы РИ-411050
- Ермолов Евгений
- Смирнов Василий 

# Часть 1 
Подробная инструкция по настройке отказоустойчивого кластера PostgreSQL


### Подготовительные шаги

1. Установка необходимого ПО
На всех узлах (Master, Replica и Arbiter) выполнить:


## Для Ubuntu/Debian
``` python
  sudo apt update
  sudo apt install -y postgresql-12 postgresql-client-12 python3 python3-pip
  sudo pip3 install psycopg2-binary
``` 

2. Настройка сетевых имен (опционально)
Добавьте в /etc/hosts на всех узлах:
``` python
192.168.1.1 pg-master
192.168.1.2 pg-replica
192.168.1.3 pg-arbiter
``` 
## Настройка Master-узла
1. Инициализация БД
``` python
sudo -u postgres /usr/lib/postgresql/12/bin/initdb -D /var/lib/postgresql/12/main
``` 
2. Настройка конфигурации
Отредактируйте /var/lib/postgresql/12/main/postgresql.conf:
``` ini
listen_addresses = '*'
wal_level = replica
max_wal_senders = 3
synchronous_commit = on
synchronous_standby_names = 'pg-replica'
``` 
3. Настройка доступа
Добавьте в /var/lib/postgresql/12/main/pg_hba.conf:
``` python
host    replication     replicator      pg-replica/32       md5
host    all             all             pg-replica/32       md5
``` 

4. Создание пользователя репликации
``` python
sudo -u postgres psql -c "CREATE USER replicator WITH REPLICATION ENCRYPTED PASSWORD 'securepassword';"
``` 
5. Запуск сервера
``` python
sudo systemctl start postgresql@12-main
``` 
## Настройка Replica-узла

1. Остановка PostgreSQL (если запущен)
``` python 
sudo systemctl stop postgresql
``` 
2. Создание резервной копии с Master
``` python
sudo -u postgres rm -rf /var/lib/postgresql/12/main/*
sudo -u postgres pg_basebackup -h pg-master -U replicator -D /var/lib/postgresql/12/main -P -R -X stream
``` 
Введите пароль securepassword при запросе.

3. Настройка файла standby.signal
``` python
sudo -u postgres touch /var/lib/postgresql/12/main/standby.signal
``` 
4. Проверка конфигурации
Убедитесь, что в /var/lib/postgresql/12/main/postgresql.auto.conf есть:

``` ini
primary_conninfo = 'user=replicator password=securepassword host=pg-master port=5432 sslmode=prefer sslcompression=0 gssencmode=prefer krbsrvname=postgres target_session_attrs=any'
``` 
5. Запуск сервера
``` python
sudo systemctl start postgresql@12-main
``` 
## Настройка Arbiter-узла

1. Установка простого TCP-сервера
Создайте файл /opt/pg_arbiter.py:

```python
import socket
import threading

def handle_client(conn, addr):
    try:
        data = conn.recv(1024)
        if data == b"CHECK_MASTER":
            # Проверяем доступность мастера
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(5)
            try:
                test_sock.connect(('pg-master', 5432))
                conn.sendall(b"MASTER_UP")
            except:
                conn.sendall(b"MASTER_DOWN")
            finally:
                test_sock.close()
    finally:
        conn.close()

def start_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('0.0.0.0', 5432))
        s.listen()
        while True:
            conn, addr = s.accept()
            threading.Thread(target=handle_client, args=(conn, addr)).start()

if __name__ == "__main__":
    start_server()
```

2. Запуск арбитра
``` python
sudo python3 /opt/pg_arbiter.py &
``` 
## Настройка агента мониторинга

1. Создание конфигурационных файлов
   
На Master (/etc/pg_agent.conf):

``` python
[node]
type = master
id = node1
host = pg-master

[postgres]
port = 5432
data_dir = /var/lib/postgresql/12/main

[arbiter]
host = pg-arbiter

[monitoring]
check_interval = 10
timeout = 5
На Replica (/etc/pg_agent.conf):
```
```ini
[node]
type = replica
id = node2
host = pg-replica

[postgres]
port = 5432
data_dir = /var/lib/postgresql/12/main

[replication]
master_host = pg-master

[arbiter]
host = pg-arbiter

[monitoring]
check_interval = 10
timeout = 5
``` 
2. Запуск агента
На всех узлах:

``` python
sudo curl -o /opt/postgres_agent.py https://raw.githubusercontent.com/your-repo/postgres-ha-agent/main/agent.py
sudo python3 /opt/postgres_agent.py /etc/pg_agent.conf
``` 
Проверка работы
1. Проверка репликации
На Master:

``` python
sudo -u postgres psql -c "SELECT * FROM pg_stat_replication;"
``` 
Должна быть видна реплика.

2. Тестирование отказоустойчивости
Остановите PostgreSQL на Master:

``` python
sudo systemctl stop postgresql@12-main
``` 
Через 10-15 секунд проверьте статус на Replica:

``` python
sudo -u postgres psql -c "SELECT pg_is_in_recovery();"
``` 
Должно вернуться f (false), что означает переход в режим master.

Проверьте запись данных на новом Master:
``` python
sudo -u postgres psql -c "CREATE TABLE test_failover(id serial PRIMARY KEY);"
``` 

## Автозапуск агента
Создайте systemd сервис (/etc/systemd/system/pg_agent.service):
``` ini
[Unit]
Description=PostgreSQL HA Agent
After=postgresql.service

[Service]
ExecStart=/usr/bin/python3 /opt/postgres_agent.py /etc/pg_agent.conf
Restart=always
User=postgres

[Install]
WantedBy=multi-user.target
Затем:


sudo systemctl daemon-reload
sudo systemctl enable pg_agent
sudo systemctl start pg_agent
``` 

# Агент 
``` python
import os
import time
import subprocess
import psycopg2
import configparser
import socket
import logging
from threading import Thread

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('postgres_agent.log'),
        logging.StreamHandler()
    ]
)

class PostgresAgent:
    def __init__(self, config_file):
        self.config = self.load_config(config_file)
        self.node_type = self.config.get('node', 'type')
        self.node_id = self.config.get('node', 'id')
        self.host = self.config.get('node', 'host')
        self.port = int(self.config.get('postgres', 'port', fallback='5432'))
        
        self.master_host = self.config.get('replication', 'master_host', fallback=None)
        self.arbiter_host = self.config.get('arbiter', 'host', fallback=None)
        
        self.check_interval = int(self.config.get('monitoring', 'check_interval', fallback='10'))
        self.timeout = int(self.config.get('monitoring', 'timeout', fallback='5'))
        
        self.is_master = False
        self.replication_active = False
        
        logging.info(f"Initialized {self.node_type} node {self.node_id} on {self.host}")

    def load_config(self, config_file):
        config = configparser.ConfigParser()
        config.read(config_file)
        return config

    def check_connection(self, host, port=5432):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception as e:
            logging.error(f"Connection check failed to {host}:{port}: {e}")
            return False

    def check_postgres_status(self):
        try:
            conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                user='postgres',
                database='postgres'
            )
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            cursor.close()
            conn.close()
            return True
        except Exception as e:
            logging.error(f"PostgreSQL status check failed: {e}")
            return False

    def promote_to_master(self):
        if self.is_master:
            return True
            
        logging.info("Promoting to master...")
        try:
            # Останавливаем репликацию и делаем promote
            subprocess.run(['pg_ctl', 'promote', '-D', self.config.get('postgres', 'data_dir')], check=True)
            
            # Обновляем конфигурацию
            self.is_master = True
            self.replication_active = False
            self.master_host = self.host
            
            logging.info("Successfully promoted to master")
            return True
        except subprocess.CalledProcessError as e:
            logging.error(f"Promote failed: {e}")
            return False

    def coordinate_with_arbiter(self):
        if not self.arbiter_host:
            logging.warning("No arbiter configured")
            return False
            
        try:
            # Простая реализация координации через TCP соединение
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.arbiter_host, 5432))
            
            # Отправляем запрос на проверку мастера
            sock.sendall(b"CHECK_MASTER")
            response = sock.recv(1024)
            sock.close()
            
            return response == b"MASTER_DOWN"
        except Exception as e:
            logging.error(f"Arbiter coordination failed: {e}")
            return False

    def block_external_access(self):
        logging.info("Blocking external access to PostgreSQL")
        try:
            subprocess.run(['iptables', '-A', 'INPUT', '-p', 'tcp', '--dport', '5432', '-j', 'DROP'], check=True)
            return True
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to block access: {e}")
            return False

    def monitor_network(self):
        while True:
            try:
                # Проверяем состояние текущего узла
                if not self.check_postgres_status():
                    logging.error("Local PostgreSQL is not responding")
                    time.sleep(self.check_interval)
                    continue
                
                # Для реплики проверяем состояние мастера
                if not self.is_master and self.master_host:
                    master_available = self.check_connection(self.master_host)
                    arbiter_available = self.check_connection(self.arbiter_host) if self.arbiter_host else False
                    
                    if not master_available:
                        logging.warning(f"Master {self.master_host} is not available")
                        
                        # Координируем с арбитром, если он доступен
                        if arbiter_available and self.coordinate_with_arbiter():
                            logging.info("Arbiter confirmed master is down. Promoting...")
                            self.promote_to_master()
                        elif not arbiter_available:
                            logging.warning("Arbiter is not available. Not promoting.")
                    else:
                        self.replication_active = True
                
                # Для мастера проверяем наличие реплики
                if self.is_master and not self.check_connection(self.arbiter_host):
                    logging.warning("No connection to arbiter. Blocking external access.")
                    self.block_external_access()
                
                time.sleep(self.check_interval)
                
            except Exception as e:
                logging.error(f"Monitoring error: {e}")
                time.sleep(self.check_interval)

    def run(self):
        # Инициализация состояния
        if self.node_type.lower() == 'master':
            self.is_master = True
        else:
            self.replication_active = True
        
        # Запуск мониторинга в отдельном потоке
        monitor_thread = Thread(target=self.monitor_network)
        monitor_thread.daemon = True
        monitor_thread.start()
        
        # Основной цикл (может быть использован для других задач)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Shutting down agent...")

if __name__ == "__main__":
    agent = PostgresAgent('pg_agent.conf')
    agent.run()
``` 
