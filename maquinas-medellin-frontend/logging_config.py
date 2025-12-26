import logging
from logging.handlers import RotatingFileHandler
import sys
from datetime import datetime

class ColorFormatter(logging.Formatter):
    """Formateador de logs con colores para consola"""
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Verde
        'WARNING': '\033[33m',   # Amarillo
        'ERROR': '\033[31m',     # Rojo
        'CRITICAL': '\033[41m',  # Fondo rojo
        'RESET': '\033[0m'       # Reset
    }
    
    def format(self, record):
        # Formato base
        log_msg = super().format(record)
        
        # Agregar color si es consola
        if hasattr(sys.stdout, 'isatty') and sys.stdout.isatty():
            color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
            log_msg = f"{color}{log_msg}{self.COLORS['RESET']}"
            
        return log_msg

def setup_logging(app):
    """Configura logging mejorado"""
    
    # Crear directorio de logs si no existe
    import os
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    # Formato para archivo
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Formato para consola (con colores)
    console_formatter = ColorFormatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Handler para archivo (rotativo)
    file_handler = RotatingFileHandler(
        f'logs/maquinas_{datetime.now().strftime("%Y%m")}.log',
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(file_formatter)
    
    # Handler para consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)
    
    # Configurar logger principal
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)
    
    # Desactivar logs de dependencias
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('mysql.connector').setLevel(logging.WARNING)
    
    return app

