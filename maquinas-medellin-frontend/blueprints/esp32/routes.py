import logging
import time
from datetime import date

import sentry_sdk
from flask import Blueprint, request, jsonify, json

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from middleware.logging_mw import log_transaccion
from utils.auth import require_login
from utils.helpers import parse_json_col
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time
from utils.validators import validate_required_fields
from blueprints.esp32.state import set_heartbeat
from utils.notifications import notify_falla

logger = logging.getLogger(LOGGER_NAME)

esp32_bp = Blueprint('esp32', __name__)


# ── Estado / health ───────────────────────────────────────────────────────────

@esp32_bp.route('/api/esp32/status', methods=['GET'])
def esp32_status():
    """Endpoint para verificar estado del servidor desde ESP32"""
    return jsonify({
        'status': 'online',
        'message': 'Servidor funcionando correctamente',
        'timestamp': get_colombia_time().isoformat()
    })


@esp32_bp.route('/api/esp32/heartbeat', methods=['POST'])
def esp32_heartbeat():
    """
    ESP32 llama a este endpoint cada STATUS_UPDATE_MS (~30s) para reportar
    que sigue activo y cuál es su estado de conectividad.
    Body JSON: { machine_id, wifi_connected, server_online, rssi (opcional) }
    """
    data = request.get_json(silent=True) or {}
    machine_id = data.get('machine_id')
    if not machine_id:
        return jsonify({'status': 'error', 'message': 'machine_id requerido'}), 400

    set_heartbeat(
        machine_id=int(machine_id),
        wifi=bool(data.get('wifi_connected', True)),
        server=bool(data.get('server_online', True)),
        rssi=int(data.get('rssi', 0)),
    )
    return jsonify({'status': 'ok'})


# ── Estado de fallas ──────────────────────────────────────────────────────────

@esp32_bp.route('/api/esp32/estado-fallas/<int:machine_id>', methods=['GET'])
@handle_api_errors
def esp32_estado_fallas(machine_id):
    """
    El ESP32 consulta este endpoint al arrancar para precargar los contadores de
    fallas consecutivas y saber qué estaciones están en mantenimiento.
    Respuesta: { consecutive_failures: {"0":2,"1":0}, stations_in_maintenance: [0] }
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'db'}), 500
        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT consecutive_failures, stations_in_maintenance, status
            FROM machine WHERE id = %s
        """, (machine_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'machine_not_found'}), 404

        try:
            consec = json.loads(row['consecutive_failures'] or '{}')
        except Exception:
            consec = {}
        try:
            en_mant = json.loads(row['stations_in_maintenance'] or '[]')
        except Exception:
            en_mant = []

        return jsonify({
            'machine_id': machine_id,
            'status': row['status'],
            'consecutive_failures': consec,
            'stations_in_maintenance': en_mant,
        })
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ── Registro de uso ───────────────────────────────────────────────────────────

@esp32_bp.route('/api/esp32/registrar-uso', methods=['POST'])
@handle_api_errors
@validate_required_fields(['qr_code', 'machine_id'])
def esp32_registrar_uso():
    """Registrar uso de máquina desde ESP32 - CON SOPORTE PARA ESTACIONES"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        machine_id = data['machine_id']
        # Para multi-estación: station_index = None hasta que el usuario elija.
        # Para simple: default 0 (única estación).
        station_index = data.get('selected_station', None)

        logger.info(f"ESP32: Registrando uso - QR: {qr_code}, Máquina: {machine_id}, Estación: {station_index}")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id, qr_name, expiration_date FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()
        if not qr_data:
            return api_response('Q001', http_status=404)

        qr_id = qr_data['id']
        qr_name = qr_data['qr_name']

        if qr_data.get('expiration_date') and qr_data['expiration_date'] < date.today():
            logger.warning(f"ESP32: QR vencido — {qr_code}, expiró el {qr_data['expiration_date']}")
            return jsonify({'status': 'error', 'code': 'Q007', 'message': 'QR vencido'}), 400

        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        turnos_data = cursor.fetchone()

        if not turnos_data or turnos_data['turns_remaining'] <= 0:
            return api_response('Q003', http_status=400)

        turns_after = turnos_data['turns_remaining'] - 1

        # Insertar con station_index y turns_remaining_after (V36); fallback si columna no existe
        try:
            cursor.execute("""
                INSERT INTO turnusage (qrCodeId, machineId, station_index, turns_remaining_after, usedAt)
                VALUES (%s, %s, %s, %s, NOW())
            """, (qr_id, machine_id, station_index, turns_after))
        except Exception:
            cursor.execute("""
                INSERT INTO turnusage (qrCodeId, machineId, station_index, usedAt)
                VALUES (%s, %s, %s, NOW())
            """, (qr_id, machine_id, station_index))

        usage_id = cursor.lastrowid
        logger.info(f"✅ USAGE_ID generado: {usage_id}, Estación: {station_index}")

        cursor.execute(
            "UPDATE userturns SET turns_remaining = turns_remaining - 1 WHERE qr_code_id = %s",
            (qr_id,)
        )
        cursor.execute("UPDATE machine SET dateLastQRUsed = NOW() WHERE id = %s", (machine_id,))

        connection.commit()

        cursor.execute("""
            SELECT ut.turns_remaining, tp.name as package_name
            FROM userturns ut
            JOIN qrcode qr ON qr.id = ut.qr_code_id
            LEFT JOIN turnpackage tp ON ut.package_id = tp.id
            WHERE ut.qr_code_id = %s
        """, (qr_id,))

        info_actualizada = cursor.fetchone()
        turnos_restantes = info_actualizada['turns_remaining']

        logger.info(
            f"ESP32: Uso registrado — QR: {qr_code} | Máquina: {machine_id} | "
            f"Estación: {station_index} | Turnos restantes: {turnos_restantes} | Usage ID: {usage_id}"
        )

        log_transaccion(
            tipo='turno_qr',
            categoria='operacional',
            descripcion=f"Turno vía QR {qr_code} ({qr_name}) — Estación {station_index}",
            maquina_id=machine_id,
            entidad='qr',
            entidad_id=qr_id,
            datos_extra={
                'qr_code': qr_code,
                'qr_name': qr_name,
                'usage_id': usage_id,
                'station_index': station_index,
                'turns_remaining': turnos_restantes,
                'package_name': info_actualizada['package_name'],
                'origen': 'esp32'
            }
        )

        return api_response(
            'S010',
            status='success',
            data={
                'turns_remaining': turnos_restantes,
                'package_name': info_actualizada['package_name'],
                'qr_name': qr_name,
                'qr_code': qr_code,
                'machine_id': machine_id,
                'usage_id': usage_id,
                'station_index': station_index
            }
        )

    except Exception as e:
        logger.error(f"Error registrando uso desde ESP32: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@esp32_bp.route('/api/esp32/actualizar-uso-estacion', methods=['POST'])
@handle_api_errors
def esp32_actualizar_uso_estacion():
    """
    Actualiza la estación de un uso ya registrado.
    Llamado por el ESP32 cuando el usuario elige estación en máquinas multi-estación,
    DESPUÉS de que el turno ya fue descontado en /registrar-uso.
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        usage_id    = data.get('usage_id')
        machine_id  = data.get('machine_id')
        station_index = data.get('station_index')

        if usage_id is None or machine_id is None or station_index is None:
            return api_response('E005', http_status=400, data={
                'message': 'Faltan datos: usage_id, machine_id y station_index son requeridos'
            })

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("""
            UPDATE turnusage
            SET station_index = %s
            WHERE id = %s AND machineId = %s
        """, (station_index, usage_id, machine_id))

        if cursor.rowcount == 0:
            return api_response('E002', http_status=404, data={
                'message': f'Usage ID {usage_id} no encontrado para máquina {machine_id}'
            })

        connection.commit()
        logger.info(
            f"✅ [ESP32] Uso {usage_id} actualizado — estación {station_index} (máquina {machine_id})"
        )

        return api_response('S010', status='success', data={
            'usage_id': usage_id,
            'station_index': station_index,
            'machine_id': machine_id
        })

    except Exception as e:
        logger.error(f"❌ [ESP32] Error actualizando estación de uso: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500, data={'error': str(e)})
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@esp32_bp.route('/api/esp32/juego-exitoso', methods=['POST'])
@handle_api_errors
def esp32_juego_exitoso():
    """
    Notifica al backend que un juego terminó sin falla reportada.
    Resetea consecutive_failures para la estación indicada en la columna JSON de machine.
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        machine_id    = data.get('machine_id')
        station_index = data.get('station_index', 0)

        if not machine_id:
            return api_response('E005', http_status=400, data={
                'message': 'Falta machine_id'
            })

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        # Leer consecutive_failures actual
        cursor.execute(
            "SELECT consecutive_failures, stations_in_maintenance FROM machine WHERE id = %s",
            (machine_id,)
        )
        row = cursor.fetchone()
        if not row:
            return api_response('M001', http_status=404, data={
                'message': f'Máquina {machine_id} no encontrada'
            })

        try:
            cf = json.loads(row['consecutive_failures'] or '{}')
        except Exception:
            cf = {}

        key = str(station_index)
        prev_count = cf.get(key, 0)
        cf[key] = 0  # Reiniciar contador de esa estación

        # También quitar la estación de stations_in_maintenance si estaba ahí
        try:
            sim = json.loads(row.get('stations_in_maintenance') or '[]')
            if not isinstance(sim, list): sim = []
        except Exception:
            sim = []
        sim = [s for s in sim if str(s) != str(station_index)]

        cursor.execute(
            "UPDATE machine SET consecutive_failures = %s, stations_in_maintenance = %s WHERE id = %s",
            (json.dumps(cf), json.dumps(sim), machine_id)
        )
        connection.commit()

        logger.info(
            f"✅ [ESP32] Juego exitoso — Máquina {machine_id}, "
            f"Estación {station_index}: contador {prev_count} → 0"
        )

        return api_response('S010', status='success', data={
            'machine_id': machine_id,
            'station_index': station_index,
            'prev_count': prev_count,
            'new_count': 0
        })

    except Exception as e:
        logger.error(f"❌ [ESP32] Error procesando juego exitoso: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500, data={'error': str(e)})
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@esp32_bp.route('/api/esp32/ultimo-usage/<qr_code>/<int:machine_id>', methods=['GET'])
@handle_api_errors
def esp32_ultimo_usage(qr_code, machine_id):
    """Obtener el último usage_id para un QR y máquina específicos"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'usage_id': 0, 'error': 'Sin conexión'}), 500

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT tu.id as usage_id
            FROM turnusage tu
            JOIN qrcode qr ON tu.qrCodeId = qr.id
            WHERE qr.code = %s AND tu.machineId = %s
            ORDER BY tu.usedAt DESC
            LIMIT 1
        """, (qr_code, machine_id))

        result = cursor.fetchone()

        if result:
            return jsonify({'usage_id': result['usage_id']})
        else:
            return jsonify({'usage_id': 0})

    except Exception as e:
        logger.error(f"Error obteniendo último usage_id: {e}")
        return jsonify({'usage_id': 0, 'error': str(e)}), 500
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ── Comandos ──────────────────────────────────────────────────────────────────

@esp32_bp.route('/api/esp32/check-commands/<int:machine_id>', methods=['GET'])
@handle_api_errors
def esp32_check_commands(machine_id):
    """Endpoint para que el ESP32 consulte comandos pendientes"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'has_commands': False, 'commands': []})

        cursor = get_db_cursor(connection)

        # Nota: triggered_at es la columna que existe en BD (no created_at)
        cursor.execute("""
            SELECT id, command, parameters, triggered_at
            FROM esp32_commands
            WHERE machine_id = %s AND status = 'queued'
            ORDER BY triggered_at ASC
        """, (machine_id,))

        commands = cursor.fetchall()

        return jsonify({
            'has_commands': len(commands) > 0,
            'commands': commands
        })

    except Exception as e:
        logger.error(f"Error checking commands for ESP32: {e}")
        return jsonify({'has_commands': False, 'commands': []})
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@esp32_bp.route('/api/esp32/command-executed/<int:command_id>', methods=['POST'])
@handle_api_errors
def esp32_command_executed(command_id):
    """Endpoint para que el ESP32 confirme ejecución de comando"""
    connection = None
    cursor = None
    try:
        data = request.get_json()
        machine_id = data.get('machine_id')
        estacion = data.get('estacion', 0)
        result = data.get('result', 'success')

        logger.info(
            f"✅ ESP32 confirmó comando {command_id} — "
            f"Máquina: {machine_id}, Estación: {estacion}, Resultado: {result}"
        )

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            UPDATE esp32_commands
            SET status = 'executed',
                executed_at = NOW(),
                response = %s
            WHERE id = %s
        """, (json.dumps(data), command_id))

        connection.commit()

        return api_response('S001', status='success')

    except Exception as e:
        logger.error(f"Error updating command status: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ── Configuración de máquina ──────────────────────────────────────────────────

@esp32_bp.route('/api/esp32/machine-config/<int:machine_id>', methods=['GET'])
def esp32_machine_config(machine_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = get_db_cursor(connection)

        # IMPORTANTE: NO incluir station_count (no existe en BD)
        try:
            cursor.execute("""
                SELECT
                    m.id, m.name, m.type, m.status,
                    m.consecutive_failures, m.stations_in_maintenance,
                    mt.credits_virtual, mt.credits_machine,
                    mt.game_duration_seconds, mt.reset_time_seconds,
                    mt.machine_subtype, mt.station_names,
                    mt.game_type, mt.has_failure_report,
                    mt.show_station_selection,
                    (SELECT MAX(usedAt) FROM turnusage WHERE machineId = m.id) as last_play_time
                FROM machine m
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                WHERE m.id = %s
            """, (machine_id,))
        except Exception:
            # Migración V32 pendiente: columnas de fallas por estación aún no existen
            cursor.execute("""
                SELECT
                    m.id, m.name, m.type, m.status,
                    NULL AS consecutive_failures, NULL AS stations_in_maintenance,
                    mt.credits_virtual, mt.credits_machine,
                    mt.game_duration_seconds, mt.reset_time_seconds,
                    mt.machine_subtype, mt.station_names,
                    mt.game_type, mt.has_failure_report,
                    mt.show_station_selection,
                    (SELECT MAX(usedAt) FROM turnusage WHERE machineId = m.id) as last_play_time
                FROM machine m
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                WHERE m.id = %s
            """, (machine_id,))

        config = cursor.fetchone()

        if not config:
            return jsonify({'status': 'error', 'code': 'M001', 'message': 'Máquina no encontrada'}), 404

        # Procesar station_names (JSON string → array)
        station_names = []
        if config['station_names']:
            try:
                if isinstance(config['station_names'], str):
                    station_names = json.loads(config['station_names'])
                else:
                    station_names = config['station_names']
            except Exception:
                station_names = [config['name']]
        else:
            if config['machine_subtype'] == 'multi_station':
                station_names = ["Estación 1", "Estación 2"]
            else:
                station_names = [config['name']]

        # Construir active_failure_stations desde consecutive_failures
        active_failure_stations = []
        try:
            cf  = parse_json_col(config.get('consecutive_failures'), {})
            sim = parse_json_col(config.get('stations_in_maintenance'), [])
            for key, count in cf.items():
                if count > 0:
                    idx = int(key) if key != 'all' else 0
                    active_failure_stations.append({
                        'station_index': idx,
                        'count': int(count),
                        'in_maintenance': idx in sim or str(idx) in [str(x) for x in sim]
                    })
        except Exception:
            pass

        response_data = {
            'id': config['id'],
            'name': config['name'],
            'type': config['type'],
            'status': config['status'],
            'credits_virtual': config['credits_virtual'] or 1,
            'credits_machine': config['credits_machine'] or 1,
            'game_duration_seconds': config['game_duration_seconds'] or 180,
            'reset_time_seconds': config['reset_time_seconds'] or 5,
            'machine_subtype': config['machine_subtype'] or 'simple',
            'station_names': station_names,
            'game_type': config['game_type'] or 'time_based',
            'has_failure_report': bool(config['has_failure_report']),
            'show_station_selection': bool(config['show_station_selection']),
            'last_play_time': config['last_play_time'],
            'active_failure_stations': active_failure_stations
        }

        return jsonify({'status': 'success', 'data': response_data})

    except Exception as e:
        logger.error(f"Error obteniendo configuración de máquina {machine_id}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ── Reporte de falla desde TFT ────────────────────────────────────────────────

@esp32_bp.route('/api/esp32/reportar-falla', methods=['POST'])
@handle_api_errors
def esp32_reportar_falla():
    """
    Endpoint para recibir reportes de falla desde la TFT/ESP32.
    Cuando el usuario presiona el botón REPORTAR durante el juego.
    Devuelve el turno automáticamente.
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()

        machine_id      = data.get('machine_id')
        machine_name    = data.get('machine_name')
        qr_code         = data.get('qr_code')
        usage_id        = data.get('usage_id')
        turnos_devueltos = data.get('turnos_devueltos', 1)
        is_forced       = data.get('is_forced', False)
        notes           = data.get('notes', 'Reporte desde TFT - Botón REPORTAR')
        station_index   = data.get('selected_station', None)  # None = máquina simple (estación 0)

        logger.info(f"🔄 [TFT] Reporte de falla recibido - Máquina: {machine_name}, QR: {qr_code}")

        if not machine_id or not qr_code:
            return api_response('E005', http_status=400, data={
                'message': 'Faltan datos: machine_id y qr_code son requeridos'
            })

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()

        if not qr_data:
            logger.warning(f"❌ [TFT] QR no encontrado: {qr_code}")
            return api_response('Q001', http_status=404, data={
                'qr_code': qr_code,
                'message': 'Código QR no existe en el sistema'
            })

        qr_id = qr_data['id']

        # Verificar que el usage_id corresponda
        if usage_id:
            cursor.execute("""
                SELECT id, usedAt
                FROM turnusage
                WHERE id = %s AND qrCodeId = %s AND machineId = %s
            """, (usage_id, qr_id, machine_id))

            uso_data = cursor.fetchone()
            if not uso_data:
                logger.warning(f"⚠️ [TFT] Usage ID {usage_id} no coincide, se usará el último juego")
                usage_id = None  # Forzar búsqueda del último

        # Si no hay usage_id, buscar el último juego
        if not usage_id:
            cursor.execute("""
                SELECT id, usedAt
                FROM turnusage
                WHERE qrCodeId = %s AND machineId = %s
                ORDER BY usedAt DESC
                LIMIT 1
            """, (qr_id, machine_id))

            ultimo_uso = cursor.fetchone()
            if not ultimo_uso:
                logger.warning(f"❌ [TFT] No hay juegos registrados para QR {qr_code} en máquina {machine_id}")
                return api_response('E002', http_status=404, data={
                    'message': 'No hay juegos registrados para este QR en esta máquina'
                })

            usage_id = ultimo_uso['id']
            logger.info(f"✅ [TFT] Usando último juego ID: {usage_id}")

        # Registrar la falla en machinefailures (con station_index)
        effective_station = station_index if station_index is not None else 0
        try:
            cursor.execute("""
                INSERT INTO machinefailures
                (qr_code_id, machine_id, machine_name, turnos_devueltos, notes,
                 is_forced, forced_by, station_index)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (qr_id, machine_id, machine_name, turnos_devueltos, notes,
                  0, None, effective_station))
        except Exception:
            # Fallback si la columna station_index no existe aún
            cursor.execute("""
                INSERT INTO machinefailures
                (qr_code_id, machine_id, machine_name, turnos_devueltos, notes, is_forced, forced_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (qr_id, machine_id, machine_name, turnos_devueltos, notes, 0, None))

        failure_id = cursor.lastrowid

        # ── Actualizar consecutive_failures y stations_in_maintenance en machine ──
        try:
            cursor.execute(
                "SELECT consecutive_failures, stations_in_maintenance FROM machine WHERE id = %s",
                (machine_id,)
            )
            mrow = cursor.fetchone()
            if mrow:
                cf  = json.loads(mrow['consecutive_failures']  or '{}')
                sim = json.loads(mrow['stations_in_maintenance'] or '[]')
                if not isinstance(sim, list): sim = []

                key = str(effective_station)
                cf[key] = int(cf.get(key, 0)) + 1
                new_count = cf[key]

                # Si llega a 3 fallas consecutivas → marcar estación en mantenimiento
                if new_count >= 3 and effective_station not in sim and str(effective_station) not in [str(x) for x in sim]:
                    sim.append(effective_station)
                    logger.warning(
                        f"⚠️ [TFT] Estación {effective_station} de máquina {machine_id} "
                        f"entra en MANTENIMIENTO tras {new_count} fallas consecutivas"
                    )

                cursor.execute(
                    "UPDATE machine SET consecutive_failures = %s, stations_in_maintenance = %s WHERE id = %s",
                    (json.dumps(cf), json.dumps(sim), machine_id)
                )
        except Exception as e_cf:
            logger.warning(f"⚠️ [TFT] No se pudo actualizar consecutive_failures: {e_cf}")

        # Devolver el turno automáticamente
        cursor.execute("""
            UPDATE userturns
            SET turns_remaining = turns_remaining + %s
            WHERE qr_code_id = %s
        """, (turnos_devueltos, qr_id))

        cursor.execute("""
            UPDATE qrcode
            SET remainingTurns = remainingTurns + %s
            WHERE id = %s
        """, (turnos_devueltos, qr_id))

        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        nuevos_turnos = cursor.fetchone()['turns_remaining']

        connection.commit()

        logger.info(
            f"✅ [TFT] Falla reportada — ID: {failure_id} | "
            f"Máquina: {machine_name} ({machine_id}) | QR: {qr_code} | "
            f"Turnos devueltos: {turnos_devueltos} | Turnos restantes: {nuevos_turnos}"
        )

        log_transaccion(
            tipo='falla_maquina',
            categoria='operacional',
            descripcion=f"Falla reportada desde ESP32/TFT en {machine_name} — turno devuelto",
            maquina_id=machine_id,
            maquina_nombre=machine_name,
            entidad='qr',
            entidad_id=qr_id,
            datos_extra={
                'failure_id': failure_id,
                'qr_code': qr_code,
                'usage_id': usage_id,
                'turnos_devueltos': turnos_devueltos,
                'turnos_restantes': nuevos_turnos,
                'is_forced': is_forced,
                'notes': notes,
                'origen': 'esp32_tft'
            }
        )

        # Notificación al admin (no bloquea la respuesta al ESP32)
        try:
            cursor.execute(
                "SELECT COALESCE(l.name, 'Sin local') AS local_nombre "
                "FROM machine m LEFT JOIN location l ON m.location_id = l.id "
                "WHERE m.id = %s", (machine_id,)
            )
            loc_row = cursor.fetchone()
            local_nombre = loc_row['local_nombre'] if loc_row else 'Sin local'
        except Exception:
            local_nombre = 'Sin local'
        notify_falla(machine_name or f'Máquina {machine_id}', local_nombre, notes or '')

        return api_response(
            'S012',
            status='success',
            data={
                'failure_id': failure_id,
                'qr_code': qr_code,
                'machine_id': machine_id,
                'usage_id': usage_id,
                'turnos_devueltos': turnos_devueltos,
                'turnos_restantes': nuevos_turnos,
                'message': 'Falla reportada y turno devuelto automáticamente'
            }
        )

    except Exception as e:
        logger.error(f"❌ [TFT] Error procesando reporte de falla: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500, data={
            'error': str(e),
            'message': 'Error interno del servidor al reportar falla'
        })
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ── TFT / datos de máquina ────────────────────────────────────────────────────

@esp32_bp.route('/api/tft/machine-status/<machine_id>', methods=['GET'])
def tft_machine_status(machine_id):
    """Obtener estado de máquina para pantalla TFT"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Error de conexión'}), 500

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                m.id, m.name, m.status, m.type,
                l.name as location_name,
                COUNT(tu.id) as usos_hoy
            FROM machine m
            LEFT JOIN location l ON m.location_id = l.id
            LEFT JOIN turnusage tu ON tu.machineId = m.id AND DATE(tu.usedAt) = CURDATE()
            WHERE m.id = %s OR m.name = %s
            GROUP BY m.id, m.name, m.status, m.type, l.name
        """, (machine_id, machine_id))

        machine_data = cursor.fetchone()

        if not machine_data:
            return jsonify({
                'machine_id': machine_id,
                'machine_name': 'Desconocida',
                'status': 'offline',
                'type': 'arcade',
                'location': 'Sin ubicación',
                'usos_hoy': 0,
                'message': 'Máquina no registrada'
            }), 200

        status_messages = {
            'activa': 'Disponible para jugar',
            'mantenimiento': 'En mantenimiento',
            'inactiva': 'Máquina desactivada'
        }

        return jsonify({
            'machine_id': machine_data['id'],
            'machine_name': machine_data['name'],
            'status': machine_data['status'],
            'type': machine_data['type'],
            'location': machine_data['location_name'],
            'usos_hoy': machine_data['usos_hoy'],
            'message': status_messages.get(machine_data['status'], 'Estado desconocido'),
            'online': True,
            'timestamp': get_colombia_time().isoformat()
        })

    except Exception as e:
        logger.error(f"Error estado máquina TFT: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@esp32_bp.route('/api/esp32/machine-technical/<int:machine_id>', methods=['GET'])
@handle_api_errors
def esp32_machine_technical(machine_id):
    """Obtener datos técnicos de la máquina para ESP32/TFT"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT
                COALESCE(mt.credits_virtual, 1) as credits_virtual,
                COALESCE(mt.credits_machine, 1) as credits_machine,
                COALESCE(mt.game_duration_seconds, 60) as game_duration_seconds,
                COALESCE(mt.reset_time_seconds, 5) as reset_time_seconds,
                m.name as machine_name,
                COALESCE(l.name, 'Sin ubicación') as location_name,
                MAX(tu.usedAt) as last_play_time
            FROM machine m
            LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
            LEFT JOIN location l ON m.location_id = l.id
            LEFT JOIN turnusage tu ON tu.machineId = m.id
            WHERE m.id = %s
            GROUP BY m.id, m.name, l.name, mt.credits_virtual,
                     mt.credits_machine, mt.game_duration_seconds, mt.reset_time_seconds
        """, (machine_id,))

        tech_data = cursor.fetchone()

        if not tech_data:
            return api_response('M001', http_status=404)

        return api_response(
            'S011',
            status='success',
            data={
                'machine_name': tech_data['machine_name'],
                'location': tech_data['location_name'],
                'credits_virtual': tech_data['credits_virtual'],
                'credits_machine': tech_data['credits_machine'],
                'game_duration_seconds': tech_data['game_duration_seconds'],
                'reset_time_seconds': tech_data['reset_time_seconds'],
                'last_play_time': tech_data['last_play_time'].isoformat() if tech_data['last_play_time'] else None,
                'machine_id': machine_id
            }
        )

    except Exception as e:
        logger.error(f"Error obteniendo datos técnicos de máquina {machine_id}: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@esp32_bp.route('/api/esp32/machine-reset', methods=['POST'])
@handle_api_errors
def esp32_machine_reset():
    """
    Endpoint para registrar cuando una máquina se reinicia
    después de una devolución exitosa.
    Solo registra el evento en logs — no escribe en BD.
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        machine_id   = data.get('machine_id')
        machine_name = data.get('machine_name')
        qr_code      = data.get('qr_code')
        usage_id     = data.get('usage_id')
        failure_id   = data.get('failure_id')
        reset_time   = data.get('reset_time_seconds', 5)

        ts = get_colombia_time().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(
            f"🔄🔄🔄 [REINICIO MÁQUINA] Máquina={machine_name} ({machine_id}) | "
            f"QR={qr_code} | UsageID={usage_id} | FailureID={failure_id} | "
            f"Reset={reset_time}s | ts={ts}"
        )

        return api_response(
            'S013',
            status='success',
            data={
                'message': 'Reinicio registrado',
                'machine_id': machine_id,
                'timestamp': get_colombia_time().isoformat()
            }
        )

    except Exception as e:
        logger.error(f"❌ Error registrando reinicio de máquina: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()
