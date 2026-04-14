import logging
import traceback
import mysql.connector

from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def get_db_connection():
    """
    Abre una conexión fresca a MySQL y fija la zona horaria Colombia (-05:00).
    Cada llamador es responsable de cerrar la conexión con connection.close().
    Retorna None si la conexión falla (el llamador debe manejar este caso).
    """
    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            port=DB_PORT,
            auth_plugin="mysql_native_password"
        )
        cursor = connection.cursor()
        cursor.execute("SET time_zone = '-05:00'")
        cursor.close()
        return connection
    except Exception as e:
        logger.error(f"Error obteniendo conexión a BD: {e}")
        traceback.print_exc()
        return None


def get_db_cursor(connection):
    """
    Retorna un cursor de diccionario para la conexión dada.
    Retorna None si falla.
    """
    try:
        return connection.cursor(dictionary=True)
    except Exception as e:
        logger.error(f"Error obteniendo cursor: {e}")
        return None
