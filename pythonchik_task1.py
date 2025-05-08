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
