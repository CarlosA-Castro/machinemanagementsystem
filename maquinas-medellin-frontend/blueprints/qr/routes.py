import logging
import traceback
from datetime import datetime, timedelta

import mysql.connector
import sentry_sdk
from flask import Blueprint, request, jsonify, session, json, redirect

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from middleware.logging_mw import log_transaccion
from utils.auth import require_login
from utils.helpers import parse_json_col
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time, format_datetime_for_db, parse_db_datetime
from utils.validators import validate_required_fields
from utils.location_scope import get_active_location, user_can_view_all

logger = logging.getLogger(LOGGER_NAME)

qr_bp = Blueprint('qr', __name__)

VALID_PAYMENT_METHODS = {'efectivo', 'transferencia', 'tarjeta', 'mixto', 'cortesia', 'ajuste'}
PAYMENT_METHOD_LABELS = {
    'efectivo': 'Efectivo',
    'transferencia': 'Transferencia',
    'tarjeta': 'Tarjeta',
    'mixto': 'Mixto',
    'cortesia': 'Cortesia',
    'ajuste': 'Ajuste',
    'sin_registrar': 'Sin registrar',
}


def _normalize_payment_method(value):
    """Normaliza el método de pago recibido desde frontend o API."""
    if value is None:
        return None
    method = str(value).strip().lower()
    return method or None


def _payment_method_label(value):
    """Etiqueta amigable para respuestas JSON y vistas de caja."""
    return PAYMENT_METHOD_LABELS.get(value or 'sin_registrar', 'Sin registrar')


def _serialize_payment_method_audit(row):
    return {
        'payment_method': row.get('payment_method') or 'sin_registrar',
        'payment_method_label': _payment_method_label(row.get('payment_method')),
        'payment_method_updated_at': (
            parse_db_datetime(row['payment_method_updated_at']).strftime('%Y-%m-%d %H:%M:%S')
            if row.get('payment_method_updated_at') else None
        ),
        'payment_method_updated_by': row.get('payment_method_updated_by'),
        'payment_method_updated_by_name': row.get('payment_method_updated_by_name'),
        'payment_method_update_reason': row.get('payment_method_update_reason'),
    }


# ── Helper functions ──────────────────────────────────────────────────────────

def generar_codigo_qr():
    """Generar código QR con formato QR0001, QR0002, etc. usando contador global con reinicio en 9999"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT counter_value FROM globalcounter
            WHERE counter_type = 'QR_CODE'
            FOR UPDATE
        """)

        resultado = cursor.fetchone()

        if not resultado:

            cursor.execute("""
                INSERT INTO globalcounter (counter_type, counter_value, description)
                VALUES ('QR_CODE', 1, 'Contador para códigos QR (formato QR0001, QR0002, etc.)')
            """)
            nuevo_numero = 1
        else:

            nuevo_numero = resultado['counter_value'] + 1

            if nuevo_numero > 9999:
                nuevo_numero = 1
                logger.warning("Contador QR reiniciado a 1 (llegó al límite de 9999)")

            cursor.execute("""
                UPDATE globalcounter
                SET counter_value = %s
                WHERE counter_type = 'QR_CODE'
            """, (nuevo_numero,))

        # Formatear con 4 dígitos (reinicia en 1 después de 9999)
        nuevo_codigo = f"QR{nuevo_numero:04d}"

        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Sistema')
        local = session.get('active_location_name') or session.get('user_local', 'El Mekatiadero')
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        cursor.execute("""
            INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
            VALUES (%s, %s, %s, %s, %s)
        """, (nuevo_codigo, 0, 1, 1, ''))

        # Registrar automáticamente en el historial
        cursor.execute("""
            INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, ''))

        connection.commit()

        logger.info(f"Generado código QR: {nuevo_codigo} (contador: {nuevo_numero}) por {user_name}")

        return nuevo_codigo

    except Exception as e:
        logger.error(f"Error generando código QR: {e}")
        if connection:
            connection.rollback()
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def generar_codigos_qr_lote(cantidad_qr, nombre=""):
    """Generar múltiples códigos QR con 4 cifras usando contador global con manejo de reinicio"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return []

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT counter_value FROM globalcounter
            WHERE counter_type = 'QR_CODE'
            FOR UPDATE
        """)

        resultado = cursor.fetchone()

        if not resultado:

            cursor.execute("""
                INSERT INTO globalcounter (counter_type, counter_value, description)
                VALUES ('QR_CODE', %s, 'Contador para códigos QR')
            """, (cantidad_qr,))
            numero_inicial = 1
            numero_final = cantidad_qr
        else:

            numero_inicial = resultado['counter_value'] + 1
            numero_final = resultado['counter_value'] + cantidad_qr


            if numero_final > 9999:

                numeros_antes_reinicio = 9999 - numero_inicial + 1

                numeros_despues_reinicio = cantidad_qr - numeros_antes_reinicio


                rango1_inicio = numero_inicial
                rango1_final = 9999
                rango2_inicio = 1
                rango2_final = numeros_despues_reinicio

                nuevo_valor_contador = numeros_despues_reinicio


                cursor.execute("""
                    UPDATE globalcounter
                    SET counter_value = %s
                    WHERE counter_type = 'QR_CODE'
                """, (nuevo_valor_contador,))

                codigos_generados = []

                user_id = session.get('user_id')
                user_name = session.get('user_name', 'Sistema')
                local = session.get('active_location_name') or session.get('user_local', 'El Mekatiadero')
                hora_colombia = get_colombia_time()
                fecha_hora_str = format_datetime_for_db(hora_colombia)


                for i in range(rango1_inicio, rango1_final + 1):
                    nuevo_codigo = f"QR{i:04d}"
                    codigos_generados.append(nuevo_codigo)

                    # Insertar en la tabla qrcode
                    cursor.execute("""
                        INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (nuevo_codigo, 0, 1, 1, nombre))

                    # Registrar automáticamente en el historial
                    cursor.execute("""
                        INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))

                # Generar códigos del segundo rango (después del reinicio)
                for i in range(rango2_inicio, rango2_final + 1):
                    nuevo_codigo = f"QR{i:04d}"
                    codigos_generados.append(nuevo_codigo)

                    # Insertar en la tabla qrcode
                    cursor.execute("""
                        INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (nuevo_codigo, 0, 1, 1, nombre))

                    # Registrar automáticamente en el historial
                    cursor.execute("""
                        INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))

                connection.commit()

                logger.warning(f"Contador QR reiniciado automáticamente al generar lote grande. Generados {cantidad_qr} códigos")
                logger.info(f"Generados {cantidad_qr} códigos QR: desde QR{rango1_inicio:04d} hasta QR{rango1_final:04d} y desde QR{rango2_inicio:04d} hasta QR{rango2_final:04d} por {user_name}")

                return codigos_generados
            else:
                # Actualizar el contador normalmente
                cursor.execute("""
                    UPDATE globalcounter
                    SET counter_value = %s
                    WHERE counter_type = 'QR_CODE'
                """, (numero_final,))

        codigos_generados = []

        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Sistema')
        local = session.get('active_location_name') or session.get('user_local', 'El Mekatiadero')
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        for i in range(numero_inicial, numero_final + 1):
            nuevo_codigo = f"QR{i:04d}"
            codigos_generados.append(nuevo_codigo)

            cursor.execute("""
                INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                VALUES (%s, %s, %s, %s, %s)
            """, (nuevo_codigo, 0, 1, 1, nombre))

            cursor.execute("""
                INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre))

        connection.commit()

        logger.info(f"Generados {cantidad_qr} códigos QR: desde QR{numero_inicial:04d} hasta QR{numero_final:04d} por {user_name}")

        return codigos_generados

    except Exception as e:
        logger.error(f"Error generando códigos QR en lote: {e}")
        if connection:
            connection.rollback()
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def generar_codigos_qr_lote_con_paquete(cantidad_qr, nombre="", paquete_id=1, payment_method=None):
    """Generar múltiples códigos QR y asignar paquete desde el inicio (blindado contra duplicados)"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return []

        cursor = get_db_cursor(connection)

        cursor.execute(
            "SELECT turns, price, name FROM turnpackage WHERE id = %s",
            (paquete_id,)
        )
        paquete = cursor.fetchone()
        if not paquete:
            logger.error(f"Paquete {paquete_id} no encontrado")
            return []

        turns_paquete = paquete['turns']
        nombre_paquete = paquete['name']

        cursor.execute("""
            SELECT counter_value FROM globalcounter
            WHERE counter_type = 'QR_CODE'
            FOR UPDATE
        """)
        resultado = cursor.fetchone()

        if not resultado:
            cursor.execute("""
                INSERT INTO globalcounter (counter_type, counter_value, description)
                VALUES ('QR_CODE', 0, 'Contador para códigos QR')
            """)
            contador_bd = 0
        else:
            contador_bd = resultado['counter_value']

        cursor.execute("""
            SELECT MAX(CAST(SUBSTRING(code, 3) AS UNSIGNED)) AS max_real
            FROM qrcode
        """)
        max_real = cursor.fetchone()['max_real'] or 0

        contador_actual = max(contador_bd, max_real)

        numero_inicial = contador_actual + 1
        numero_final = contador_actual + cantidad_qr

        cursor.execute("""
            UPDATE globalcounter
            SET counter_value = %s
            WHERE counter_type = 'QR_CODE'
        """, (numero_final,))

        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Sistema')
        local = session.get('active_location_name') or session.get('user_local', 'El Mekatiadero')
        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)
        payment_method = _normalize_payment_method(payment_method)

        codigos_generados = []

        for i in range(numero_inicial, numero_final + 1):
            nuevo_codigo = f"QR{i:04d}"
            codigos_generados.append(nuevo_codigo)

            cursor.execute("""
                INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name, expiration_date)
                VALUES (%s, %s, %s, %s, %s, DATE_ADD(CURDATE(), INTERVAL 15 DAY))
            """, (nuevo_codigo, turns_paquete, 1, paquete_id, nombre))

            cursor.execute("""
                INSERT INTO userturns (qr_code_id, turns_remaining, total_turns, package_id)
                VALUES (LAST_INSERT_ID(), %s, %s, %s)
            """, (turns_paquete, turns_paquete, paquete_id))

            cursor.execute("""
                INSERT INTO qrhistory
                (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real, payment_method)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
            """, (nuevo_codigo, user_id, user_name, local, fecha_hora_str, nombre, payment_method))

        connection.commit()

        logger.info(
            f"Generados {cantidad_qr} QRs: QR{numero_inicial:04d} a QR{numero_final:04d} "
            f"con paquete {nombre_paquete} por {user_name}"
        )

        return codigos_generados

    except Exception as e:
        logger.error(f"Error generando códigos QR en lote con paquete: {e}")
        logger.error(traceback.format_exc())
        if connection:
            connection.rollback()
        return []

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_next_qr_number():
    """Obtener el próximo número de QR disponible"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return None

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT counter_value FROM globalcounter WHERE counter_type = 'QR_CODE'")
        resultado = cursor.fetchone()

        if resultado:
            return resultado['counter_value'] + 1
        else:

            return 1

    except Exception as e:
        logger.error(f"Error obteniendo próximo número QR: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def actualizar_contador_diario(fecha=None):
    """Actualizar contador diario - SOLO VENTAS REALES"""
    if fecha is None:
        fecha = get_colombia_time().strftime('%Y-%m-%d')

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return False

        cursor = get_db_cursor(connection)

        # Crear tabla si no existe
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ContadorDiario (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fecha DATE NOT NULL UNIQUE,
                qr_vendidos INT DEFAULT 0,
                valor_ventas DECIMAL(10, 2) DEFAULT 0,
                qr_escaneados INT DEFAULT 0,
                turnos_utilizados INT DEFAULT 0,
                fallas_reportadas INT DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_fecha (fecha)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # Solo contar ventas REALES
        cursor.execute("""
            INSERT INTO ContadorDiario (fecha, qr_vendidos, valor_ventas, qr_escaneados, turnos_utilizados, fallas_reportadas)
            SELECT
                %s as fecha,
                COUNT(DISTINCT CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                          AND qh.es_venta_real = TRUE  -- SOLO VENTAS REALES
                          THEN qh.qr_code END) as qr_vendidos,
                COALESCE(SUM(CASE WHEN qr.turnPackageId IS NOT NULL AND qr.turnPackageId != 1
                           AND qh.es_venta_real = TRUE  -- SOLO VENTAS REALES
                           THEN tp.price END), 0) as valor_ventas,
                COUNT(DISTINCT qh.qr_code) as qr_escaneados,
                COUNT(DISTINCT tu.id) as turnos_utilizados,
                COUNT(DISTINCT mf.id) as fallas_reportadas
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN turnusage tu ON DATE(tu.usedAt) = %s
            LEFT JOIN machinefailures mf ON DATE(mf.reported_at) = %s
            WHERE DATE(qh.fecha_hora) = %s
            ON DUPLICATE KEY UPDATE
                qr_vendidos = VALUES(qr_vendidos),
                valor_ventas = VALUES(valor_ventas),
                qr_escaneados = VALUES(qr_escaneados),
                turnos_utilizados = VALUES(turnos_utilizados),
                fallas_reportadas = VALUES(fallas_reportadas),
                updated_at = NOW()
        """, (fecha, fecha, fecha, fecha))

        connection.commit()
        logger.info(f"Contador diario actualizado para {fecha}")
        return True

    except Exception as e:
        logger.error(f"Error actualizando contador diario: {e}")
        if connection:
            connection.rollback()
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ── Routes ────────────────────────────────────────────────────────────────────

@qr_bp.route('/api/debug-generar-qr', methods=['POST'])
def debug_generar_qr():
    """Endpoint temporal para debug del generador QR"""
    try:
        data = request.get_json()
        cantidad = int(data.get('cantidad', 1))
        nombre = data.get('nombre', '')

        print(f"DEBUG: Intentando generar {cantidad} QR")

        codigos = generar_codigos_qr_lote(cantidad, nombre)

        if not codigos:
            return jsonify({
                'error': 'La función retornó lista vacía',
                'cantidad': cantidad,
                'nombre': nombre
            }), 500

        return jsonify({
            'success': True,
            'codigos': codigos,
            'cantidad': len(codigos)
        })

    except Exception as e:
        error_detail = traceback.format_exc()
        print(f"ERROR DETALLADO: {error_detail}")
        return jsonify({
            'error': str(e),
            'traceback': error_detail
        }), 500


@qr_bp.route('/api/contador-qr/estado', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_estado_contador():
    """Obtener el estado actual del contador de QR con información de reinicio"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                gc.counter_value,
                gc.description,
                gc.last_updated,
                COUNT(qc.id) as total_qr_registrados
            FROM globalcounter gc
            LEFT JOIN qrcode qc ON qc.code REGEXP '^QR[0-9]+$'
            WHERE gc.counter_type = 'QR_CODE'
        """)

        resultado = cursor.fetchone()

        if not resultado:
            return api_response('E002', http_status=404, data={'message': 'Contador no encontrado'})

        proximo_numero = resultado['counter_value'] + 1
        if proximo_numero > 9999:
            proximo_numero = 1
            proximo_codigo = f"QR{proximo_numero:04d}"
            reinicio_pendiente = True
        else:
            proximo_codigo = f"QR{proximo_numero:04d}"
            reinicio_pendiente = False

        codigos_disponibles = 9999 - resultado['counter_value']
        porcentaje_restante = (codigos_disponibles / 9999) * 100

        return api_response(
            'S001',
            status='success',
            data={
                'contador_actual': resultado['counter_value'],
                'proximo_codigo': proximo_codigo,
                'descripcion': resultado['description'],
                'ultima_actualizacion': resultado['last_updated'].isoformat() if resultado['last_updated'] else None,
                'total_qr_registrados': resultado['total_qr_registrados'],
                'limite_superior': 9999,
                'codigos_disponibles': codigos_disponibles,
                'porcentaje_restante': round(porcentaje_restante, 2),
                'reinicio_pendiente': reinicio_pendiente,
                'advertencia': reinicio_pendiente and '¡El contador se reiniciará en el próximo QR generado!'
            }
        )

    except Exception as e:
        logger.error(f"Error obteniendo estado del contador: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/contador-qr/reiniciar', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['nuevo_valor'])
def reiniciar_contador():
    """Reiniciar el contador de QR (solo administradores)"""
    try:
        data = request.get_json()
        nuevo_valor = int(data['nuevo_valor'])

        if nuevo_valor < 0 or nuevo_valor > 9999:
            return api_response('E005', http_status=400, data={'message': 'El valor debe estar entre 0 y 9999'})

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            if not connection:
                return api_response('E006', http_status=500)

            cursor = get_db_cursor(connection)

            cursor.execute("""
                UPDATE globalcounter
                SET counter_value = %s
                WHERE counter_type = 'QR_CODE'
            """, (nuevo_valor,))

            connection.commit()

            cursor.execute("SELECT counter_value FROM globalcounter WHERE counter_type = 'QR_CODE'")
            resultado = cursor.fetchone()

            logger.warning(f"Contador QR reiniciado manualmente a {nuevo_valor} por usuario {session.get('user_name')}")

            return api_response(
                'S003',
                status='success',
                data={
                    'nuevo_valor': resultado['counter_value'] if resultado else nuevo_valor,
                    'proximo_codigo': f"QR{(resultado['counter_value'] + 1 if resultado else nuevo_valor + 1):04d}",
                    'limite_superior': 9999,
                    'timestamp': get_colombia_time().isoformat()
                }
            )

        except Exception as e:
            logger.error(f"Error reiniciando contador: {e}")
            if connection:
                connection.rollback()
            return api_response('E001', http_status=500)
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    except ValueError:
        return api_response('E005', http_status=400, data={'message': 'Valor inválido'})


@qr_bp.route('/api/generar-qr', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['cantidad'])
def generar_qr():
    """Generar nuevos códigos QR con 4 cifras"""
    try:
        logger.info(f"SESSION EN generar-qr: {dict(session)}")
        data = request.get_json()
        cantidad = int(data['cantidad'])
        nombre = data.get('nombre', '')
        paquete_id = data.get('paquete_id')
        payment_method = _normalize_payment_method(data.get('payment_method'))

        if cantidad <= 0 or cantidad > 1000:
            return api_response(
                'E005',
                http_status=400,
                data={'message': 'Cantidad debe estar entre 1 y 1000'}
            )

        if cantidad > 9999:
            return api_response(
                'E005',
                http_status=400,
                data={'message': 'No se pueden generar más de 9999 códigos a la vez'}
            )

        if paquete_id and payment_method not in VALID_PAYMENT_METHODS:
            return api_response(
                'E005',
                http_status=400,
                data={'message': 'Método de pago inválido'}
            )

        if paquete_id:
            codigos_generados = generar_codigos_qr_lote_con_paquete(
                cantidad,
                nombre,
                paquete_id,
                payment_method=payment_method,
            )

            if not codigos_generados:
                return api_response('E001', http_status=500)

            connection = get_db_connection()
            if not connection:
                return api_response('E006', http_status=500)

            cursor = get_db_cursor(connection)
            cursor.execute("SELECT * FROM turnpackage WHERE id = %s", (paquete_id,))
            paquete = cursor.fetchone()
            cursor.close()
            connection.close()

            if not paquete:
                return api_response('Q004', http_status=404, data={'paquete_id': paquete_id})

            response_data = {
                'codigos': codigos_generados,
                'cantidad': len(codigos_generados),
                'nombre': nombre,
                'paquete_id': paquete_id,
                'paquete_nombre': paquete['name'],
                'paquete_precio': float(paquete['price']),
                'paquete_turnos': paquete['turns'],
                'payment_method': payment_method,
                'payment_method_label': _payment_method_label(payment_method),
                'expiration_date': (get_colombia_time() + timedelta(days=15)).strftime('%d/%m/%Y'),
                'formato': 'QRXXXX (4 dígitos, de QR0001 a QR9999)',
                'nota': 'El contador se reiniciará automáticamente al llegar a QR9999'
            }

            logger.info(f"Generados {len(codigos_generados)} códigos QR con paquete {paquete['name']}")

            return api_response(
                'S002',
                status='success',
                data=response_data
            )
        else:

            codigos_generados = generar_codigos_qr_lote(cantidad, nombre)

            if not codigos_generados:
                return api_response('E001', http_status=500)

            logger.info(f"Generados {len(codigos_generados)} códigos QR sin paquete")

            return api_response(
                'S002',
                status='success',
                data={
                    'codigos': codigos_generados,
                    'cantidad': len(codigos_generados),
                    'nombre': nombre,
                    'formato': 'QRXXXX (4 dígitos, de QR0001 a QR9999)',
                    'nota': 'El contador se reiniciará automáticamente al llegar a QR9999'
                }
            )

    except Exception as e:
        logger.error(f"Error generando QR: {e}")
        return api_response('E001', http_status=500)


@qr_bp.route('/api/obtener-siguiente-qr', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_siguiente_qr():
    """Obtener el siguiente código QR disponible con manejo de reinicio"""
    siguiente_codigo = generar_codigo_qr()

    if not siguiente_codigo:
        return api_response('E001', http_status=500)

    numero_qr = int(siguiente_codigo[2:])

    return api_response(
        'S001',
        status='success',
        data={
            'siguiente_codigo': siguiente_codigo,
            'numero_qr': numero_qr,
            'es_reinicio': numero_qr == 1,
            'mensaje': '¡Contador reiniciado!' if numero_qr == 1 else None
        }
    )


@qr_bp.route('/api/paquetes', methods=['GET'])
@handle_api_errors
def obtener_paquetes():
    """Obtener paquetes disponibles filtrados por local activo."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        active_id, _ = get_active_location()
        can_all = user_can_view_all()

        if can_all and active_id is None:
            cursor.execute("SELECT * FROM turnpackage ORDER BY id")
        else:
            eff = active_id if active_id is not None else -1
            cursor.execute(
                "SELECT * FROM turnpackage WHERE location_id = %s ORDER BY id",
                (eff,)
            )

        return jsonify(cursor.fetchall())
    except Exception as e:
        logger.error(f"Error obteniendo paquetes: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/asignar-paquete', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['codigo_qr', 'paquete_id'])
def asignar_paquete():
    """Asignar un paquete a un código QR"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        codigo_qr = data['codigo_qr']
        paquete_id = data['paquete_id']

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT turnPackageId FROM qrcode WHERE code = %s", (codigo_qr,))
        qr_existente = cursor.fetchone()

        if qr_existente and qr_existente['turnPackageId'] is not None and qr_existente['turnPackageId'] != 1:
            cursor.execute("SELECT name FROM turnpackage WHERE id = %s", (qr_existente['turnPackageId'],))
            paquete_actual = cursor.fetchone()
            paquete_nombre = paquete_actual['name'] if paquete_actual else 'Desconocido'

            return api_response(
                'Q002',
                http_status=400,
                data={
                    'paquete_actual': paquete_nombre,
                    'qr_code': codigo_qr
                }
            )

        cursor.execute("SELECT turns, price FROM turnpackage WHERE id = %s", (paquete_id,))
        paquete = cursor.fetchone()
        if not paquete:
            return api_response('Q004', http_status=404)

        turns, price = paquete['turns'], paquete['price']

        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (codigo_qr,))
        qr_existente = cursor.fetchone()

        if not qr_existente:
            cursor.execute("""
                INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId)
                VALUES (%s, %s, 1, %s)
            """, (codigo_qr, turns, paquete_id))
            connection.commit()
            qr_id = cursor.lastrowid
        else:
            qr_id = qr_existente['id']
            cursor.execute("""
                UPDATE qrcode
                SET remainingTurns = remainingTurns + %s,
                    turnPackageId = %s
                WHERE id = %s
            """, (turns, paquete_id, qr_id))
            connection.commit()

        cursor.execute("""
            INSERT INTO userturns (qr_code_id, turns_remaining, total_turns, package_id)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                turns_remaining = turns_remaining + %s,
                total_turns = total_turns + %s,
                package_id = %s
        """, (qr_id, turns, turns, paquete_id, turns, turns, paquete_id))

        connection.commit()

        logger.info(f"Paquete {paquete_id} asignado a QR {codigo_qr}")

        return api_response(
            'S002',
            status='success',
            data={
                'turns': turns,
                'price': price,
                'qr_id': qr_id,
                'paquete_id': paquete_id
            }
        )

    except Exception as e:
        logger.error(f"Error asignando paquete: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/verificar-qr/<qr_code>', methods=['GET'])
@handle_api_errors
def verificar_qr(qr_code):
    """Verificar información de un código QR"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT id, code, remainingTurns, isActive, turnPackageId FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()

        if not qr_data:
            return api_response('Q001', http_status=404, data={'qr_code': qr_code})

        qr_id = qr_data['id']
        tiene_paquete = qr_data['turnPackageId'] is not None and qr_data['turnPackageId'] != 1

        cursor.execute("""
            SELECT ut.*, tp.name as package_name, tp.turns, tp.price
            FROM userturns ut
            LEFT JOIN turnpackage tp ON ut.package_id = tp.id
            WHERE ut.qr_code_id = %s
        """, (qr_id,))
        resultado = cursor.fetchone()

        response_data = {
            'existe': True,
            'tiene_paquete': tiene_paquete,
            'qr_code': qr_code,
            'turnPackageId': qr_data['turnPackageId']
        }

        if resultado:
            response_data.update({
                'turns_remaining': resultado['turns_remaining'],
                'total_turns': resultado['total_turns'],
                'package_name': resultado['package_name'],
                'package_turns': resultado['turns'],
                'package_price': resultado['price']
            })
        else:
            response_data.update({
                'turns_remaining': 0,
                'total_turns': 0,
                'package_name': 'Sin paquete',
                'package_turns': 0,
                'package_price': 0
            })

        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error verificando QR: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/registrar-uso', methods=['POST'])
@handle_api_errors
@validate_required_fields(['qr_code', 'machine_id'])
def registrar_uso():
    """Registrar uso de un turno"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        machine_id = data['machine_id']

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return api_response('Q001', http_status=404)

        qr_id = qr_data['id']
        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()

        if not turnos_data or turnos_data['turns_remaining'] <= 0:
            return api_response('Q003', http_status=400)

        station_index = data.get('station_index', None)

        turns_after = turnos_data['turns_remaining'] - 1

        # Insertar turno usado (con station_index y turns_remaining_after si las columnas existen)
        try:
            cursor.execute(
                "INSERT INTO turnusage (qrCodeId, machineId, station_index, turns_remaining_after) VALUES (%s, %s, %s, %s)",
                (qr_id, machine_id, station_index, turns_after)
            )
        except Exception:
            try:
                cursor.execute(
                    "INSERT INTO turnusage (qrCodeId, machineId, station_index) VALUES (%s, %s, %s)",
                    (qr_id, machine_id, station_index)
                )
            except Exception:
                cursor.execute("INSERT INTO turnusage (qrCodeId, machineId) VALUES (%s, %s)", (qr_id, machine_id))

        cursor.execute("UPDATE userturns SET turns_remaining = turns_remaining - 1 WHERE qr_code_id = %s", (qr_id,))

        # Resetear contador de fallas consecutivas para esta estación (juego exitoso = contador a 0)
        station_key = str(station_index) if station_index is not None else 'all'
        try:
            cursor.execute("SELECT consecutive_failures FROM machine WHERE id = %s", (machine_id,))
            maq = cursor.fetchone()
            if maq:
                contadores = json.loads(maq['consecutive_failures'] or '{}')
                if contadores.get(station_key, 0) > 0:
                    contadores[station_key] = 0
                    cursor.execute(
                        "UPDATE machine SET consecutive_failures = %s WHERE id = %s",
                        (json.dumps(contadores), machine_id)
                    )
        except Exception as e:
            logger.warning(f"No se pudo resetear consecutive_failures: {e}")

        connection.commit()

        logger.info(f"Turno usado - QR: {qr_code}, Máquina: {machine_id}, Estación: {station_index}")

        return api_response(
            'S010',
            status='success',
            data={
                'turns_remaining': turnos_data['turns_remaining'] - 1
            }
        )

    except Exception as e:
        logger.error(f"Error registrando uso: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/reportar-falla', methods=['POST'])
@handle_api_errors
@validate_required_fields(['qr_code', 'turnos_devueltos'])
def reportar_falla():
    """Reportar falla desde ESP32: devuelve turnos, cuenta fallas consecutivas y actualiza estado."""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code          = data['qr_code']
        machine_id       = data.get('machine_id', 0)
        machine_name     = data.get('machine_name', 'Sistema')
        turnos_devueltos = data['turnos_devueltos']
        is_forced        = data.get('is_forced', False)
        forced_by        = data.get('forced_by', '')
        notes            = data.get('notes', '')
        station_index    = data.get('station_index', None)   # nuevo: índice de estación

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        # ── Verificar QR ──────────────────────────────────────────────────────
        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return api_response('Q001', http_status=404)
        qr_id = qr_data['id']

        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()
        if not turnos_data:
            return api_response('Q003', http_status=400)

        turnos_originales = turnos_data['turns_remaining']
        nuevos_turnos     = turnos_originales + turnos_devueltos
        actual_machine_id   = None if machine_id == 0 else machine_id
        actual_machine_name = 'Devolución Manual' if machine_id == 0 else machine_name

        # ── Insertar en machinefailures ───────────────────────────────────────
        cursor.execute("DESCRIBE machinefailures")
        columnas_existentes = [col['Field'] for col in cursor.fetchall()]

        campos = ['qr_code_id', 'machine_name', 'turnos_devueltos']
        valores = [qr_id, actual_machine_name, turnos_devueltos]
        if 'machine_id'    in columnas_existentes: campos.append('machine_id');    valores.append(actual_machine_id)
        if 'notes'         in columnas_existentes: campos.append('notes');         valores.append(notes or None)
        if 'is_forced'     in columnas_existentes: campos.append('is_forced');     valores.append(1 if is_forced else 0)
        if 'forced_by'     in columnas_existentes: campos.append('forced_by');     valores.append(forced_by or None)
        if 'station_index' in columnas_existentes: campos.append('station_index'); valores.append(station_index)

        placeholders = ', '.join(['%s'] * len(campos))
        cursor.execute(f"INSERT INTO machinefailures ({', '.join(campos)}) VALUES ({placeholders})", valores)

        # ── Devolver turnos (SIEMPRE, incluyendo la 3ª falla) ────────────────
        cursor.execute("UPDATE userturns SET turns_remaining = %s WHERE qr_code_id = %s",
                       (nuevos_turnos, qr_id))

        # ── Contar fallas consecutivas y actualizar estado de máquina ─────────
        fallas_consecutivas = 0
        station_en_mantenimiento = False
        if actual_machine_id:
            # Clave de estación en el JSON de la máquina
            station_key = str(station_index) if station_index is not None else 'all'

            # Leer contadores actuales
            cursor.execute(
                "SELECT consecutive_failures, stations_in_maintenance, machine_subtype "
                "FROM machine WHERE id = %s",
                (actual_machine_id,)
            )
            maq = cursor.fetchone()
            if maq:
                try:
                    contadores = json.loads(maq['consecutive_failures'] or '{}')
                except Exception:
                    contadores = {}
                try:
                    en_mant = json.loads(maq['stations_in_maintenance'] or '[]')
                except Exception:
                    en_mant = []
                machine_subtype = maq.get('machine_subtype', 'simple') or 'simple'

                # Incrementar contador de esta estación
                contadores[station_key] = contadores.get(station_key, 0) + 1
                fallas_consecutivas = contadores[station_key]

                updates = {"consecutive_failures": json.dumps(contadores)}

                if fallas_consecutivas >= 3:
                    # Marcar estación como en mantenimiento
                    station_en_mantenimiento = True
                    if station_key not in [str(s) for s in en_mant]:
                        en_mant.append(station_index if station_index is not None else 'all')
                    updates["stations_in_maintenance"] = json.dumps(en_mant)
                    updates["errorNote"] = f"Falla estación {station_key} — 3 fallos consecutivos"

                    # Determinar si toda la máquina queda en mantenimiento
                    if machine_subtype == 'multi_station':
                        # Obtener cuántas estaciones tiene la máquina
                        cursor.execute(
                            "SELECT JSON_LENGTH(station_names) as n_stations FROM machine WHERE id = %s",
                            (actual_machine_id,)
                        )
                        row = cursor.fetchone()
                        n_stations = row['n_stations'] if row and row['n_stations'] else 2
                        # Verificar cuántas estaciones distintas están en mantenimiento
                        stations_bloqueadas = set()
                        for s in en_mant:
                            stations_bloqueadas.add(str(s))
                        if len(stations_bloqueadas) >= n_stations:
                            updates["status"] = "mantenimiento"
                        # Si solo una está en mantenimiento, la máquina sigue activa
                        # pero la estación individual ya está marcada
                    else:
                        # Máquina simple → toda la máquina pasa a mantenimiento
                        updates["status"] = "mantenimiento"

                    # Encolar MAINTENANCE al ESP32
                    try:
                        cursor.execute("""
                            INSERT INTO esp32_commands
                            (machine_id, command, parameters, triggered_by, status, triggered_at)
                            VALUES (%s, 'MAINTENANCE', %s, 'auto_falla_esp32', 'queued', NOW())
                        """, (actual_machine_id, json.dumps({
                            'station_index': station_index,
                            'station_key': station_key,
                            'fallas_consecutivas': fallas_consecutivas
                        })))
                    except Exception as cmd_err:
                        logger.error(f"No se pudo encolar MAINTENANCE: {cmd_err}")

                # Aplicar updates a machine
                set_parts  = [f"{k} = %s" for k in updates]
                set_values = list(updates.values()) + [actual_machine_id]
                cursor.execute(
                    f"UPDATE machine SET {', '.join(set_parts)} WHERE id = %s",
                    set_values
                )

        if is_forced:
            try:
                cursor.execute("""
                    INSERT INTO error_logs
                    (error_type, error_message, module, user_id, request_path)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    'REFUND_FORCED',
                    f'Devolución forzada: QR={qr_code}, Turnos={turnos_devueltos}, Por={forced_by}',
                    'packfailure', session.get('user_id'), '/api/reportar-falla'
                ))
            except Exception as e:
                logger.error(f"Error registrando en error_logs: {e}")

        connection.commit()

        logger.info(
            f"✅ Falla reportada — QR={qr_code} maquina={actual_machine_id} "
            f"estacion={station_index} consecutivas={fallas_consecutivas} "
            f"turnos_devueltos={turnos_devueltos}"
        )

        return api_response(
            'S003',
            status='success',
            data={
                'nuevos_turnos': nuevos_turnos,
                'is_forced': is_forced,
                'machine_id': actual_machine_id,
                'qr_code': qr_code,
                'turnos_originales': turnos_originales,
                'turnos_devueltos': turnos_devueltos,
                'fallas_consecutivas': fallas_consecutivas,
                'station_en_mantenimiento': station_en_mantenimiento,
            }
        )

    except mysql.connector.Error as e:
        logger.error(f"Error MySQL reportando falla: {e}")
        if connection:
            connection.rollback()

        try:
            logger.info("Intentando inserción mínima...")
            cursor.execute("""
                INSERT INTO machinefailures (qr_code_id, machine_name, turnos_devueltos)
                VALUES (%s, %s, %s)
            """, (qr_id, 'Sistema', turnos_devueltos))

            cursor.execute("UPDATE userturns SET turns_remaining = turns_remaining + %s WHERE qr_code_id = %s",
                           (turnos_devueltos, qr_id))

            connection.commit()

            return api_response(
                'S003',
                status='success',
                data={
                    'nuevos_turnos': turnos_originales + turnos_devueltos,
                    'is_forced': is_forced,
                    'machine_id': None,
                    'qr_code': qr_code,
                    'note': 'Inserción mínima exitosa'
                }
            )
        except Exception as retry_error:
            logger.error(f"Error en inserción mínima: {retry_error}")
            return api_response('E001', http_status=500, data={'mysql_error': str(e)})

    except Exception as e:
        logger.error(f"Error reportando falla: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/historial-fallas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_historial_fallas():
    """Obtener historial de fallas"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT mf.*, qr.code as qr_code, ut.turns_remaining, ut.total_turns
            FROM machinefailures mf
            JOIN qrcode qr ON mf.qr_code_id = qr.id
            JOIN userturns ut ON mf.qr_code_id = ut.qr_code_id
            ORDER BY mf.reported_at DESC
            LIMIT 50
        """)
        return jsonify(cursor.fetchall())
    except Exception as e:
        logger.error(f"Error obteniendo historial de fallas: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/guardar-qr', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_code'])
def guardar_qr():
    """Guardar QR en historial - CONSULTA o VENTA"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('active_location_name') or session.get('user_local', 'El Mekatiadero')
        es_venta_real = data.get('es_venta_real', False)
        es_consulta = data.get('es_consulta', False)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        cursor.execute("SELECT qr_name, turnPackageId FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        qr_name = qr_data['qr_name'] if qr_data and 'qr_name' in qr_data else None


        tiene_paquete = qr_data and qr_data['turnPackageId'] is not None and qr_data['turnPackageId'] != 1

        es_venta = False
        if es_venta_real and not es_consulta and tiene_paquete:
            es_venta = True

        cursor.execute("""
            INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (qr_code, user_id, user_name, local, fecha_hora_str, qr_name, es_venta_real))

        connection.commit()

        if es_venta:
            actualizar_contador_diario(hora_colombia.strftime('%Y-%m-%d'))
            logger.info(f"VENTA REAL registrada: {qr_code} por {user_name}")
            mensaje = "Venta registrada"
        else:

            logger.info(f"CONSULTA registrada: {qr_code} por {user_name}")
            mensaje = "Consulta registrada"

        return api_response(
            'S006',
            status='success',
            data={
                'qr_name': qr_name,
                'es_venta': es_venta,
                'es_venta_real': es_venta_real,
                'es_consulta': es_consulta,
                'tiene_paquete': tiene_paquete,
                'mensaje': mensaje,
                'timestamp': hora_colombia.strftime('%Y-%m-%d %H:%M:%S')
            }
        )
    except Exception as e:
        logger.error(f"Error guardando QR en historial: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/verificar-venta-existente/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def verificar_venta_existente(qr_code):
    """Verificar si ya existe una venta real para este QR"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                COUNT(*) as existe_venta,
                MAX(fecha_hora) as ultima_venta_fecha,
                COUNT(*) as total_ventas
            FROM qrhistory
            WHERE qr_code = %s
            AND es_venta_real = TRUE
        """, (qr_code,))

        venta_info = cursor.fetchone()

        cursor.execute("SELECT qr_name, turnPackageId FROM qrcode WHERE code = %s", (qr_code,))
        qr_info = cursor.fetchone()

        existe_venta = venta_info['existe_venta'] > 0

        return jsonify({
            'existe_venta': existe_venta,
            'total_ventas': venta_info['total_ventas'] or 0,
            'ultima_venta_fecha': venta_info['ultima_venta_fecha'].isoformat() if venta_info['ultima_venta_fecha'] else None,
            'qr_tiene_paquete': qr_info and qr_info['turnPackageId'] is not None and qr_info['turnPackageId'] != 1,
            'qr_nombre': qr_info['qr_name'] if qr_info and 'qr_name' in qr_info else None
        })

    except Exception as e:
        logger.error(f"Error verificando venta existente: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/guardar-multiples-qr-con-paquete', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_codes', 'paquete_id'])
def guardar_multiples_qr_con_paquete():
    """Guardar múltiples QR con paquete como VENTAS REALES"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_codes = data['qr_codes']
        nombre = data.get('nombre', '')
        paquete_id = data['paquete_id']
        paquete_nombre = data.get('paquete_nombre', '')
        paquete_precio = data.get('paquete_precio', 0)
        paquete_turns = data.get('paquete_turns', 0)
        es_venta_real = data.get('es_venta_real', True)

        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('active_location_name') or session.get('user_local', 'El Mekatiadero')

        if not qr_codes:
            return api_response('E005', http_status=400, data={'message': 'Lista de QR vacía'})

        logger.info(f"Guardando {len(qr_codes)} QR con paquete {paquete_nombre}")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        qrs_creados = 0
        qrs_actualizados = 0

        for qr_code in qr_codes:
            cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
            qr_existente = cursor.fetchone()

            if not qr_existente:
                cursor.execute("""
                    INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                    VALUES (%s, %s, %s, %s, %s)
                """, (qr_code, paquete_turns, 1, paquete_id, nombre))

                qr_id = cursor.lastrowid

                cursor.execute("""
                    INSERT INTO userturns (qr_code_id, turns_remaining, total_turns, package_id)
                    VALUES (%s, %s, %s, %s)
                """, (qr_id, paquete_turns, paquete_turns, paquete_id))

                qrs_creados += 1
            else:
                qr_id = qr_existente['id']

                cursor.execute("SELECT turnPackageId FROM qrcode WHERE id = %s", (qr_id,))
                qr_info = cursor.fetchone()

                if qr_info['turnPackageId'] is not None and qr_info['turnPackageId'] != 1:
                    continue

                cursor.execute("""
                    UPDATE qrcode
                    SET remainingTurns = %s, turnPackageId = %s, qr_name = %s
                    WHERE id = %s
                """, (paquete_turns, paquete_id, nombre, qr_id))

                cursor.execute("SELECT id FROM userturns WHERE qr_code_id = %s", (qr_id,))
                user_turns_existente = cursor.fetchone()

                if user_turns_existente:
                    cursor.execute("""
                        UPDATE userturns
                        SET turns_remaining = %s, total_turns = %s, package_id = %s
                        WHERE qr_code_id = %s
                    """, (paquete_turns, paquete_turns, paquete_id, qr_id))
                else:
                    cursor.execute("""
                        INSERT INTO userturns (qr_code_id, turns_remaining, total_turns, package_id)
                        VALUES (%s, %s, %s, %s)
                    """, (qr_id, paquete_turns, paquete_turns, paquete_id))

                qrs_actualizados += 1

            cursor.execute("""
                INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (qr_code, user_id, user_name, local, fecha_hora_str, nombre, es_venta_real))

        connection.commit()

        if es_venta_real and qr_codes:
            actualizar_contador_diario(hora_colombia.strftime('%Y-%m-%d'))

        total_qrs = qrs_creados + qrs_actualizados

        logger.info(f"{total_qrs} QR generados como VENTAS REALES con paquete {paquete_nombre}")

        return api_response(
            'S002',
            status='success',
            data={
                'count': total_qrs,
                'nombre': nombre,
                'paquete': paquete_nombre,
                'precio': paquete_precio,
                'turns': paquete_turns,
                'creados': qrs_creados,
                'actualizados': qrs_actualizados,
                'es_venta_real': es_venta_real,
                'mensaje': f'{total_qrs} QR registrados como VENTAS REALES'
            }
        )

    except Exception as e:
        logger.error(f"Error guardando múltiples QR con paquete: {e}")
        sentry_sdk.capture_exception(e)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/guardar-multiples-qr', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_codes'])
def guardar_multiples_qr():
    """Agregar QR generados en lote al historial"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_codes = data['qr_codes']
        nombre = data.get('nombre', '')
        es_venta_real = data.get('es_venta_real', False)

        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('active_location_name') or session.get('user_local', 'El Mekatiadero')

        logger.info(f"Guardando {len(qr_codes)} QR con nombre: {nombre}")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        hora_colombia = get_colombia_time()
        fecha_hora_str = format_datetime_for_db(hora_colombia)

        for qr_code in qr_codes:
            cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
            qr_existente = cursor.fetchone()

            if not qr_existente:
                cursor.execute("""
                    INSERT INTO qrcode (code, remainingTurns, isActive, turnPackageId, qr_name)
                    VALUES (%s, %s, %s, %s, %s)
                """, (qr_code, 0, 1, 1, nombre))
            else:
                cursor.execute("""
                    UPDATE qrcode SET qr_name = %s WHERE code = %s
                """, (nombre, qr_code))

            cursor.execute("""
                INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (qr_code, user_id, user_name, local, fecha_hora_str, nombre, es_venta_real))

        connection.commit()

        if es_venta_real and qr_codes:
            actualizar_contador_diario(hora_colombia.strftime('%Y-%m-%d'))

        logger.info(f"{len(qr_codes)} QR guardados con nombre: {nombre}")

        return api_response(
            'S002',
            status='success',
            data={
                'count': len(qr_codes),
                'nombre': nombre,
                'es_venta_real': es_venta_real
            }
        )

    except Exception as e:
        logger.error(f"Error guardando múltiples QR: {e}")
        sentry_sdk.capture_exception(e)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/estadisticas/tiempo-real', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_estadisticas_tiempo_real():
    """Obtener estadísticas en tiempo real (sin cache)"""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT COUNT(DISTINCT qh.qr_code) as vendidos_hoy,
                   COALESCE(SUM(tp.price), 0) as valor_hoy
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
        """, (fecha,))

        ventas = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*) as escaneados_hoy
            FROM qrhistory
            WHERE DATE(fecha_hora) = %s
        """, (fecha,))

        escaneados = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*) as turnos_hoy
            FROM turnusage
            WHERE DATE(usedAt) = %s
        """, (fecha,))

        turnos = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*) as qr_generados_hoy
            FROM qrcode
            WHERE DATE(createdAt) = %s
        """, (fecha,))

        generados = cursor.fetchone()

        cursor.execute("SELECT counter_value FROM globalcounter WHERE counter_type = 'QR_CODE'")
        contador_qr = cursor.fetchone()

        return jsonify({
            'fecha': fecha,
            'ventas': {
                'vendidos': ventas['vendidos_hoy'] or 0,
                'valor': float(ventas['valor_hoy'] or 0)
            },
            'escaneados': escaneados['escaneados_hoy'] or 0,
            'turnos': turnos['turnos_hoy'] or 0,
            'generados': generados['qr_generados_hoy'] or 0,
            'contador_qr_actual': contador_qr['counter_value'] if contador_qr else 0,
            'proximo_qr': f"QR{(contador_qr['counter_value'] + 1 if contador_qr else 1):04d}",
            'timestamp': get_colombia_time().isoformat()
        })

    except Exception as e:
        logger.error(f"Error obteniendo estadísticas tiempo real: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ==================== APIS PARA HISTORIAL ====================

@qr_bp.route('/api/historial-completo', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_historial_completo():
    """Obtener historial completo de QR escaneados"""
    connection = None
    cursor = None
    try:
        user_id = session.get('user_id')
        local = session.get('active_location_name') or session.get('user_local', 'El Mekatiadero')

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        if session.get('user_role') == 'admin':
            cursor.execute("""
                SELECT
                    h.id,
                    h.qr_code,
                    h.user_name,
                    h.qr_name,
                    h.fecha_hora,
                    h.payment_method,
                    qr.turnPackageId,
                    tp.name as package_name,
                    tp.price as precio_paquete,
                    ut.turns_remaining
                FROM qrhistory h
                LEFT JOIN qrcode qr ON qr.code = h.qr_code
                LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
                LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
               WHERE h.local = %s
               AND h.es_venta_real = TRUE
               ORDER BY h.fecha_hora DESC
            LIMIT 100
            """, (local,))
        else:
            cursor.execute("""
                SELECT
                    h.id,
                    h.qr_code,
                    h.user_name,
                    h.qr_name,
                    h.fecha_hora,
                    h.payment_method,
                    qr.turnPackageId,
                    tp.name as package_name,
                    tp.price as precio_paquete,
                    ut.turns_remaining
                FROM qrhistory h
                LEFT JOIN qrcode qr ON qr.code = h.qr_code
                LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
                LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
                WHERE (h.user_id = %s OR h.local = %s)
                AND h.es_venta_real = TRUE
                ORDER BY h.fecha_hora DESC
                LIMIT 50
            """, (user_id, local))

        historial = cursor.fetchall()

        for item in historial:
            if item['fecha_hora']:
                try:
                    fecha_colombia = parse_db_datetime(item['fecha_hora'])
                    item['fecha_hora'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    logger.warning(f"Error formateando fecha: {e}")
                    item['fecha_hora'] = str(item['fecha_hora'])

            item['es_venta'] = item['turnPackageId'] is not None and item['turnPackageId'] != 1
            item['payment_method_label'] = _payment_method_label(item.get('payment_method'))

        logger.info(f"Historial obtenido: {len(historial)} registros")
        return jsonify(historial)

    except Exception as e:
        logger.error(f"Error obteniendo historial completo: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/historial-qr/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_historial_qr(qr_code):
    """Obtener historial específico de un código QR"""
    connection = None
    cursor = None
    try:
        logger.info(f"Obteniendo historial para QR: {qr_code}")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        active_id, active_name = get_active_location()
        can_all = user_can_view_all()
        local_actual = active_name or session.get('user_local', 'El Mekatiadero')

        if can_all and active_id is None:
            loc_clause = ""
            qr_params = (qr_code,)
        else:
            loc_clause = "AND h.local = %s"
            qr_params = (qr_code, local_actual)

        cursor.execute(f"""
            SELECT
                h.id,
                h.qr_code,
                h.user_name,
                h.qr_name,
                h.fecha_hora,
                qr.turnPackageId,
                tp.name as package_name,
                tp.price as precio_paquete,
                ut.turns_remaining
            FROM qrhistory h
            LEFT JOIN qrcode qr ON qr.code = h.qr_code
            LEFT JOIN userturns ut ON ut.qr_code_id = qr.id
            LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
            WHERE h.qr_code = %s
            {loc_clause}
            ORDER BY h.fecha_hora DESC
            LIMIT 20
        """, qr_params)

        historial = cursor.fetchall()

        for item in historial:
            if item['fecha_hora']:
                try:
                    fecha_colombia = parse_db_datetime(item['fecha_hora'])
                    item['fecha_hora'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    logger.warning(f"Error formateando fecha: {e}")
                    item['fecha_hora'] = str(item['fecha_hora'])

            item['es_venta'] = item['turnPackageId'] is not None and item['turnPackageId'] != 1

        logger.info(f"Historial obtenido para {qr_code}: {len(historial)} registros")

        if not historial:
            return api_response('I001', status='info', data={
                'message': 'No hay historial para este QR',
                'qr_code': qr_code
            })

        return jsonify(historial)

    except Exception as e:
        logger.error(f"Error obteniendo historial del QR: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ==================== APIS PARA VENTAS ====================

@qr_bp.route('/api/registrar-venta', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_code', 'paquete_id', 'payment_method'])
def registrar_venta():
    """Registrar una venta REAL"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        paquete_id = data['paquete_id']
        precio = data.get('precio')
        payment_method = _normalize_payment_method(data.get('payment_method'))

        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        local = session.get('active_location_name') or session.get('user_local', 'El Mekatiadero')

        logger.info(f"REGISTRANDO VENTA REAL: QR={qr_code}, Paquete={paquete_id}")

        if payment_method not in VALID_PAYMENT_METHODS:
            return api_response('E005', http_status=400, data={'message': 'Método de pago inválido'})

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        hora_colombia = get_colombia_time()

        cursor.execute("""
            INSERT INTO qrhistory (qr_code, user_id, user_name, local, fecha_hora, qr_name, es_venta_real, payment_method)
            VALUES (%s, %s, %s, %s, %s,
                    (SELECT qr_name FROM qrcode WHERE code = %s LIMIT 1),
                    TRUE, %s)
        """, (qr_code, user_id, user_name, local, format_datetime_for_db(hora_colombia), qr_code, payment_method))

        connection.commit()

        return api_response(
            'S007',
            status='success',
            data={
                'timestamp': hora_colombia.strftime('%Y-%m-%d %H:%M:%S'),
                'payment_method': payment_method,
                'payment_method_label': _payment_method_label(payment_method),
            }
        )

    except Exception as e:
        logger.error(f"Error registrando venta: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/ventas-dia', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def ventas_dia():
    """Obtener VENTAS REALES del día (solo donde es_venta_real = TRUE)"""
    connection = None
    cursor = None
    try:
        fecha = request.args.get('fecha', get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        active_id, active_name = get_active_location()
        can_all = user_can_view_all()
        local_actual = active_name or session.get('user_local', 'El Mekatiadero')

        if can_all and active_id is None:
            loc_clause = ""
            loc_params = [fecha]
        else:
            loc_clause = "AND qh.local = %s"
            loc_params = [fecha, local_actual]

        cursor.execute(f"""
            SELECT
                COUNT(DISTINCT qh.qr_code) as total_ventas,
                COALESCE(SUM(tp.price), 0) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            {loc_clause}
        """, tuple(loc_params))

        resultado = cursor.fetchone()

        cursor.execute(f"""
            SELECT
                COALESCE(NULLIF(qh.payment_method, ''), 'sin_registrar') as payment_method,
                COUNT(DISTINCT qh.qr_code) as total_ventas,
                COALESCE(SUM(tp.price), 0) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) = %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            {loc_clause}
            GROUP BY COALESCE(NULLIF(qh.payment_method, ''), 'sin_registrar')
            ORDER BY valor_total DESC, total_ventas DESC
        """, tuple(loc_params))

        resumen_metodos = []
        for row in cursor.fetchall():
            payment_method = row['payment_method']
            resumen_metodos.append({
                'payment_method': payment_method,
                'label': _payment_method_label(payment_method),
                'total_ventas': int(row['total_ventas'] or 0),
                'valor_total': float(row['valor_total'] or 0),
            })

        logger.info(f"Ventas REALES del día {fecha}: {resultado['total_ventas']} ventas")

        return jsonify({
            'total_ventas': resultado['total_ventas'] or 0,
            'valor_total': float(resultado['valor_total'] or 0),
            'fecha': fecha,
            'por_metodo': resumen_metodos,
        })
    except Exception as e:
        logger.error(f"Error obteniendo ventas del día: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/ventas/<int:venta_id>/payment-method', methods=['PUT'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['payment_method', 'reason'])
def actualizar_metodo_pago_venta(venta_id):
    """Actualizar método de pago de una venta real con auditoría obligatoria."""
    connection = None
    cursor = None
    try:
        data = request.get_json() or {}
        nuevo_metodo = _normalize_payment_method(data.get('payment_method'))
        motivo = (data.get('reason') or '').strip()

        if nuevo_metodo not in VALID_PAYMENT_METHODS:
            return api_response('E005', http_status=400, data={'message': 'Método de pago inválido'})

        if len(motivo) < 5:
            return api_response(
                'E005',
                http_status=400,
                data={'message': 'Debes indicar un motivo de al menos 5 caracteres'}
            )

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        active_id, active_name = get_active_location()
        can_all = user_can_view_all()
        local_actual = active_name or session.get('user_local', 'El Mekatiadero')
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Usuario')
        now_db = format_datetime_for_db(get_colombia_time())

        if can_all and active_id is None:
            loc_clause = ""
            params = [venta_id]
        else:
            loc_clause = "AND qh.local = %s"
            params = [venta_id, local_actual]

        cursor.execute(f"""
            SELECT
                qh.id,
                qh.qr_code,
                qh.qr_name,
                qh.local,
                qh.user_name,
                qh.payment_method,
                qh.payment_method_updated_at,
                qh.payment_method_updated_by,
                qh.payment_method_update_reason,
                tp.name AS package_name,
                tp.price AS precio_paquete
            FROM qrhistory qh
            LEFT JOIN qrcode qr ON qr.code = qh.qr_code
            LEFT JOIN turnpackage tp ON tp.id = qr.turnPackageId
            WHERE qh.id = %s
              AND qh.es_venta_real = TRUE
              {loc_clause}
            LIMIT 1
        """, tuple(params))

        venta = cursor.fetchone()
        if not venta:
            return api_response(
                'I001',
                status='info',
                http_status=404,
                data={'message': 'La venta no existe o no pertenece al alcance actual'}
            )

        metodo_anterior = _normalize_payment_method(venta.get('payment_method'))
        if metodo_anterior == nuevo_metodo:
            return api_response(
                'E005',
                http_status=400,
                data={'message': 'El nuevo método debe ser diferente al actual'}
            )

        cursor.execute(
            """
            UPDATE qrhistory
            SET payment_method = %s,
                payment_method_updated_at = %s,
                payment_method_updated_by = %s,
                payment_method_update_reason = %s
            WHERE id = %s
            """,
            (nuevo_metodo, now_db, user_id, motivo, venta_id),
        )

        connection.commit()

        log_transaccion(
            tipo='editar_metodo_pago',
            categoria='financiero',
            descripcion=(
                f"Método de pago actualizado para venta {venta['qr_code']} "
                f"de {_payment_method_label(metodo_anterior)} a {_payment_method_label(nuevo_metodo)}"
            ),
            usuario=user_name,
            usuario_id=user_id,
            entidad='qrhistory',
            entidad_id=venta_id,
            monto=float(venta.get('precio_paquete') or 0),
            datos_extra={
                'qr_code': venta['qr_code'],
                'local': venta.get('local'),
                'motivo': motivo,
                'valor_anterior': metodo_anterior,
                'valor_nuevo': nuevo_metodo,
                'vendedor_original': venta.get('user_name'),
            },
        )

        return jsonify({
            'success': True,
            'venta': {
                'id': venta_id,
                'qr_code': venta['qr_code'],
                'qr_nombre': venta.get('qr_name') or 'Sin nombre',
                'paquete': venta.get('package_name'),
                'precio': float(venta.get('precio_paquete') or 0),
                'vendedor': venta.get('user_name'),
                'valor_anterior': metodo_anterior or 'sin_registrar',
                'valor_anterior_label': _payment_method_label(metodo_anterior),
                'valor_nuevo': nuevo_metodo,
                'valor_nuevo_label': _payment_method_label(nuevo_metodo),
                'motivo': motivo,
                'responsable': user_name,
                'updated_at': parse_db_datetime(now_db).strftime('%Y-%m-%d %H:%M:%S'),
            }
        })

    except Exception as e:
        logger.error(f"Error actualizando método de pago: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/ventas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_ventas():
    """Obtener ventas con datos completos para el panel de ventas"""
    connection = None
    cursor = None
    try:
        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        active_id, active_name = get_active_location()
        can_all = user_can_view_all()
        local_actual = active_name or session.get('user_local', 'El Mekatiadero')

        if can_all and active_id is None:
            loc_clause = ""
            loc_params = []
        else:
            loc_clause = "AND qh.local = %s"
            loc_params = [local_actual]

        cursor.execute(f"""
            SELECT
                qh.id,
                DATE(qh.fecha_hora) as fecha,
                TIME(qh.fecha_hora) as hora,
                qh.qr_code,
                qh.qr_name,
                qh.payment_method,
                qh.payment_method_updated_at,
                qh.payment_method_updated_by,
                qh.payment_method_update_reason,
                COALESCE(NULLIF(u.name, ''), '') AS payment_method_updated_by_name,
                tp.name as paquete,
                tp.price as precio,
                tp.turns as turnos,
                qh.user_name as vendedor,
                'Completada' as estado
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN users u ON u.id = qh.payment_method_updated_by
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            {loc_clause}
            ORDER BY qh.fecha_hora DESC
        """, (fecha_inicio, fecha_fin, *loc_params))

        ventas = cursor.fetchall()

        cursor.execute(f"""
            SELECT
                COUNT(DISTINCT qh.qr_code) as total_paquetes,
                COALESCE(SUM(tp.price), 0) as total_ventas,
                CASE
                    WHEN COUNT(DISTINCT qh.qr_code) > 0 THEN
                        COALESCE(SUM(tp.price), 0) / COUNT(DISTINCT qh.qr_code)
                    ELSE 0
                END as ticket_promedio
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            {loc_clause}
        """, (fecha_inicio, fecha_fin, *loc_params))

        estadisticas_data = cursor.fetchone()

        cursor.execute(f"""
            SELECT
                tp.name as paquete,
                COUNT(DISTINCT qh.qr_code) as cantidad
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            {loc_clause}
            GROUP BY tp.id, tp.name
            ORDER BY cantidad DESC
        """, (fecha_inicio, fecha_fin, *loc_params))

        ventas_por_paquete = cursor.fetchall()

        cursor.execute(f"""
            SELECT
                COALESCE(NULLIF(qh.payment_method, ''), 'sin_registrar') as payment_method,
                COUNT(DISTINCT qh.qr_code) as total_ventas,
                COALESCE(SUM(tp.price), 0) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            {loc_clause}
            GROUP BY COALESCE(NULLIF(qh.payment_method, ''), 'sin_registrar')
            ORDER BY valor_total DESC, total_ventas DESC
        """, (fecha_inicio, fecha_fin, *loc_params))

        resumen_metodos = []
        for item in cursor.fetchall():
            payment_method = item['payment_method']
            resumen_metodos.append({
                'payment_method': payment_method,
                'label': _payment_method_label(payment_method),
                'total_ventas': int(item['total_ventas'] or 0),
                'valor_total': float(item['valor_total'] or 0),
            })

        cursor.execute(f"""
            SELECT
                COALESCE(NULLIF(TRIM(qh.user_name), ''), 'Sin vendedor') as vendedor,
                COUNT(DISTINCT qh.qr_code) as total_ventas,
                COALESCE(SUM(tp.price), 0) as valor_total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            {loc_clause}
            GROUP BY COALESCE(NULLIF(TRIM(qh.user_name), ''), 'Sin vendedor')
            ORDER BY valor_total DESC, total_ventas DESC, vendedor ASC
        """, (fecha_inicio, fecha_fin, *loc_params))

        resumen_vendedores = []
        for item in cursor.fetchall():
            resumen_vendedores.append({
                'vendedor': item['vendedor'],
                'total_ventas': int(item['total_ventas'] or 0),
                'valor_total': float(item['valor_total'] or 0),
            })

        cursor.execute(f"""
            SELECT
                MIN(qh.fecha_hora) as primera_venta,
                MAX(qh.fecha_hora) as ultima_venta,
                COUNT(DISTINCT COALESCE(NULLIF(TRIM(qh.user_name), ''), 'Sin vendedor')) as vendedores_activos,
                COUNT(DISTINCT CASE
                    WHEN qh.payment_method IS NULL OR TRIM(qh.payment_method) = '' THEN qh.qr_code
                    ELSE NULL
                END) as ventas_sin_metodo
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            {loc_clause}
        """, (fecha_inicio, fecha_fin, *loc_params))

        caja_data = cursor.fetchone() or {}

        # Si es el mismo día: agrupar por hora. Si es rango: agrupar por día
        es_mismo_dia = fecha_inicio == fecha_fin

        if es_mismo_dia:
            cursor.execute(f"""
                SELECT
                    HOUR(qh.fecha_hora) as periodo,
                    COUNT(DISTINCT qh.qr_code) as cantidad
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                AND qr.turnPackageId IS NOT NULL
                AND qr.turnPackageId != 1
                AND qh.es_venta_real = TRUE
                {loc_clause}
                GROUP BY HOUR(qh.fecha_hora)
                ORDER BY periodo
            """, (fecha_inicio, fecha_fin, *loc_params))
            ventas_evolucion = cursor.fetchall()
            tipo_evolucion = 'horas'
            labels_evolucion = [f"{item['periodo']}:00" for item in ventas_evolucion]
        else:
            cursor.execute(f"""
                SELECT
                    DATE(qh.fecha_hora) as periodo,
                    COUNT(DISTINCT qh.qr_code) as cantidad
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                AND qr.turnPackageId IS NOT NULL
                AND qr.turnPackageId != 1
                AND qh.es_venta_real = TRUE
                {loc_clause}
                GROUP BY DATE(qh.fecha_hora)
                ORDER BY periodo
            """, (fecha_inicio, fecha_fin, *loc_params))
            ventas_evolucion = cursor.fetchall()
            tipo_evolucion = 'dias'
            labels_evolucion = [str(item['periodo']) for item in ventas_evolucion]

        fecha_inicio_ayer = (datetime.strptime(fecha_inicio, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        fecha_fin_ayer = (datetime.strptime(fecha_fin, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')

        cursor.execute(f"""
            SELECT
                COUNT(DISTINCT qh.qr_code) as paquetes_ayer,
                COALESCE(SUM(tp.price), 0) as ventas_ayer
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
            AND qr.turnPackageId IS NOT NULL
            AND qr.turnPackageId != 1
            AND qh.es_venta_real = TRUE
            {loc_clause}
        """, (fecha_inicio_ayer, fecha_fin_ayer, *loc_params))

        ayer_data = cursor.fetchone()

        total_ventas_hoy = float(estadisticas_data['total_ventas'] or 0)
        total_ventas_ayer = float(ayer_data['ventas_ayer'] or 0)

        total_paquetes_hoy = estadisticas_data['total_paquetes'] or 0
        total_paquetes_ayer = ayer_data['paquetes_ayer'] or 0

        tendencia_ventas = 0
        if total_ventas_ayer > 0:
            tendencia_ventas = ((total_ventas_hoy - total_ventas_ayer) / total_ventas_ayer) * 100

        tendencia_paquetes = 0
        if total_paquetes_ayer > 0:
            tendencia_paquetes = ((total_paquetes_hoy - total_paquetes_ayer) / total_paquetes_ayer) * 100

        eficiencia = 85

        primera_venta = caja_data.get('primera_venta')
        ultima_venta = caja_data.get('ultima_venta')
        primera_venta_str = (
            parse_db_datetime(primera_venta).strftime('%Y-%m-%d %H:%M:%S')
            if primera_venta else None
        )
        ultima_venta_str = (
            parse_db_datetime(ultima_venta).strftime('%Y-%m-%d %H:%M:%S')
            if ultima_venta else None
        )

        cuadre_caja = {
            'total_recaudo': total_ventas_hoy,
            'total_paquetes': int(total_paquetes_hoy or 0),
            'ticket_promedio': float(estadisticas_data['ticket_promedio'] or 0),
            'vendedores_activos': int(caja_data.get('vendedores_activos') or 0),
            'metodos_registrados': len(resumen_metodos),
            'ventas_sin_metodo': int(caja_data.get('ventas_sin_metodo') or 0),
            'primera_venta': primera_venta_str,
            'ultima_venta': ultima_venta_str,
        }

        graficos = {
    'paquetes': {
        'labels': [item['paquete'] for item in ventas_por_paquete],
        'data': [item['cantidad'] for item in ventas_por_paquete]
    },
    'evolucion': {
        'labels': labels_evolucion,
        'data': [item['cantidad'] for item in ventas_evolucion],
        'tipo': tipo_evolucion
    }
}

        ventas_formateadas = []
        for venta in ventas:
            audit_data = _serialize_payment_method_audit(venta)
            ventas_formateadas.append({
                'id': int(venta['id']),
                'fecha': str(venta['fecha']),
                'hora': str(venta['hora'])[:5] if venta['hora'] else '00:00',
                'paquete': venta['paquete'],
                'qr_code': venta['qr_code'],
                'qr_nombre': venta['qr_name'] or 'Sin nombre',
                **audit_data,
                'precio': float(venta['precio']),
                'turnos': venta['turnos'],
                'vendedor': venta['vendedor'],
                'estado': venta['estado']
            })

        logger.info(f"Ventas obtenidas: {len(ventas_formateadas)} registros")

        return jsonify({
            'ventas': ventas_formateadas,
            'estadisticas': {
                'total_ventas': total_ventas_hoy,
                'total_paquetes': total_paquetes_hoy,
                'ticket_promedio': float(estadisticas_data['ticket_promedio'] or 0),
                'tendencia_ventas': round(tendencia_ventas, 1),
                'tendencia_paquetes': round(tendencia_paquetes, 1),
                'eficiencia': eficiencia
            },
            'graficos': graficos,
            'resumen_metodos': resumen_metodos,
            'resumen_vendedores': resumen_vendedores,
            'cuadre_caja': cuadre_caja,
            'rango_fechas': {
                'inicio': fecha_inicio,
                'fin': fecha_fin
            },
            'timestamp': get_colombia_time().isoformat()
        })

    except Exception as e:
        logger.error(f"Error obteniendo ventas: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/exportar-ventas-pdf', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def exportar_ventas_pdf():
    """Exportar ventas como PDF"""
    try:

        fecha_inicio = request.args.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))

        logger.info(f"Exportando ventas a PDF: {fecha_inicio} - {fecha_fin}")

        return jsonify({
            'status': 'success',
            'message': 'Función de exportación PDF en desarrollo',
            'rango_fechas': f'{fecha_inicio} a {fecha_fin}',
            'sugerencia': 'Implementar con reportlab o weasyprint'
        })

    except Exception as e:
        logger.error(f"Error exportando PDF: {e}")
        return api_response('E001', http_status=500)


# ==================== APIS PARA REPORTES DE FALLAS ====================

@qr_bp.route('/api/reportar-falla-maquina', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
@validate_required_fields(['machine_id', 'description'])
def reportar_falla_maquina():
    """Reportar falla en una máquina"""
    connection = None
    cursor = None

    try:
        data = request.get_json()
        machine_id = data['machine_id']
        description = data['description'].strip()
        problem_type = data.get('problem_type', 'mantenimiento')
        station_index = data.get('station_index', None)
        user_id = session.get('user_id', 1)

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id, name FROM machine WHERE id = %s", (machine_id,))
        maquina = cursor.fetchone()

        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': machine_id})

        # Insertar reporte con station_index
        cursor.execute("""
            INSERT INTO errorreport
            (machineId, userId, description, problem_type, reportedAt, isResolved, station_index)
            VALUES (%s, %s, %s, %s, NOW(), FALSE, %s)
        """, (machine_id, user_id, description, problem_type, station_index))

        error_report_id = cursor.lastrowid

        # Determinar el nuevo estado global de la máquina
        # Para máquinas multi-estación: solo ir a 'mantenimiento' si TODAS las estaciones
        # tienen al menos un errorreport activo (no resuelto)
        cursor.execute("""
            SELECT mt.machine_subtype, JSON_LENGTH(mt.station_names) as n_stations
            FROM machinetechnical mt
            WHERE mt.machine_id = %s
        """, (machine_id,))
        maq_info = cursor.fetchone() or {}
        machine_subtype = maq_info.get('machine_subtype', 'simple') or 'simple'
        n_stations      = maq_info.get('n_stations') or 1

        if machine_subtype == 'multi_station' and n_stations > 1:
            # Contar cuántas estaciones distintas tienen fallas activas
            cursor.execute("""
                SELECT COUNT(DISTINCT station_index) as estaciones_con_falla
                FROM errorreport
                WHERE machineId = %s AND isResolved = 0 AND station_index IS NOT NULL
            """, (machine_id,))
            row = cursor.fetchone() or {}
            estaciones_con_falla = row.get('estaciones_con_falla', 0)
            nuevo_estado = 'mantenimiento' if estaciones_con_falla >= n_stations else 'activa'
        else:
            nuevo_estado = 'mantenimiento' if problem_type == 'mantenimiento' else 'inactiva'

        # Actualizar stations_in_maintenance
        try:
            cursor.execute("SELECT stations_in_maintenance FROM machine WHERE id = %s", (machine_id,))
            maq_row = cursor.fetchone() or {}
            en_mant = parse_json_col(maq_row.get('stations_in_maintenance'), [])
            if station_index is not None and station_index not in en_mant:
                en_mant.append(station_index)
            cursor.execute(
                "UPDATE machine SET stations_in_maintenance = %s WHERE id = %s",
                (json.dumps(en_mant), machine_id)
            )
        except Exception as e:
            logger.warning(f"No se pudo actualizar stations_in_maintenance: {e}")

        cursor.execute("""
            UPDATE machine
            SET status = %s,
                errorNote = %s,
                dailyFailedTurns = COALESCE(dailyFailedTurns, 0) + 1
            WHERE id = %s
        """, (nuevo_estado, description, machine_id))

        # Contar fallas activas para esta máquina/estación
        if station_index is not None:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM errorreport
                WHERE machineId = %s AND station_index = %s AND isResolved = 0
            """, (machine_id, station_index))
        else:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM errorreport
                WHERE machineId = %s AND isResolved = 0
            """, (machine_id,))
        fallas_activas = (cursor.fetchone() or {}).get('cnt', 0)

        # Cualquier falla desde la web → encolar MAINTENANCE al ESP32 para mostrar pantalla de mantenimiento
        try:
            cursor.execute("""
                INSERT INTO esp32_commands
                (machine_id, command, parameters, triggered_by, status, triggered_at)
                VALUES (%s, 'MAINTENANCE', %s, 'sistema_auto', 'queued', NOW())
            """, (machine_id, json.dumps({
                'machine_name': maquina['name'],
                'station_index': station_index,
                'failure_count': fallas_activas,
                'reason': 'Falla reportada desde web — activar pantalla mantenimiento'
            })))
            logger.warning(
                f"⚠ MAINTENANCE encolado — {maquina['name']} "
                f"(station={station_index}) fallas_activas={fallas_activas}"
            )
        except Exception as cmd_err:
            logger.error(f"No se pudo encolar MAINTENANCE: {cmd_err}")

        connection.commit()

        logger.info(
            f"Falla reportada — {maquina['name']} reporte#{error_report_id} "
            f"estación={station_index} fallas_activas={fallas_activas}"
        )

        return api_response(
            'S002',
            status='success',
            data={
                'machine_id': machine_id,
                'machine_name': maquina['name'],
                'new_status': nuevo_estado,
                'error_report_id': error_report_id,
                'fallas_activas': fallas_activas,
                'maintenance_triggered': fallas_activas >= 3
            }
        )

    except Exception as e:
        logger.error(f"Error reportando falla: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/reportes/<int:reporte_id>/resolver', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def resolver_reporte(reporte_id):
    """Marcar un reporte como resuelto"""
    connection = None
    cursor = None
    try:
        logger.info(f"=== INICIANDO RESOLUCIÓN DE REPORTE {reporte_id} ===")

        data = request.get_json()
        comentarios = data.get('comentarios', '')
        user_id = session.get('user_id')
        user_name = session.get('user_name')
        user_role = session.get('user_role')

        logger.info(f"DEPURACIÓN - user_id: {user_id}, user_name: {user_name}, user_role: {user_role}")
        logger.info(f"Datos recibidos: {data}")
        logger.info(f"Comentarios: '{comentarios}'")

        if not user_id:
            logger.error("Usuario no autenticado - Sesión inválida")
            return api_response('E003', http_status=401, data={'message': 'Usuario no autenticado'})

        if user_role != 'admin':
            logger.error(f"Usuario {user_name} no es admin, es {user_role}")
            return api_response('E004', http_status=403, data={'message': 'Solo administradores pueden resolver reportes'})

        connection = get_db_connection()
        if not connection:
            logger.error("No se pudo conectar a la BD")
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT 1 as test")
        test_result = cursor.fetchone()
        logger.info(f"Conexión BD test: {test_result}")

        cursor.execute("SELECT id FROM errorreport WHERE id = %s", (reporte_id,))
        reporte_existe = cursor.fetchone()

        if not reporte_existe:
            logger.error(f"Reporte {reporte_id} no encontrado")
            return api_response('M007', http_status=404, data={'message': 'Reporte no encontrado'})

        cursor.execute("""
            SELECT er.*, m.name as machine_name, m.id as machine_id
            FROM errorreport er
            LEFT JOIN machine m ON er.machineId = m.id
            WHERE er.id = %s
        """, (reporte_id,))

        reporte = cursor.fetchone()
        logger.info(f"Reporte encontrado: {reporte}")

        if not reporte:
            logger.error(f"Error al obtener datos del reporte {reporte_id}")
            return api_response('M007', http_status=404, data={'message': 'Reporte no encontrado'})

        machine_id = reporte['machineId']
        machine_name = reporte['machine_name']

        logger.info(f"Máquina asociada: id={machine_id}, nombre={machine_name}")

        try:

            logger.info("Actualizando ErrorReport...")

            query_update_er = """
                UPDATE errorreport
                SET isResolved = TRUE, resolved_at = NOW()
                WHERE id = %s
            """

            cursor.execute(query_update_er, (reporte_id,))
            logger.info(f"ErrorReport actualizado: {cursor.rowcount} filas afectadas")


            logger.info("Insertando en confirmation_logs...")

            try:
                insert_query = """
                    INSERT INTO confirmation_logs
                    VALUES (%s, %s, %s, %s)
                """
                logger.info(f"Query: {insert_query}")
                logger.info(f"Valores: {reporte_id}, {user_id}, 'resuelta', '{comentarios}'")

                cursor.execute(insert_query, (reporte_id, user_id, 'resuelta', comentarios))
                confirmation_id = cursor.lastrowid
                logger.info(f"Registro creado en confirmation_logs con ID: {confirmation_id}")
            except Exception as insert_error:
                logger.error(f"Error insertando en confirmation_logs: {insert_error}")

                cursor.execute("""
                    INSERT INTO confirmation_logs
                    (fault_report_id, admin_id, confirmation_status)
                    VALUES (%s, %s, %s)
                """, (reporte_id, user_id, 'resuelta'))
                confirmation_id = cursor.lastrowid
                logger.info(f"Registro creado (sin comments) con ID: {confirmation_id}")

            if machine_id:
                logger.info(f"Actualizando estado de máquina {machine_id}...")


                cursor.execute("""
                    SELECT COUNT(*) as reportes_pendientes
                    FROM errorreport
                    WHERE machineId = %s AND isResolved = FALSE
                """, (machine_id,))

                otros_reportes = cursor.fetchone()
                reportes_pendientes = otros_reportes['reportes_pendientes'] if otros_reportes else 0

                logger.info(f"Máquina {machine_id} tiene {reportes_pendientes} reportes pendientes adicionales")

                if reportes_pendientes == 0:

                    cursor.execute("""
                        UPDATE machine
                        SET status = 'activa',
                            errorNote = NULL  -- IMPORTANTE: Limpiar el mensaje de error
                        WHERE id = %s AND status IN ('mantenimiento', 'inactiva')
                    """, (machine_id,))

                    if cursor.rowcount > 0:
                        logger.info(f"Máquina {machine_id} cambiada a estado 'activa' y errorNote limpiado")
                    else:
                        logger.info(f"Máquina {machine_id} no cambió de estado (ya estaba activa o no aplica)")
                else:

                    cursor.execute("""
                        UPDATE machine
                        SET status = 'activa'
                        WHERE id = %s AND status IN ('mantenimiento', 'inactiva')
                    """, (machine_id,))

                    if cursor.rowcount > 0:
                        logger.info(f"Máquina {machine_id} cambiada a estado 'activa' (aún tiene {reportes_pendientes} reportes pendientes)")
                    else:
                        logger.info(f"Máquina {machine_id} no cambió de estado")

            connection.commit()
            logger.info(f"=== REPORTE {reporte_id} RESUELTO EXITOSAMENTE ===")

            return api_response(
                'S009',
                status='success',
                data={
                    'machine_id': machine_id,
                    'reporte_id': reporte_id,
                    'machine_name': machine_name,
                    'confirmation_id': confirmation_id,
                    'resolved_by': user_name,
                    'errorNote_cleared': True if machine_id and reportes_pendientes == 0 else False
                }
            )

        except Exception as trans_error:
            logger.error(f"Error en transacción: {trans_error}", exc_info=True)
            connection.rollback()

            error_msg = str(trans_error)

            if "confirmation_logs" in error_msg:

                logger.info("Verificando estructura de confirmation_logs...")
                try:
                    cursor.execute("DESCRIBE confirmation_logs")
                    estructura = cursor.fetchall()
                    logger.info(f"Estructura: {estructura}")
                except Exception as e:
                    logger.error(f"Error verificando estructura: {e}")

            raise Exception(f"Error en transacción: {error_msg}")

    except Exception as e:
        logger.error(f"Error resolviendo reporte: {e}", exc_info=True)
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/debug/tabla-confirmation-logs', methods=['GET'])
def debug_confirmation_logs():
    """Debug: Ver estructura exacta de confirmation_logs"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_db_cursor(connection)

        cursor.execute("DESCRIBE confirmation_logs")
        estructura = cursor.fetchall()

        cursor.execute("SHOW COLUMNS FROM confirmation_logs LIKE 'confirmation_status'")
        enum_info = cursor.fetchone()

        test_data = {
            'fault_report_id': 5,
            'admin_id': session.get('user_id', 1),
            'confirmation_status': 'resuelta',
            'comments': 'test desde API'
        }

        try:
            cursor.execute("""
                INSERT INTO confirmation_logs
                (fault_report_id, admin_id, confirmation_status, comments)
                VALUES (%s, %s, %s, %s)
            """, (test_data['fault_report_id'], test_data['admin_id'],
                  test_data['confirmation_status'], test_data['comments']))

            test_id = cursor.lastrowid
            connection.commit()

            cursor.execute("SELECT * FROM confirmation_logs WHERE id = %s", (test_id,))
            registro_insertado = cursor.fetchone()

            return jsonify({
                'estructura': estructura,
                'enum_info': enum_info,
                'test_insert': {
                    'success': True,
                    'id': test_id,
                    'registro': registro_insertado
                }
            })

        except Exception as insert_error:
            connection.rollback()
            return jsonify({
                'estructura': estructura,
                'enum_info': enum_info,
                'test_insert': {
                    'success': False,
                    'error': str(insert_error),
                    'error_type': type(insert_error).__name__
                }
            })

    except Exception as e:
        return jsonify({'error': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/debug/reporte-5', methods=['GET'])
def debug_reporte_5():
    """Debug: Verificar reporte con ID 5"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                er.*,
                m.name as machine_name,
                u.name as user_name
            FROM errorreport er
            LEFT JOIN machine m ON er.machineId = m.id
            LEFT JOIN users u ON er.userId = u.id
            WHERE er.id = 5
        """)

        reporte = cursor.fetchone()

        return jsonify({
            'reporte_5': reporte,
            'exists': reporte is not None
        })

    except Exception as e:
        return jsonify({'error': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@qr_bp.route('/api/debug/errorreport-estructura', methods=['GET'])
def debug_errorreport_estructura():
    """Verificar estructura de ErrorReport"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_db_cursor(connection)

        cursor.execute("DESCRIBE errorreport")
        estructura = cursor.fetchall()

        cursor.execute("DESCRIBE confirmation_logs")
        estructura_logs = cursor.fetchall()

        return jsonify({
            'ErrorReport': estructura,
            'confirmation_logs': estructura_logs
        })
    except Exception as e:
        return jsonify({'error': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
