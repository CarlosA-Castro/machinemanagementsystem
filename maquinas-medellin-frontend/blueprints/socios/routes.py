import logging
from datetime import datetime

import sentry_sdk
from flask import Blueprint, request, jsonify, render_template, session

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time
from utils.validators import validate_required_fields
from utils.socios_finance import (
    calcular_utilidad_socio,
    calcular_detalle_por_maquina,
    calcular_detalle_por_local,
    calcular_evolucion_mensual,
    calcular_roi,
    calcular_resumen_todos_socios,
)

logger = logging.getLogger(LOGGER_NAME)

socios_bp = Blueprint('socios', __name__)


def _serialize_value(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _serialize_dict_dates(row, fields):
    if not row:
        return row
    for field in fields:
        if row.get(field):
            row[field] = _serialize_value(row[field])
    return row


def _resolve_socio_by_session(cursor):
    user_role = session.get('user_role')
    user_id = session.get('user_id')
    user_name = session.get('user_name')

    if user_role == 'socio':
        if user_id:
            try:
                cursor.execute("SELECT * FROM socios WHERE user_id = %s LIMIT 1", (user_id,))
                socio = cursor.fetchone()
                if socio:
                    return socio
            except Exception:
                logger.warning("No fue posible resolver socio por user_id=%s", user_id, exc_info=True)

        cursor.execute(
            """
            SELECT * FROM socios
            WHERE nombre = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_name,),
        )
        socio = cursor.fetchone()
        if socio:
            return socio

        return None

    socio_id = request.args.get('socio_id') or session.get('socio_id')
    if socio_id:
        cursor.execute("SELECT * FROM socios WHERE id = %s", (socio_id,))
        return cursor.fetchone()

    if user_id:
        try:
            cursor.execute("SELECT * FROM socios WHERE user_id = %s", (user_id,))
            socio = cursor.fetchone()
            if socio:
                return socio
        except Exception:
            pass

    return None


@socios_bp.route('/socios')
@require_login(['admin', 'socio'])
def mostrar_panel_socio():
    hora_colombia = get_colombia_time()
    return render_template(
        'socios.html',
        nombre_usuario=session.get('user_name', 'Socio'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


@socios_bp.route('/admin/inversores/gestionsocios')
@require_login(['admin'])
def mostrar_gestion_socios():
    hora_colombia = get_colombia_time()
    return render_template(
        'admin/inversores/gestionsocios.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


@socios_bp.route('/api/socio/actual', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'socio'])
def obtener_socio_actual():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        socio = _resolve_socio_by_session(cursor)
        if not socio:
            return api_response('E002', http_status=404, data={'message': 'Socio no encontrado'})

        socio = dict(socio)
        socio['tipo_socio'] = 'inversionista'
        _serialize_dict_dates(socio, ['fecha_inscripcion', 'fecha_vencimiento'])
        if 'cuota_anual' in socio:
            socio['cuota_anual'] = float(socio.get('cuota_anual', 0) or 0)
        if 'porcentaje_global' in socio:
            socio['porcentaje_global'] = float(socio.get('porcentaje_global', 0) or 0)
        return jsonify(socio)
    except Exception as e:
        logger.error(f"Error obteniendo socio actual: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_todos_socios():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM socios ORDER BY fecha_inscripcion DESC")
        return jsonify(cursor.fetchall())
    except Exception as e:
        logger.error(f"Error obteniendo socios: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/completos', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_socios_completos():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT
                s.*,
                COALESCE(SUM(i.monto_inicial), 0) as inversion_total,
                COUNT(DISTINCT i.id) as total_inversiones,
                COUNT(CASE WHEN i.estado = 'activa' THEN 1 END) as inversiones_activas,
                COUNT(DISTINCT pc.id) as total_pagos,
                COUNT(CASE WHEN pc.estado = 'pendiente' THEN 1 END) as pagos_pendientes
            FROM socios s
            LEFT JOIN inversiones i ON s.id = i.socio_id
            LEFT JOIN pagoscuotas pc ON s.id = pc.socio_id
            GROUP BY s.id
            ORDER BY s.fecha_inscripcion DESC
            """
        )
        socios = cursor.fetchall()
        for socio in socios:
            _serialize_dict_dates(socio, ['fecha_inscripcion', 'fecha_vencimiento', 'created_at', 'updated_at'])
        return jsonify(socios)
    except Exception as e:
        logger.error(f"Error obteniendo socios completos: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/<int:socio_id>', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_socio(socio_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT * FROM socios WHERE id = %s", (socio_id,))
        socio = cursor.fetchone()
        if not socio:
            return api_response('E002', http_status=404, data={'socio_id': socio_id})

        cursor.execute(
            """
            SELECT i.*, m.name as maquina_nombre
            FROM inversiones i
            LEFT JOIN machine m ON i.maquina_id = m.id
            WHERE i.socio_id = %s
            ORDER BY i.fecha_inicio DESC
            """,
            (socio_id,),
        )
        inversiones = cursor.fetchall()

        cursor.execute(
            """
            SELECT * FROM pagoscuotas
            WHERE socio_id = %s
            ORDER BY anio DESC, created_at DESC
            """,
            (socio_id,),
        )
        pagos = cursor.fetchall()

        _serialize_dict_dates(socio, ['fecha_inscripcion', 'fecha_vencimiento'])
        for inversion in inversiones:
            _serialize_dict_dates(inversion, ['fecha_inicio', 'fecha_fin'])
        for pago in pagos:
            _serialize_dict_dates(pago, ['fecha_pago'])

        return jsonify({'socio': socio, 'inversiones': inversiones, 'pagos': pagos})
    except Exception as e:
        logger.error(f"Error obteniendo socio: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['nombre', 'documento', 'fecha_inscripcion', 'fecha_vencimiento'])
def crear_socio():
    connection = None
    cursor = None
    try:
        data = request.get_json()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute(
            "SELECT MAX(CAST(SUBSTRING(codigo_socio, 5) AS UNSIGNED)) as max_num FROM socios WHERE codigo_socio LIKE 'SOC-%'"
        )
        max_num = cursor.fetchone()
        codigo_socio = f"SOC-{((max_num['max_num'] or 0) + 1):04d}"

        cursor.execute(
            """
            INSERT INTO socios (
                codigo_socio, nombre, documento, tipo_documento, telefono, email,
                direccion, fecha_inscripcion, fecha_vencimiento, cuota_anual,
                estado, notas, porcentaje_global
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                codigo_socio,
                data['nombre'],
                data['documento'],
                data.get('tipo_documento', 'CC'),
                data.get('telefono', ''),
                data.get('email', ''),
                data.get('direccion', ''),
                data['fecha_inscripcion'],
                data['fecha_vencimiento'],
                data.get('cuota_anual', 0),
                data.get('estado', 'activo'),
                data.get('notas', ''),
                data.get('porcentaje_global', 0),
            ),
        )
        socio_id = cursor.lastrowid
        connection.commit()
        logger.info(f"Socio creado: {data['nombre']} (Código: {codigo_socio})")
        return api_response('S002', status='success', data={'socio_id': socio_id, 'codigo_socio': codigo_socio})
    except Exception as e:
        logger.error(f"Error creando socio: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/<int:socio_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['nombre', 'documento', 'fecha_inscripcion', 'fecha_vencimiento'])
def actualizar_socio(socio_id):
    connection = None
    cursor = None
    try:
        data = request.get_json()
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute("SELECT id FROM socios WHERE id = %s", (socio_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'socio_id': socio_id})

        cursor.execute(
            """
            UPDATE socios SET
                nombre = %s,
                documento = %s,
                tipo_documento = %s,
                telefono = %s,
                email = %s,
                direccion = %s,
                fecha_inscripcion = %s,
                fecha_vencimiento = %s,
                cuota_anual = %s,
                estado = %s,
                notas = %s,
                porcentaje_global = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                data['nombre'],
                data['documento'],
                data.get('tipo_documento', 'CC'),
                data.get('telefono', ''),
                data.get('email', ''),
                data.get('direccion', ''),
                data['fecha_inscripcion'],
                data['fecha_vencimiento'],
                data.get('cuota_anual', 0),
                data.get('estado', 'activo'),
                data.get('notas', ''),
                data.get('porcentaje_global', 0),
                socio_id,
            ),
        )
        connection.commit()
        logger.info(f"Socio actualizado: {data['nombre']} (ID: {socio_id})")
        return api_response('S003', status='success')
    except Exception as e:
        logger.error(f"Error actualizando socio: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/<int:socio_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_socio(socio_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT nombre FROM socios WHERE id = %s", (socio_id,))
        socio = cursor.fetchone()
        if not socio:
            return api_response('E002', http_status=404, data={'socio_id': socio_id})

        cursor.execute(
            "SELECT COUNT(*) as inversiones_activas FROM inversiones WHERE socio_id = %s AND estado = 'activa'",
            (socio_id,),
        )
        inversiones = cursor.fetchone()
        if inversiones['inversiones_activas'] > 0:
            return api_response(
                'W006',
                status='warning',
                http_status=400,
                data={
                    'message': f'El socio tiene {inversiones["inversiones_activas"]} inversiones activas',
                    'inversiones_activas': inversiones['inversiones_activas'],
                },
            )

        cursor.execute("DELETE FROM pagoscuotas WHERE socio_id = %s", (socio_id,))
        cursor.execute("UPDATE inversiones SET estado = 'finalizada' WHERE socio_id = %s", (socio_id,))
        cursor.execute("DELETE FROM socios WHERE id = %s", (socio_id,))
        connection.commit()
        logger.info(f"Socio eliminado: {socio['nombre']} (ID: {socio_id})")
        return api_response('S004', status='success')
    except Exception as e:
        logger.error(f"Error eliminando socio: {e}")
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/estadisticas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_estadisticas_socios():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute(
            """
            SELECT
                COUNT(*) as total_socios,
                COUNT(CASE WHEN estado = 'activo' THEN 1 END) as socios_activos,
                COUNT(CASE WHEN estado = 'inactivo' THEN 1 END) as socios_inactivos,
                COUNT(CASE WHEN estado = 'pendiente_pago' THEN 1 END) as socios_pendientes,
                SUM(cuota_anual) as cuota_anual_total,
                SUM(porcentaje_global) as porcentaje_total
            FROM socios
            """
        )
        stats = cursor.fetchone()

        cursor.execute("SELECT COALESCE(SUM(monto_inicial), 0) as inversion_total FROM inversiones WHERE estado = 'activa'")
        inversion = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) as cuotas_pendientes FROM pagoscuotas WHERE estado = 'pendiente'")
        pendientes = cursor.fetchone()

        return jsonify(
            {
                'total_socios': stats['total_socios'] or 0,
                'socios_activos': stats['socios_activos'] or 0,
                'socios_inactivos': stats['socios_inactivos'] or 0,
                'socios_pendientes': stats['socios_pendientes'] or 0,
                'cuota_anual_total': float(stats['cuota_anual_total'] or 0),
                'inversion_total': float(inversion['inversion_total'] or 0),
                'cuotas_pendientes': pendientes['cuotas_pendientes'] or 0,
                'porcentaje_total': float(stats['porcentaje_total'] or 0),
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas de socios: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/top', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_top_socios():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT
                s.id,
                s.codigo_socio,
                s.nombre,
                s.documento,
                s.estado,
                COALESCE(SUM(i.monto_inicial), 0) as inversion_total,
                COUNT(i.id) as total_inversiones
            FROM socios s
            LEFT JOIN inversiones i ON s.id = i.socio_id AND i.estado = 'activa'
            GROUP BY s.id, s.codigo_socio, s.nombre, s.documento, s.estado
            HAVING COALESCE(SUM(i.monto_inicial), 0) > 0
            ORDER BY inversion_total DESC
            LIMIT 10
            """
        )
        return jsonify(cursor.fetchall())
    except Exception as e:
        logger.error(f"Error obteniendo top socios: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/recientes', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_socios_recientes():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT id, codigo_socio, nombre, documento, fecha_inscripcion, estado, cuota_anual
            FROM socios
            ORDER BY fecha_inscripcion DESC
            LIMIT 10
            """
        )
        return jsonify(cursor.fetchall())
    except Exception as e:
        logger.error(f"Error obteniendo socios recientes: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/<int:socio_id>/inversiones', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_inversiones_socio(socio_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT
                i.*,
                m.name as maquina_nombre,
                m.type as maquina_tipo,
                l.name as ubicacion
            FROM inversiones i
            LEFT JOIN machine m ON i.maquina_id = m.id
            LEFT JOIN location l ON m.location_id = l.id
            WHERE i.socio_id = %s
            ORDER BY i.fecha_inicio DESC
            """,
            (socio_id,),
        )
        return jsonify(cursor.fetchall())
    except Exception as e:
        logger.error(f"Error obteniendo inversiones de socio: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/<int:socio_id>/pagos', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_pagos_socio(socio_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT * FROM pagoscuotas
            WHERE socio_id = %s
            ORDER BY anio DESC, created_at DESC
            """,
            (socio_id,),
        )
        return jsonify(cursor.fetchall())
    except Exception as e:
        logger.error(f"Error obteniendo pagos de socio: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/<int:socio_id>/ingresos/ultimos', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_ultimos_ingresos_socio(socio_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        cursor.execute(
            """
            SELECT '2024-01' as fecha_periodo, 'Máquina A' as maquina_nombre, 1000.00 as ganancia_neta, TRUE as liquidado
            UNION ALL
            SELECT '2023-12', 'Máquina B', 850.50, TRUE
            UNION ALL
            SELECT '2023-11', 'Todas', 1250.75, FALSE
            LIMIT 5
            """
        )
        return jsonify(cursor.fetchall())
    except Exception as e:
        logger.error(f"Error obteniendo ingresos de socio: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socio/panel/estadisticas', methods=['GET'])
@handle_api_errors
@require_login(['socio'])
def obtener_estadisticas_panel_socio():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        socio = _resolve_socio_by_session(cursor)
        if not socio or not socio.get('id'):
            return jsonify(
                {
                    'message': 'No se encontró información de socio asociada a tu usuario',
                    'requiere_configuracion': True,
                }
            ), 404

        socio_id = socio['id']
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(i.monto_inicial), 0) as total_invertido,
                COUNT(i.id) as total_inversiones,
                COUNT(CASE WHEN i.estado = 'activa' THEN 1 END) as inversiones_activas,
                COALESCE(SUM(i.monto_inicial * i.porcentaje_inversion / 100), 0) as inversion_personal
            FROM inversiones i
            WHERE i.socio_id = %s
            """,
            (socio_id,),
        )
        inversiones_stats = cursor.fetchone()

        cursor.execute(
            """
            SELECT
                MONTH(fecha_hora) as mes,
                YEAR(fecha_hora) as anio,
                COALESCE(SUM(tp.price * i.porcentaje_inversion / 100), 0) as ingresos_mensuales
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            JOIN inversiones i ON i.maquina_id = (
                SELECT tu.machineId
                FROM turnusage tu
                JOIN qrcode qr2 ON qr2.id = tu.qrCodeId
                WHERE qr2.code = qh.qr_code
                LIMIT 1
            )
            WHERE DATE(qh.fecha_hora) >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
              AND i.socio_id = %s
              AND qh.es_venta_real = TRUE
            GROUP BY YEAR(fecha_hora), MONTH(fecha_hora)
            ORDER BY anio DESC, mes DESC
            LIMIT 12
            """,
            (socio_id,),
        )
        ingresos_mensuales = cursor.fetchall()

        total_invertido = float(inversiones_stats['total_invertido'] or 0)
        roi_total = calcular_roi(cursor, socio_id)
        if ingresos_mensuales:
            ingreso_mensual_promedio = float(sum(
                i['ingresos_mensuales'] for i in ingresos_mensuales
            )) / len(ingresos_mensuales)
        else:
            ingreso_mensual_promedio = 0

        cuota_anual = float(socio.get('cuota_anual', 0) or 0)
        estado_cuota = 'pendiente' if socio['estado'] == 'pendiente_pago' else 'al_dia'

        return jsonify(
            {
                'socio': {
                    'id': socio['id'],
                    'codigo_socio': socio['codigo_socio'],
                    'nombre': socio['nombre'],
                    'documento': socio['documento'],
                    'email': socio.get('email', ''),
                    'telefono': socio.get('telefono', ''),
                    'fecha_inscripcion': _serialize_value(socio['fecha_inscripcion']) if socio.get('fecha_inscripcion') else None,
                    'fecha_vencimiento': _serialize_value(socio['fecha_vencimiento']) if socio.get('fecha_vencimiento') else None,
                    'estado': socio['estado'],
                    'cuota_anual': cuota_anual,
                    'porcentaje_global': float(socio.get('porcentaje_global', 0) or 0),
                },
                'estadisticas': {
                    'total_invertido': total_invertido,
                    'inversion_personal': float(inversiones_stats['inversion_personal'] or 0),
                    'total_inversiones': inversiones_stats['total_inversiones'] or 0,
                    'inversiones_activas': inversiones_stats['inversiones_activas'] or 0,
                    'ingreso_mensual': ingreso_mensual_promedio,
                    'roi_total': roi_total,
                    'estado_cuota': estado_cuota,
                },
                'ingresos_mensuales': [float(i['ingresos_mensuales']) for i in reversed(ingresos_mensuales)] if ingresos_mensuales else [0] * 12,
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas panel socio: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socio/panel/maquinas', methods=['GET'])
@handle_api_errors
@require_login(['socio'])
def obtener_maquinas_socio_panel():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        socio = _resolve_socio_by_session(cursor)
        if not socio or not socio.get('id'):
            return jsonify([])
        socio_id = socio['id']

        hoy = get_colombia_time().date()
        inicio_mes = hoy.replace(day=1)
        detalle_financiero = calcular_detalle_por_maquina(cursor, socio_id, inicio_mes, hoy)
        detalle_por_maquina = {item['maquina_id']: item for item in detalle_financiero}

        cursor.execute(
            """
            SELECT
                i.id as inversion_id,
                i.porcentaje_inversion,
                i.monto_inicial,
                i.fecha_inicio,
                i.estado,
                m.id as maquina_id,
                m.name as maquina_nombre,
                m.type as maquina_tipo,
                l.name as ubicacion
            FROM inversiones i
            JOIN machine m ON i.maquina_id = m.id
            LEFT JOIN location l ON m.location_id = l.id
            WHERE i.socio_id = %s
              AND i.estado = 'activa'
            ORDER BY i.fecha_inicio DESC
            """,
            (socio_id,),
        )
        maquinas = cursor.fetchall()
        maquinas_formateadas = []
        for maquina in maquinas:
            detalle = detalle_por_maquina.get(maquina['maquina_id'], {})
            inversion_inicial = float(maquina['monto_inicial'] or 0)
            participacion = float(detalle.get('participacion') or 0)
            rentabilidad = round((participacion / inversion_inicial) * 100, 2) if inversion_inicial > 0 else 0.0
            maquinas_formateadas.append(
                {
                    'id': maquina['maquina_id'],
                    'nombre': maquina['maquina_nombre'],
                    'tipo': maquina['maquina_tipo'],
                    'ubicacion': maquina['ubicacion'],
                    'porcentaje_propiedad': float(maquina['porcentaje_inversion']),
                    'inversion_inicial': inversion_inicial,
                    'ingreso_mensual': participacion,
                    'ingreso_bruto': float(detalle.get('ingreso_bruto') or 0),
                    'turnos_jugados': int(detalle.get('turnos_jugados') or 0),
                    'rentabilidad': rentabilidad,
                    'fecha_adquisicion': _serialize_value(maquina['fecha_inicio']) if maquina.get('fecha_inicio') else None,
                    'estado': maquina['estado'],
                }
            )
        return jsonify(maquinas_formateadas)
    except Exception as e:
        logger.error(f"Error obteniendo máquinas panel socio: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socio/panel/ingresos', methods=['GET'])
@handle_api_errors
@require_login(['socio'])
def obtener_ingresos_socio_panel():
    connection = None
    cursor = None
    try:
        pagina = int(request.args.get('pagina', 1))
        por_pagina = int(request.args.get('por_pagina', 10))
        offset = (pagina - 1) * por_pagina

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        socio = _resolve_socio_by_session(cursor)
        if not socio or not socio.get('id'):
            return jsonify({'ingresos': [], 'total': 0})
        socio_id = socio['id']

        cursor.execute(
            """
            SELECT
                DATE_FORMAT(qh.fecha_hora, '%Y-%m') as periodo,
                DATE_FORMAT(qh.fecha_hora, '%M %Y') as periodo_nombre,
                m.name as maquina_nombre,
                COUNT(DISTINCT qh.qr_code) as turnos_totales,
                COALESCE(SUM(tp.price), 0) as ingresos_brutos,
                i.porcentaje_inversion as porcentaje_propiedad,
                COALESCE(SUM(tp.price * i.porcentaje_inversion / 100), 0) as ganancia_neta,
                TRUE as liquidado
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            JOIN turnusage tu ON qr.id = tu.qrCodeId
            JOIN machine m ON tu.machineId = m.id
            JOIN inversiones i ON i.maquina_id = m.id AND i.socio_id = %s
            WHERE qh.es_venta_real = TRUE
            GROUP BY DATE_FORMAT(qh.fecha_hora, '%Y-%m'), m.name, i.porcentaje_inversion
            ORDER BY periodo DESC
            LIMIT %s OFFSET %s
            """,
            (socio_id, por_pagina, offset),
        )
        ingresos = cursor.fetchall()

        cursor.execute(
            """
            SELECT COUNT(DISTINCT CONCAT(DATE_FORMAT(qh.fecha_hora, '%Y-%m'), m.name)) as total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnusage tu ON qr.id = tu.qrCodeId
            JOIN machine m ON tu.machineId = m.id
            JOIN inversiones i ON i.maquina_id = m.id AND i.socio_id = %s
            WHERE qh.es_venta_real = TRUE
            """,
            (socio_id,),
        )
        total = cursor.fetchone()['total'] or 0
        return jsonify(
            {
                'ingresos': ingresos,
                'total': total,
                'pagina': pagina,
                'por_pagina': por_pagina,
                'total_paginas': (total + por_pagina - 1) // por_pagina,
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo ingresos panel socio: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socio/panel/pagos', methods=['GET'])
@handle_api_errors
@require_login(['socio'])
def obtener_pagos_socio_panel():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        socio = _resolve_socio_by_session(cursor)
        if not socio or not socio.get('id'):
            return jsonify([])
        socio_id = socio['id']

        cursor.execute(
            """
            SELECT
                pc.id,
                pc.anio,
                pc.monto,
                pc.fecha_pago,
                pc.metodo_pago,
                pc.comprobante,
                pc.estado,
                DATE_ADD(DATE(CONCAT(pc.anio, '-01-01')), INTERVAL 30 DAY) as fecha_vencimiento,
                'cuota_anual' as tipo_pago
            FROM pagoscuotas pc
            WHERE pc.socio_id = %s
              AND pc.estado = 'pendiente'
            ORDER BY pc.anio DESC
            LIMIT 10
            """,
            (socio_id,),
        )
        pagos = cursor.fetchall()
        pagos_formateados = []
        for pago in pagos:
            pagos_formateados.append(
                {
                    'id': pago['id'],
                    'tipo_pago': pago['tipo_pago'],
                    'monto': float(pago['monto']),
                    'fecha_pago': _serialize_value(pago['fecha_pago']) if pago.get('fecha_pago') else None,
                    'fecha_vencimiento': _serialize_value(pago['fecha_vencimiento']) if pago.get('fecha_vencimiento') else None,
                    'metodo_pago': pago['metodo_pago'],
                    'comprobante': pago['comprobante'],
                    'estado': pago['estado'],
                    'anio': pago['anio'],
                }
            )
        return jsonify(pagos_formateados)
    except Exception as e:
        logger.error(f"Error obteniendo pagos panel socio: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


# ─── Endpoints financieros reales ─────────────────────────────────────────────

def _parse_periodo(args):
    """Lee fecha_inicio / fecha_fin de query params; por defecto mes actual."""
    hoy = get_colombia_time().date()
    inicio_mes = hoy.replace(day=1)
    try:
        fi = datetime.strptime(args.get('fecha_inicio', str(inicio_mes)), '%Y-%m-%d').date()
    except ValueError:
        fi = inicio_mes
    try:
        ff = datetime.strptime(args.get('fecha_fin', str(hoy)), '%Y-%m-%d').date()
    except ValueError:
        ff = hoy
    if fi > ff:
        fi, ff = ff, fi
    return fi, ff


@socios_bp.route('/api/socios/resumen-financiero', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def resumen_financiero_socios():
    """Todos los socios activos con su participación real en el período."""
    connection = None
    cursor = None
    try:
        fecha_inicio, fecha_fin = _parse_periodo(request.args)
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        datos = calcular_resumen_todos_socios(cursor, fecha_inicio, fecha_fin)
        return jsonify({
            'fecha_inicio': str(fecha_inicio),
            'fecha_fin':    str(fecha_fin),
            'socios':       datos,
        })
    except Exception as e:
        logger.error(f"Error resumen financiero socios: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/por-local', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def socios_por_local():
    """Socios filtrados por local: resuelto via inversiones → machine → location."""
    connection = None
    cursor = None
    try:
        local_id = request.args.get('local_id', type=int)
        fecha_inicio, fecha_fin = _parse_periodo(request.args)
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        # Locales disponibles (para el selector en el front)
        cursor.execute(
            """
            SELECT DISTINCT l.id, l.name
            FROM location l
            JOIN machine m ON m.location_id = l.id
            JOIN inversiones i ON i.maquina_id = m.id AND i.estado = 'activa'
            ORDER BY l.name
            """
        )
        locales = [{'id': r['id'], 'name': r['name']} for r in cursor.fetchall()]

        # Socios del local (o todos si no se filtra)
        where_local = 'AND l.id = %s' if local_id else ''
        params = [fecha_inicio, fecha_fin]
        if local_id:
            params.append(local_id)

        cursor.execute(
            f"""
            SELECT
                s.id AS socio_id, s.nombre, s.codigo_socio, s.estado,
                l.id AS local_id, l.name AS local_nombre,
                COALESCE(SUM(i.monto_inicial), 0) AS inversion_total,
                COUNT(DISTINCT i.id)               AS maquinas_activas
            FROM socios s
            JOIN inversiones i ON i.socio_id = s.id AND i.estado = 'activa'
            JOIN machine m ON i.maquina_id = m.id
            JOIN location l ON m.location_id = l.id
            WHERE s.estado = 'activo'
              {where_local}
            GROUP BY s.id, s.nombre, s.codigo_socio, s.estado, l.id, l.name
            ORDER BY s.nombre
            """,
            params,
        )
        socios = [dict(r) for r in cursor.fetchall()]
        return jsonify({'locales': locales, 'socios': socios, 'local_id': local_id})
    except Exception as e:
        logger.error(f"Error socios por local: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/<int:socio_id>/financiero', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def detalle_financiero_socio(socio_id):
    """Detalle financiero completo de un socio: período, por máquina, por local, ROI."""
    connection = None
    cursor = None
    try:
        fecha_inicio, fecha_fin = _parse_periodo(request.args)
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        cursor.execute('SELECT * FROM socios WHERE id = %s', (socio_id,))
        socio = cursor.fetchone()
        if not socio:
            return api_response('E002', http_status=404, data={'message': 'Socio no encontrado'})
        socio = dict(socio)
        _serialize_dict_dates(socio, ['fecha_inscripcion', 'fecha_vencimiento'])

        utilidad   = calcular_utilidad_socio(cursor, socio_id, fecha_inicio, fecha_fin)
        por_local  = calcular_detalle_por_local(cursor, socio_id, fecha_inicio, fecha_fin)
        roi        = calcular_roi(cursor, socio_id)
        evolucion  = calcular_evolucion_mensual(cursor, socio_id, meses=6)

        cursor.execute(
            """
            SELECT COALESCE(SUM(monto_inicial), 0) AS total,
                   COUNT(*)                         AS cantidad
            FROM inversiones WHERE socio_id = %s AND estado = 'activa'
            """,
            (socio_id,),
        )
        inv = cursor.fetchone()

        return jsonify({
            'socio':          socio,
            'periodo':        {'fecha_inicio': str(fecha_inicio), 'fecha_fin': str(fecha_fin)},
            'financiero':     utilidad,
            'por_local':      por_local,
            'roi':            roi,
            'evolucion':      evolucion,
            'inversion_total': float(inv['total'] or 0),
            'maquinas_activas': int(inv['cantidad'] or 0),
        })
    except Exception as e:
        logger.error(f"Error detalle financiero socio {socio_id}: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socios/<int:socio_id>/evolucion', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'socio'])
def evolucion_socio(socio_id):
    """Evolución mensual de la participación del socio (últimos N meses)."""
    connection = None
    cursor = None
    try:
        meses = min(int(request.args.get('meses', 6)), 24)
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)
        datos = calcular_evolucion_mensual(cursor, socio_id, meses=meses)
        return jsonify(datos)
    except Exception as e:
        logger.error(f"Error evolución socio {socio_id}: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@socios_bp.route('/api/socio/panel/financiero', methods=['GET'])
@handle_api_errors
@require_login(['socio'])
def panel_financiero_socio_actual():
    """Panel del socio autenticado: utilidad real del período + evolución."""
    connection = None
    cursor = None
    try:
        fecha_inicio, fecha_fin = _parse_periodo(request.args)
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)
        cursor = get_db_cursor(connection)

        socio = _resolve_socio_by_session(cursor)
        if not socio or not socio.get('id'):
            return api_response('E002', http_status=404, data={'message': 'Socio no encontrado'})
        socio_id = socio['id']

        utilidad  = calcular_utilidad_socio(cursor, socio_id, fecha_inicio, fecha_fin)
        por_local = calcular_detalle_por_local(cursor, socio_id, fecha_inicio, fecha_fin)
        evolucion = calcular_evolucion_mensual(cursor, socio_id, meses=6)
        roi       = calcular_roi(cursor, socio_id)

        cursor.execute(
            """
            SELECT COALESCE(SUM(monto_inicial), 0) AS total
            FROM inversiones WHERE socio_id = %s AND estado = 'activa'
            """,
            (socio_id,),
        )
        inv_row = cursor.fetchone()

        return jsonify({
            'socio': {
                'id':               socio['id'],
                'nombre':           socio['nombre'],
                'codigo_socio':     socio['codigo_socio'],
                'estado':           socio['estado'],
                'cuota_anual':      float(socio.get('cuota_anual', 0) or 0),
                'porcentaje_global': float(socio.get('porcentaje_global', 0) or 0),
            },
            'periodo':        {'fecha_inicio': str(fecha_inicio), 'fecha_fin': str(fecha_fin)},
            'financiero':     utilidad,
            'por_local':      por_local,
            'evolucion':      evolucion,
            'roi':            roi,
            'inversion_total': float(inv_row['total'] or 0),
        })
    except Exception as e:
        logger.error(f"Error panel financiero socio actual: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
