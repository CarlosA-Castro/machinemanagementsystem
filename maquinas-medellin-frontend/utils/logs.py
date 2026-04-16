import logging

from flask import request, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor

logger = logging.getLogger(LOGGER_NAME)


def log_app_event(level, message, module=None, extra_data=None, user_id=None):
    """Registra eventos de aplicación en app_logs."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            INSERT INTO app_logs
                (level, module, message, ip_address, user_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                level,
                module or 'app',
                str(message)[:1000],
                request.remote_addr if hasattr(request, 'remote_addr') else None,
                user_id or session.get('user_id'),
            ),
        )
        connection.commit()
    except Exception as e:
        logger.error(f"Error en log_app_event: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def log_error(error_type, error_message, stack_trace=None, module=None, user_id=None):
    """Registra errores en error_logs."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            INSERT INTO error_logs
                (error_type, error_message, stack_trace, module,
                 request_path, request_method, ip_address, user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                error_type,
                str(error_message)[:2000],
                str(stack_trace)[:5000] if stack_trace else None,
                module or 'app',
                request.path if hasattr(request, 'path') else None,
                request.method if hasattr(request, 'method') else None,
                request.remote_addr if hasattr(request, 'remote_addr') else None,
                user_id or session.get('user_id'),
            ),
        )
        connection.commit()
    except Exception as e:
        logger.error(f"Error en log_error: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def update_daily_statistics():
    """Actualiza estadísticas diarias de logs."""
    connection = None
    cursor = None
    try:
        from datetime import datetime

        connection = get_db_connection()
        if not connection:
            return

        cursor = get_db_cursor(connection)
        today = datetime.now().date()

        cursor.execute(
            """
            SELECT
                COUNT(*) as total_logs,
                COUNT(CASE WHEN level = 'INFO' THEN 1 END) as info_logs,
                COUNT(CASE WHEN level = 'WARNING' THEN 1 END) as warning_logs,
                COUNT(CASE WHEN level = 'ERROR' THEN 1 END) as error_logs
            FROM app_logs
            WHERE DATE(created_at) = %s
            """,
            (today,),
        )
        app_stats = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                COUNT(*) as access_logs,
                COUNT(DISTINCT ip_address) as unique_ips,
                COUNT(DISTINCT user_id) as unique_users,
                AVG(response_time_ms) as avg_response_time
            FROM access_logs
            WHERE DATE(created_at) = %s
            """,
            (today,),
        )
        access_stats = cursor.fetchone()

        cursor.execute(
            """
            INSERT INTO log_statistics
                (date, total_logs, info_logs, warning_logs, error_logs,
                 access_logs, unique_ips, unique_users, avg_response_time_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                total_logs = VALUES(total_logs),
                info_logs = VALUES(info_logs),
                warning_logs = VALUES(warning_logs),
                error_logs = VALUES(error_logs),
                access_logs = VALUES(access_logs),
                unique_ips = VALUES(unique_ips),
                unique_users = VALUES(unique_users),
                avg_response_time_ms = VALUES(avg_response_time_ms),
                updated_at = NOW()
            """,
            (
                today,
                app_stats['total_logs'] or 0,
                app_stats['info_logs'] or 0,
                app_stats['warning_logs'] or 0,
                app_stats['error_logs'] or 0,
                access_stats['access_logs'] or 0,
                access_stats['unique_ips'] or 0,
                access_stats['unique_users'] or 0,
                access_stats['avg_response_time'] or 0,
            ),
        )
        connection.commit()
    except Exception as e:
        logger.debug(f"Error en update_daily_statistics: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def check_alerts(level, message, module):
    """Verifica si alguna alerta activa debe dispararse."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM log_alerts WHERE is_active = TRUE")
        alerts = cursor.fetchall()

        for alert in alerts:
            if level == 'ERROR' and 'error' in alert['condition'].lower():
                cursor.execute(
                    """
                    UPDATE log_alerts
                    SET last_triggered = NOW()
                    WHERE id = %s
                    """,
                    (alert['id'],),
                )
                logger.warning(f"ALERTA: {alert['alert_message']}")

        connection.commit()
    except Exception as e:
        logger.debug(f"Error en check_alerts: {e}")
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def log_info(message, module=None, user_id=None):
    log_app_event('INFO', message, module, user_id=user_id or session.get('user_id'))


def log_warning(message, module=None, user_id=None):
    log_app_event('WARNING', message, module, user_id=user_id or session.get('user_id'))


def log_error_system(error, module=None, user_id=None):
    import traceback

    log_error(
        type(error).__name__,
        str(error),
        traceback.format_exc(),
        module,
        user_id or session.get('user_id'),
    )


def log_user_action(action, user_id=None):
    log_info(f"Usuario {user_id or session.get('user_id')}: {action}", 'user_action')


def log_system_event(event):
    log_info(f"Evento del sistema: {event}", 'system')
