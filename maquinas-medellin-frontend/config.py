# config.py
import os

class Config:
    # Configuración MySQL - CONTRASEÑA VACÍA
    MYSQL_HOST = 'localhost'
    MYSQL_USER = 'root'
    MYSQL_PASSWORD = ''  # ← CONTRASEÑA VACÍA
    MYSQL_DATABASE = 'maquinasmedellin'
    MYSQL_PORT = 3306
    
    # Configuración Flask
    SECRET_KEY = 'maquinasmedellin_secret_key_2025'
    
    # Configuración Sentry
    SENTRY_DSN = "https://5fc281c2ace4860969f2f1f6fa10039d@o4510071013310464.ingest.us.sentry.io/4510071047454720"
    
    # Configuración tiempo
    TIMEZONE = 'America/Bogota'

class DevelopmentConfig(Config):
    DEBUG = True
    TESTING = False

class ProductionConfig(Config):
    DEBUG = False
    TESTING = False

# Configuración para ESP32
ESP32_CONFIG = {
    'local_backend': 'http://192.168.1.21:5000',
    'ngrok_backend': 'https://wider-damaris-anachronistically.ngrok-free.dev',
    'machine_id': 1
}