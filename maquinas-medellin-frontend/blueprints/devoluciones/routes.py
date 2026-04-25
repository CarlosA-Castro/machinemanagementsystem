import logging

from flask import Blueprint, jsonify, request, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.location_scope import get_active_location, user_can_view_all
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time, parse_db_datetime
from utils.validators import validate_required_fields

logger = logging.getLogger(LOGGER_NAME)

devoluciones_bp = Blueprint('devoluciones', __name__)


@devoluciones_bp.route('/api/qr/historial-completo/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_historial_completo_qr(qr_code):
    connection = None
    cursor = None
    try:
        logger.info(f"Historial completo solicitado para QR: {qr_code}")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT
                qr.id as qr_id,
                qr.code as qr_code,
                qr.remainingTurns,
                qr.isActive,
                qr.turnPackageId,
                qr.qr_name,
                tp.name as package_name,
                tp.turns as package_total_turns,
                tp.price as package_price,
                ut.turns_remaining,
                ut.total_turns
            FROM qrcode qr
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN userturns ut ON qr.id = ut.qr_code_id
            WHERE qr.code = %s
            """,
            (qr_code,),
        )
        qr_data = cursor.fetchone()

        if not qr_data:
            return api_response('Q001', http_status=404, data={'qr_code': qr_code})

        # Validar que el QR pertenece al local activo del cajero
        active_loc_id, active_loc_name = get_active_location()
        if not (user_can_view_all() and active_loc_id is None) and active_loc_name:
            cursor.execute(
                "SELECT 1 FROM qrhistory WHERE qr_code = %s LIMIT 1", (qr_code,)
            )
            if cursor.fetchone():
                cursor.execute(
                    "SELECT 1 FROM qrhistory WHERE qr_code = %s AND local = %s LIMIT 1",
                    (qr_code, active_loc_name),
                )
                if not cursor.fetchone():
                    return api_response('E004', http_status=403)

        cursor.execute(
            """
            SELECT
                COUNT(*) as total_devoluciones,
                SUM(turnos_devueltos) as turnos_devueltos_total
            FROM machinefailures
            WHERE qr_code_id = %s
            """,
            (qr_data['qr_id'],),
        )
        devolucion_data = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                tu.id as usage_id,
                tu.usedAt as fecha_hora,
                tu.machineId as machine_id,
                m.name as machine_name,
                m.status as machine_status
            FROM turnusage tu
            JOIN machine m ON tu.machineId = m.id
            WHERE tu.qrCodeId = %s
            ORDER BY tu.usedAt DESC
            """,
            (qr_data['qr_id'],),
        )
        juegos = cursor.fetchall()

        historial_juegos = []
        for juego in juegos:
            juego_id = juego['usage_id']
            machine_id = juego['machine_id']
            fecha_juego = juego['fecha_hora']

            cursor.execute(
                """
                SELECT COUNT(*) as usos_previos
                FROM turnusage
                WHERE qrCodeId = %s AND usedAt < %s
                """,
                (qr_data['qr_id'], fecha_juego),
            )
            usos_previos = cursor.fetchone()['usos_previos']
            turnos_antes = (qr_data['total_turns'] or 0) - usos_previos
            turnos_despues = turnos_antes - 1
            if turnos_despues < 0:
                turnos_despues = 0

            cursor.execute(
                """
                SELECT
                    id,
                    turnos_devueltos,
                    is_forced,
                    forced_by,
                    reported_at,
                    notes
                FROM machinefailures
                WHERE qr_code_id = %s
                  AND machine_id = %s
                  AND ABS(TIMESTAMPDIFF(MINUTE, reported_at, %s)) < 30
                ORDER BY reported_at DESC
                LIMIT 1
                """,
                (qr_data['qr_id'], machine_id, fecha_juego),
            )
            falla = cursor.fetchone()

            hubo_falla = falla is not None
            falla_forzada = falla['is_forced'] if falla else False
            falla_id = falla['id'] if falla else None

            cursor.execute(
                """
                SELECT
                    tu.id,
                    tu.usedAt,
                    m.name as machine_name
                FROM turnusage tu
                JOIN machine m ON tu.machineId = m.id
                WHERE tu.machineId = %s
                  AND tu.usedAt > %s
                  AND tu.qrCodeId != %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM machinefailures mf
                      WHERE mf.qr_code_id = tu.qrCodeId
                        AND mf.machine_id = tu.machineId
                        AND ABS(TIMESTAMPDIFF(MINUTE, mf.reported_at, tu.usedAt)) < 30
                  )
                ORDER BY tu.usedAt ASC
                LIMIT 1
                """,
                (machine_id, fecha_juego, qr_data['qr_id']),
            )
            uso_posterior = cursor.fetchone()

            alguien_jugo_despues = uso_posterior is not None
            fecha_uso_posterior = uso_posterior['usedAt'] if uso_posterior else None

            if hubo_falla and not alguien_jugo_despues:
                estado_validacion = 'APTO'
                color_estado = 'green'
                mensaje_estado = 'Apto para devolución - Falla confirmada'
            elif hubo_falla and alguien_jugo_despues:
                estado_validacion = 'NO_APTO'
                color_estado = 'red'
                mensaje_estado = 'No apto - Alguien jugó exitosamente después'
            elif not hubo_falla:
                estado_validacion = 'SIN_REPORTE'
                color_estado = 'yellow'
                mensaje_estado = 'Sin reporte de falla - Verificar con cliente'
            else:
                estado_validacion = 'REVISAR'
                color_estado = 'orange'
                mensaje_estado = 'Revisar caso'

            historial_juegos.append(
                {
                    'usage_id': juego_id,
                    'fecha_hora': fecha_juego.isoformat() if fecha_juego else None,
                    'fecha_formateada': fecha_juego.strftime('%d/%m/%Y %H:%M') if fecha_juego else None,
                    'machine': {
                        'id': machine_id,
                        'nombre': juego['machine_name'],
                        'estado': juego['machine_status'],
                    },
                    'turnos': {'antes': turnos_antes, 'despues': turnos_despues},
                    'falla': {'hubo': hubo_falla, 'forzada': falla_forzada, 'id': falla_id},
                    'uso_posterior': {
                        'hubo': alguien_jugo_despues,
                        'fecha': fecha_uso_posterior.isoformat() if fecha_uso_posterior else None,
                        'fecha_formateada': (
                            fecha_uso_posterior.strftime('%d/%m/%Y %H:%M') if fecha_uso_posterior else None
                        ),
                    },
                    'validacion': {
                        'estado': estado_validacion,
                        'color': color_estado,
                        'mensaje': mensaje_estado,
                    },
                }
            )

        return jsonify(
            {
                'qr': {
                    'id': qr_data['qr_id'],
                    'codigo': qr_data['qr_code'],
                    'nombre': qr_data['qr_name'],
                    'activo': bool(qr_data['isActive']),
                },
                'paquete': {
                    'id': qr_data['turnPackageId'],
                    'nombre': qr_data['package_name'] or 'Sin paquete',
                    'turnos_totales': qr_data['package_total_turns'] or 0,
                    'precio': float(qr_data['package_price'] or 0),
                },
                'turnos': {
                    'totales': qr_data['total_turns'] or 0,
                    'restantes': qr_data['turns_remaining'] or 0,
                    'usados': (qr_data['total_turns'] or 0) - (qr_data['turns_remaining'] or 0),
                },
                'devoluciones': {
                    'total': devolucion_data['total_devoluciones'] or 0,
                    'turnos_devueltos': devolucion_data['turnos_devueltos_total'] or 0,
                },
                'historial': historial_juegos,
                'timestamp': get_colombia_time().isoformat(),
            }
        )
    except Exception as e:
        logger.error(f"Error generando historial completo QR: {e}", exc_info=True)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@devoluciones_bp.route('/api/qr/estado-devolucion/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_estado_devolucion_qr(qr_code):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()

        if not qr_data:
            return api_response('Q001', http_status=404)

        # Validar que el QR pertenece al local activo del cajero
        active_loc_id, active_loc_name = get_active_location()
        if not (user_can_view_all() and active_loc_id is None) and active_loc_name:
            cursor.execute(
                "SELECT 1 FROM qrhistory WHERE qr_code = %s LIMIT 1", (qr_code,)
            )
            if cursor.fetchone():
                cursor.execute(
                    "SELECT 1 FROM qrhistory WHERE qr_code = %s AND local = %s LIMIT 1",
                    (qr_code, active_loc_name),
                )
                if not cursor.fetchone():
                    return api_response('E004', http_status=403)

        qr_id = qr_data['id']
        cursor.execute(
            """
            SELECT
                COUNT(*) as total,
                MAX(reported_at) as ultima,
                SUM(turnos_devueltos) as turnos_devueltos
            FROM machinefailures
            WHERE qr_code_id = %s
            """,
            (qr_id,),
        )
        data = cursor.fetchone()

        return jsonify(
            {
                'qr_code': qr_code,
                'qr_id': qr_id,
                'ya_tuvo_devolucion': data['total'] > 0,
                'total_devoluciones': data['total'] or 0,
                'ultima_devolucion': data['ultima'].isoformat() if data['ultima'] else None,
                'turnos_devueltos_total': data['turnos_devueltos'] or 0,
                'limite': 1,
                'puede_devolver': data['total'] == 0,
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo estado devolución: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@devoluciones_bp.route('/api/qr/procesar-devolucion', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
@validate_required_fields(['qr_code', 'machine_id', 'usage_id'])
def procesar_devolucion_unica():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        qr_code = data['qr_code']
        machine_id = data['machine_id']
        usage_id = data['usage_id']
        is_forced = data.get('is_forced', False)
        forced_by = session.get('user_name', 'Cajero')
        notes = data.get('notes', f'Devolución desde packfailure - Usage ID: {usage_id}')

        logger.info(
            f"Procesando devolución - QR: {qr_code}, Máquina: {machine_id}, Uso: {usage_id}, Forzado: {is_forced}"
        )

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        # Validar que la máquina pertenece al local activo (aplica a cajeros y admins con local seleccionado)
        active_loc_id, _ = get_active_location()
        if active_loc_id and not user_can_view_all():
            cursor.execute("SELECT location_id FROM machine WHERE id = %s", (machine_id,))
            mach_row = cursor.fetchone()
            if not mach_row or mach_row['location_id'] != active_loc_id:
                return api_response('E005', http_status=403, data={
                    'message': 'La máquina no pertenece al local activo'
                })

        cursor.execute("SELECT id, remainingTurns FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()

        if not qr_data:
            return api_response('Q001', http_status=404)

        qr_id = qr_data['id']
        cursor.execute(
            """
            SELECT COUNT(*) as total
            FROM machinefailures
            WHERE qr_code_id = %s
            """,
            (qr_id,),
        )
        devoluciones_existentes = cursor.fetchone()['total']

        if devoluciones_existentes >= 1:
            logger.warning(f"Devolución rechazada - QR {qr_code} ya tuvo devolución")
            return api_response(
                'D001',
                status='error',
                http_status=400,
                data={
                    'qr_code': qr_code,
                    'motivo': 'Este QR ya ha recibido una devolución anteriormente',
                    'limite': 1,
                    'actual': devoluciones_existentes,
                },
            )

        cursor.execute(
            """
            SELECT tu.*, m.name as machine_name
            FROM turnusage tu
            JOIN machine m ON tu.machineId = m.id
            WHERE tu.id = %s AND tu.qrCodeId = %s
            """,
            (usage_id, qr_id),
        )
        uso_data = cursor.fetchone()

        if not uso_data:
            return api_response('E002', http_status=404, data={'message': 'Registro de uso no encontrado'})

        cursor.execute(
            """
            INSERT INTO machinefailures
                (qr_code_id, machine_id, machine_name, turnos_devueltos, notes, is_forced, forced_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                qr_id,
                machine_id,
                uso_data['machine_name'],
                1,
                notes,
                1 if is_forced else 0,
                forced_by if is_forced else None,
            ),
        )
        failure_id = cursor.lastrowid

        cursor.execute(
            """
            UPDATE userturns
            SET turns_remaining = turns_remaining + 1
            WHERE qr_code_id = %s
            """,
            (qr_id,),
        )
        connection.commit()

        cursor.execute("SELECT turns_remaining FROM userturns WHERE qr_code_id = %s", (qr_id,))
        nuevos_turnos = cursor.fetchone()['turns_remaining']

        return api_response(
            'S003',
            status='success',
            data={
                'devolucion_id': failure_id,
                'qr_code': qr_code,
                'machine_id': machine_id,
                'usage_id': usage_id,
                'turnos_devueltos': 1,
                'turnos_restantes': nuevos_turnos,
                'is_forced': is_forced,
                'limite': 1,
                'devoluciones_restantes': 0,
            },
        )
    except Exception as e:
        logger.error(f"Error procesando devolución: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@devoluciones_bp.route('/api/qr-id/<qr_code>', methods=['GET'])
@handle_api_errors
def obtener_id_qr(qr_code):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("SELECT id FROM qrcode WHERE code = %s", (qr_code,))
        qr_data = cursor.fetchone()

        if not qr_data:
            return api_response('Q001', http_status=404)

        return jsonify({'id': qr_data['id']})
    except Exception as e:
        logger.error(f"Error obteniendo ID QR: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@devoluciones_bp.route('/api/historial-juegos/<qr_code>', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_historial_juegos(qr_code):
    connection = None
    cursor = None
    try:
        limit = request.args.get('limit', 5)

        active_loc_id, _ = get_active_location()
        _no_filter = user_can_view_all() and active_loc_id is None
        _loc_clause = "" if _no_filter else " AND m.location_id = %s"
        _loc_p = [] if _no_filter or not active_loc_id else [active_loc_id]

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            f"""
            SELECT tu.usedAt, m.name as machine_name
            FROM qrcode qr
            JOIN turnusage tu ON qr.id = tu.qrCodeId
            JOIN machine m ON tu.machineId = m.id
            WHERE qr.code = %s{_loc_clause}
            ORDER BY tu.usedAt DESC
            LIMIT %s
            """,
            (qr_code, *_loc_p, int(limit)),
        )
        juegos = cursor.fetchall()

        for juego in juegos:
            if juego['usedAt']:
                fecha_colombia = parse_db_datetime(juego['usedAt'])
                juego['usedAt'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')

        return jsonify(juegos)
    except Exception as e:
        logger.error(f"Error obteniendo historial de juegos: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@devoluciones_bp.route('/api/historial-devoluciones/<qr_code>', methods=['GET'])
@handle_api_errors
def obtener_historial_devoluciones(qr_code):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT mf.turnos_devueltos, mf.reported_at, mf.machine_name
            FROM qrcode qr
            JOIN machinefailures mf ON qr.id = mf.qr_code_id
            WHERE qr.code = %s
            ORDER BY mf.reported_at DESC
            """,
            (qr_code,),
        )
        devoluciones = cursor.fetchall()

        for devolucion in devoluciones:
            if devolucion['reported_at']:
                fecha_colombia = parse_db_datetime(devolucion['reported_at'])
                devolucion['reported_at'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')

        return jsonify(devoluciones)
    except Exception as e:
        logger.error(f"Error obteniendo historial de devoluciones: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
