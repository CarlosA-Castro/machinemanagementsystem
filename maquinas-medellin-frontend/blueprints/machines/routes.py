import logging
import os

import sentry_sdk
from flask import Blueprint, request, jsonify, session, json

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import api_response, handle_api_errors
from utils.validators import validate_required_fields
from utils.timezone import get_colombia_time, format_datetime_for_db, parse_db_datetime
from utils.helpers import parse_json_col
from blueprints.esp32.state import get_heartbeat_fields

logger = logging.getLogger(LOGGER_NAME)

machines_bp = Blueprint('machines', __name__)


# ── Helper local ──────────────────────────────────────────────────────────────

def _nombre_imagen(nombre_maquina: str) -> str:
    """Mapea el nombre de una máquina al nombre de su archivo de imagen."""
    mapa = {
        'Simulador connection': 'simulador pk.jpg',
        'Simulador Cruisin 1':  'simulador1.jpg',
        'Simulador Cruisin 2':  'simulador2.jpg',
        'Peluches 1':           'peluches1.jpg',
        'Peluches 2':           'peluches2.jpg',
        'Basketball':           'basketball.jpg',
        'Pelea':                'pelea.jpg',
        'Disco hockey':         'disco hockey.jpg',
        'Sillas masajes':       'sillas de masajes.jpg',
        'Mcqueen':              'mcqueen.jpg',
        'Caballito':            'caballo.jpg',
        'Trencito':             'tren.jpg',
        'Basketball 2':         'basketball 2.jpg',
        'Disco Air Hockey':     'disco air hockey.jpg',
    }
    for key, filename in mapa.items():
        if key.lower() in nombre_maquina.lower() or nombre_maquina.lower() in key.lower():
            return filename
    return 'default.jpg'


# ── Listados ──────────────────────────────────────────────────────────────────

@machines_bp.route('/api/maquinas', methods=['GET'])
@handle_api_errors
def obtener_maquinas():
    """Obtener todas las máquinas con información completa."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        try:
            cursor.execute("""
                SELECT
                    m.id, m.name, m.type, m.status, m.location_id,
                    m.dailyFailedTurns, m.dateLastQRUsed, m.errorNote,
                    m.stations_in_maintenance, m.consecutive_failures,
                    l.name as location_name,
                    COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante,
                    mt.machine_subtype, mt.station_names
                FROM machine m
                LEFT JOIN location l ON m.location_id = l.id
                LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                ORDER BY m.name
            """)
        except Exception:
            # Fallback si las columnas de V32 aún no existen
            cursor.execute("""
                SELECT
                    m.id, m.name, m.type, m.status, m.location_id,
                    m.dailyFailedTurns, m.dateLastQRUsed, m.errorNote,
                    NULL AS stations_in_maintenance, NULL AS consecutive_failures,
                    l.name as location_name,
                    COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante,
                    mt.machine_subtype, mt.station_names
                FROM machine m
                LEFT JOIN location l ON m.location_id = l.id
                LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
                ORDER BY m.name
            """)

        maquinas = cursor.fetchall()
        resultado = []

        for m in maquinas:
            cursor.execute("""
                SELECT p.id, p.nombre, mp.porcentaje_propiedad
                FROM maquinapropietario mp
                JOIN propietarios p ON mp.propietario_id = p.id
                WHERE mp.maquina_id = %s
            """, (m['id'],))
            propietarios = cursor.fetchall()

            info_prop = ", ".join(
                f"{p['nombre']} ({p['porcentaje_propiedad']}%)" for p in propietarios
            ) if propietarios else "Sin propietarios"

            resultado.append({
                'id':                       m['id'],
                'name':                     m['name'],
                'type':                     m['type'],
                'status':                   m['status'],
                'location_id':              m['location_id'],
                'location_name':            m['location_name'],
                'dailyFailedTurns':         m['dailyFailedTurns'],
                'dateLastQRUsed':           m['dateLastQRUsed'].isoformat() if m['dateLastQRUsed'] else None,
                'errorNote':                m['errorNote'],
                'porcentaje_restaurante':   float(m['porcentaje_restaurante']),
                'propietarios':             propietarios,
                'info_propietarios':        info_prop,
                'machine_subtype':          m.get('machine_subtype', 'simple') or 'simple',
                'station_names':            parse_json_col(m.get('station_names'), []),
                'stations_in_maintenance':  parse_json_col(m.get('stations_in_maintenance'), []),
                'consecutive_failures':     parse_json_col(m.get('consecutive_failures'), {}),
                **get_heartbeat_fields(m['id']),
            })

        return jsonify(resultado)

    except Exception as e:
        logger.error(f"Error obteniendo máquinas: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/maquinas-por-tipo', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_maquinas_por_tipo():
    """Obtener todas las máquinas organizadas por tipo (para machinereport.html)."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT
                m.id, m.name, m.type, m.status, m.location_id,
                l.name as location_name,
                COALESCE(m.dailyFailedTurns, 0) as dailyFailedTurns,
                m.dateLastQRUsed,
                COALESCE(m.valor_por_turno, 3000.00) as valor_por_turno
            FROM machine m
            LEFT JOIN location l ON m.location_id = l.id
            WHERE m.status IN ('activa', 'mantenimiento', 'inactiva')
            ORDER BY m.type, m.name
        """)
        maquinas = cursor.fetchall()

        resultado = {'arcade': [], 'simulador': [], 'peluchera': [], 'otros': []}

        for m in maquinas:
            info = {
                'id':              m['id'],
                'name':            m['name'],
                'type':            m['type'],
                'status':          m['status'],
                'location_id':     m['location_id'],
                'location_name':   m['location_name'],
                'dailyFailedTurns': m['dailyFailedTurns'],
                'dateLastQRUsed':  m['dateLastQRUsed'].isoformat() if m['dateLastQRUsed'] else None,
                'valor_por_turno': float(m['valor_por_turno']),
                'imagen':          _nombre_imagen(m['name']),
            }
            tipo = (m['type'] or 'otros').lower()
            resultado.setdefault(tipo, resultado['otros']).append(info) if tipo not in resultado else resultado[tipo].append(info)

        return jsonify({
            'status': 'success',
            'data': resultado,
            'totales': {k: len(v) for k, v in resultado.items()} | {'total': len(maquinas)},
        })

    except Exception as e:
        logger.error(f"Error obteniendo máquinas por tipo: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/maquinas/<int:maquina_id>', methods=['GET'])
@handle_api_errors
def obtener_maquina(maquina_id):
    """Obtener una máquina específica con información completa."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        cursor.execute("""
            SELECT
                m.*, l.name as location_name,
                COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante,
                mt.machine_subtype, mt.station_names,
                mt.has_failure_report, mt.show_station_selection
            FROM machine m
            LEFT JOIN location l ON m.location_id = l.id
            LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
            LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
            WHERE m.id = %s
        """, (maquina_id,))
        maquina = cursor.fetchone()

        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})

        cursor.execute("""
            SELECT p.id, p.nombre, mp.porcentaje_propiedad
            FROM maquinapropietario mp
            JOIN propietarios p ON mp.propietario_id = p.id
            WHERE mp.maquina_id = %s
        """, (maquina_id,))
        propietarios = cursor.fetchall()

        # Fallas activas por estación
        cursor.execute("""
            SELECT station_index, COUNT(*) as count
            FROM errorreport
            WHERE machineId = %s AND isResolved = 0
            GROUP BY station_index
        """, (maquina_id,))
        failure_rows = cursor.fetchall()
        active_failure_stations = []
        machine_level_failures = 0
        for row in failure_rows:
            if row['station_index'] is None:
                machine_level_failures += row['count']
            else:
                active_failure_stations.append({
                    'station_index': row['station_index'],
                    'count':         row['count'],
                    'cajero_count':  row['count'],
                    'esp32_count':   0,
                })

        # Tiempo desde último uso
        ultimo_uso_texto = "Nunca"
        if maquina['dateLastQRUsed']:
            try:
                fecha_ultimo = parse_db_datetime(maquina['dateLastQRUsed'])
                ahora = get_colombia_time()
                diff = ahora - fecha_ultimo
                if diff.days > 0:
                    ultimo_uso_texto = f"Hace {diff.days} días"
                elif diff.seconds > 3600:
                    ultimo_uso_texto = f"Hace {diff.seconds // 3600} horas"
                elif diff.seconds > 60:
                    ultimo_uso_texto = f"Hace {diff.seconds // 60} minutos"
                else:
                    ultimo_uso_texto = "Hace unos segundos"
            except Exception:
                ultimo_uso_texto = maquina['dateLastQRUsed'].strftime('%Y-%m-%d %H:%M')

        return jsonify({
            'id':                       maquina['id'],
            'name':                     maquina['name'],
            'type':                     maquina['type'],
            'status':                   maquina['status'],
            'location_id':              maquina['location_id'],
            'location_name':            maquina['location_name'],
            'dailyFailedTurns':         maquina['dailyFailedTurns'] or 0,
            'dateLastQRUsed':           maquina['dateLastQRUsed'].isoformat() if maquina['dateLastQRUsed'] else None,
            'ultimo_uso_texto':         ultimo_uso_texto,
            'errorNote':                maquina['errorNote'],
            'porcentaje_restaurante':   float(maquina['porcentaje_restaurante']),
            'propietarios':             propietarios,
            'info_propietarios':        ", ".join(
                f"{p['nombre']} ({p['porcentaje_propiedad']}%)" for p in propietarios
            ) if propietarios else "Sin propietarios",
            'valor_por_turno':          float(maquina['valor_por_turno'] or 3000.00),
            'machine_subtype':          maquina.get('machine_subtype', 'simple') or 'simple',
            'station_names':            parse_json_col(maquina.get('station_names'), []),
            'show_station_selection':   bool(maquina.get('show_station_selection', False)),
            'active_failure_stations':  active_failure_stations,
            'machine_level_failures':   machine_level_failures,
        })

    except Exception as e:
        logger.error(f"Error obteniendo máquina: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/maquinas/<int:maquina_id>/ultima-actividad', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_ultima_actividad_maquina(maquina_id):
    """Obtener información sobre la última actividad de una máquina."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("""
            SELECT tu.usedAt, qr.code as qr_code, qr.qr_name, tp.name as package_name
            FROM turnusage tu
            JOIN qrcode qr ON tu.qrCodeId = qr.id
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE tu.machineId = %s
            ORDER BY tu.usedAt DESC LIMIT 1
        """, (maquina_id,))
        ultimo_juego = cursor.fetchone()

        cursor.execute("""
            SELECT reported_at, notes, is_forced, turnos_devueltos
            FROM machinefailures
            WHERE machine_id = %s
            ORDER BY reported_at DESC LIMIT 1
        """, (maquina_id,))
        ultima_falla = cursor.fetchone()

        resultado = {'ultimo_juego': None, 'ultima_falla': None}

        if ultimo_juego:
            resultado['ultimo_juego'] = {
                'fecha':   ultimo_juego['usedAt'].isoformat() if ultimo_juego['usedAt'] else None,
                'qr_code': ultimo_juego['qr_code'],
                'qr_name': ultimo_juego['qr_name'],
                'package': ultimo_juego['package_name'],
            }
        if ultima_falla:
            resultado['ultima_falla'] = {
                'fecha':           ultima_falla['reported_at'].isoformat() if ultima_falla['reported_at'] else None,
                'descripcion':     ultima_falla['notes'],
                'forzada':         bool(ultima_falla['is_forced']),
                'turnos_devueltos': ultima_falla['turnos_devueltos'],
            }

        return jsonify(resultado)

    except Exception as e:
        logger.error(f"Error obteniendo última actividad: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ── Turnusage / Failures ──────────────────────────────────────────────────────

@machines_bp.route('/api/turnusage/recientes', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_turnusage_recientes():
    """Historial reciente de juegos con soporte para estaciones."""
    connection = None
    cursor = None
    try:
        try:
            limit = int(request.args.get('limit', 100))
        except Exception:
            limit = 100

        machine_id = request.args.get('machine_id')
        station    = request.args.get('station')

        connection = get_db_connection()
        if not connection:
            return jsonify([])

        cursor = get_db_cursor(connection)
        if not cursor:
            return jsonify([])

        query = """
            SELECT
                tu.id, tu.qrCodeId, tu.machineId, tu.station_index, tu.usedAt,
                COALESCE(m.name, 'Máquina desconocida') as machine_name,
                COALESCE(qr.code, '') as qr_code,
                COALESCE(qr.qr_name, '') as qr_name,
                COALESCE(tp.name, 'Sin paquete') as package_name,
                tu.turns_remaining_after as turns_remaining
            FROM turnusage tu
            LEFT JOIN machine m  ON tu.machineId  = m.id
            LEFT JOIN qrcode qr  ON tu.qrCodeId   = qr.id
            LEFT JOIN turnpackage tp ON qr.turnPackageId = tp.id
            WHERE 1=1
        """
        params = []
        if machine_id:
            query += " AND tu.machineId = %s"
            params.append(machine_id)
        if station is not None:
            query += " AND tu.station_index = %s"
            params.append(station)
        query += " ORDER BY tu.usedAt DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        juegos = cursor.fetchall()

        resultado = []
        for j in juegos:
            d = dict(j)
            if d.get('usedAt') and hasattr(d['usedAt'], 'isoformat'):
                d['usedAt'] = d['usedAt'].isoformat()
            resultado.append(d)

        return jsonify(resultado)

    except Exception as e:
        logger.error(f"Error obteniendo turnusage recientes: {e}")
        return jsonify([])
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/machinefailures/recientes', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def obtener_machinefailures_recientes():
    """Historial reciente de fallas con soporte para estaciones."""
    connection = None
    cursor = None
    try:
        try:
            limit = int(request.args.get('limit', 100))
        except Exception:
            limit = 100

        machine_id = request.args.get('machine_id')
        station    = request.args.get('station')

        connection = get_db_connection()
        if not connection:
            return jsonify([])

        cursor = get_db_cursor(connection)
        if not cursor:
            return jsonify([])

        query = """
            SELECT
                mf.id, mf.qr_code_id,
                COALESCE(mf.machine_id, 0) as machine_id,
                mf.station_index,
                COALESCE(mf.machine_name, 'Máquina desconocida') as machine_name,
                COALESCE(mf.turnos_devueltos, 0) as turnos_devueltos,
                mf.reported_at,
                COALESCE(mf.notes, '') as notes,
                COALESCE(mf.is_forced, 0) as is_forced,
                COALESCE(mf.forced_by, '') as forced_by,
                COALESCE(qr.code, '') as qr_code,
                COALESCE(qr.qr_name, '') as qr_name
            FROM machinefailures mf
            LEFT JOIN qrcode qr ON mf.qr_code_id = qr.id
            WHERE 1=1
        """
        params = []
        if machine_id:
            query += " AND mf.machine_id = %s"
            params.append(machine_id)
        if station is not None:
            query += " AND mf.station_index = %s"
            params.append(station)
        query += " ORDER BY mf.reported_at DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        fallas = cursor.fetchall()

        resultado = []
        for f in fallas:
            d = dict(f)
            if d.get('reported_at') and hasattr(d['reported_at'], 'isoformat'):
                d['reported_at'] = d['reported_at'].isoformat()
            resultado.append(d)

        return jsonify(resultado)

    except Exception as e:
        logger.error(f"Error obteniendo fallas recientes: {e}")
        return jsonify([])
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ── Imágenes ──────────────────────────────────────────────────────────────────

@machines_bp.route('/api/imagenes/maquinas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def listar_imagenes_maquinas():
    """Listar imágenes disponibles para máquinas."""
    try:
        static_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'static', 'img')
        imagenes = []
        if os.path.exists(static_dir):
            for f in os.listdir(static_dir):
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    imagenes.append(f)
        return jsonify(imagenes)
    except Exception as e:
        logger.error(f"Error listando imágenes: {e}")
        return api_response('E001', http_status=500)


# ── CRUD máquinas ─────────────────────────────────────────────────────────────

@machines_bp.route('/api/maquinas', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'type', 'location_id'])
def crear_maquina():
    """Crear una nueva máquina."""
    connection = None
    cursor = None
    try:
        data                  = request.get_json()
        name                  = data['name']
        tipo                  = data['type']
        status                = data.get('status', 'activa')
        location_id           = data['location_id']
        errorNote             = data.get('errorNote', '')
        porcentaje_restaurante = data.get('porcentaje_restaurante', 35.00)

        if tipo not in ['simulador', 'arcade', 'peluchera']:
            return api_response('E005', http_status=400, data={'message': 'Tipo de máquina inválido'})
        if status not in ['activa', 'mantenimiento', 'inactiva']:
            return api_response('E005', http_status=400, data={'message': 'Estado inválido'})
        if not (0 <= float(porcentaje_restaurante) <= 100):
            return api_response('E005', http_status=400, data={'message': 'Porcentaje debe estar entre 0 y 100'})

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM machine WHERE name = %s", (name,))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Máquina ya existe'})

        cursor.execute("SELECT id FROM location WHERE id = %s", (location_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'local_id': location_id})

        cursor.execute(
            "INSERT INTO machine (name, type, status, location_id, errorNote) VALUES (%s, %s, %s, %s, %s)",
            (name, tipo, status, location_id, errorNote)
        )
        maquina_id = cursor.lastrowid

        if float(porcentaje_restaurante) != 35.00:
            cursor.execute(
                "INSERT INTO maquinaporcentajerestaurante (maquina_id, porcentaje_restaurante) VALUES (%s, %s)",
                (maquina_id, porcentaje_restaurante)
            )

        connection.commit()
        logger.info(f"Máquina creada: {name} (ID: {maquina_id})")
        return api_response('S002', status='success', data={'maquina_id': maquina_id})

    except Exception as e:
        logger.error(f"Error creando máquina: {e}")
        if connection: connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/maquinas/<int:maquina_id>', methods=['PUT'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['name', 'type', 'status', 'location_id'])
def actualizar_maquina(maquina_id):
    """Actualizar una máquina existente."""
    connection = None
    cursor = None
    try:
        data                  = request.get_json()
        name                  = data['name']
        tipo                  = data['type']
        status                = data['status']
        location_id           = data['location_id']
        errorNote             = data.get('errorNote', '')
        porcentaje_restaurante = data.get('porcentaje_restaurante', 35.00)

        if tipo not in ['simulador', 'arcade', 'peluchera']:
            return api_response('E005', http_status=400, data={'message': 'Tipo de máquina inválido'})
        if status not in ['activa', 'mantenimiento', 'inactiva']:
            return api_response('E005', http_status=400, data={'message': 'Estado inválido'})
        if not (0 <= float(porcentaje_restaurante) <= 100):
            return api_response('E005', http_status=400, data={'message': 'Porcentaje debe estar entre 0 y 100'})

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name FROM machine WHERE id = %s", (maquina_id,))
        if not cursor.fetchone():
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})

        cursor.execute("SELECT id FROM machine WHERE name = %s AND id != %s", (name, maquina_id))
        if cursor.fetchone():
            return api_response('E007', http_status=400, data={'message': 'Nombre de máquina ya existe'})

        cursor.execute("SELECT id FROM location WHERE id = %s", (location_id,))
        if not cursor.fetchone():
            return api_response('E002', http_status=404, data={'local_id': location_id})

        cursor.execute(
            "UPDATE machine SET name=%s, type=%s, status=%s, location_id=%s, errorNote=%s WHERE id=%s",
            (name, tipo, status, location_id, errorNote, maquina_id)
        )

        if float(porcentaje_restaurante) != 35.00:
            cursor.execute("""
                INSERT INTO maquinaporcentajerestaurante (maquina_id, porcentaje_restaurante)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE porcentaje_restaurante = %s
            """, (maquina_id, porcentaje_restaurante, porcentaje_restaurante))
        else:
            cursor.execute("DELETE FROM maquinaporcentajerestaurante WHERE maquina_id = %s", (maquina_id,))

        connection.commit()
        logger.info(f"Máquina actualizada: {name} (ID: {maquina_id})")
        return api_response('S003', status='success')

    except Exception as e:
        logger.error(f"Error actualizando máquina: {e}")
        if connection: connection.rollback()
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/maquinas/<int:maquina_id>', methods=['DELETE'])
@handle_api_errors
@require_login(['admin'])
def eliminar_maquina(maquina_id):
    """Eliminar una máquina y sus FK relacionadas."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})

        cursor.execute("SELECT COUNT(*) as uso_count FROM turnusage WHERE machineId = %s", (maquina_id,))
        uso_count = cursor.fetchone()['uso_count']
        if uso_count > 0:
            return api_response(
                'W004', status='warning', http_status=400,
                data={'message': f'Máquina tiene {uso_count} usos registrados',
                      'uso_count': uso_count, 'machine_name': maquina['name']}
            )

        tablas_fk = [
            ('machinetechnical',            'machine_id'),
            ('esp32_commands',              'machine_id'),
            ('machine_resets',              'machine_id'),
            ('machinefailures',             'machine_id'),
            ('maquinapropietario',          'maquina_id'),
            ('maquinaporcentajerestaurante', 'maquina_id'),
            ('errorreport',                 'machineId'),
        ]
        for tabla, col in tablas_fk:
            try:
                cursor.execute(f"DELETE FROM {tabla} WHERE {col} = %s", (maquina_id,))
            except Exception as fk_err:
                logger.warning(f"FK cleanup {tabla}: {fk_err}")

        cursor.execute("DELETE FROM machine WHERE id = %s", (maquina_id,))
        connection.commit()

        logger.info(f"Máquina eliminada: {maquina['name']} (ID: {maquina_id})")
        return api_response('S004', status='success')

    except Exception as e:
        logger.error(f"Error eliminando máquina: {e}")
        sentry_sdk.capture_exception(e)
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


# ── Datos técnicos y propietarios ─────────────────────────────────────────────

@machines_bp.route('/api/maquinas/<int:maquina_id>/technical', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def guardar_technical_maquina(maquina_id):
    """Guardar configuración técnica de la máquina."""
    connection = None
    cursor = None
    try:
        data = request.get_json()

        connection = get_db_connection()
        cursor = get_db_cursor(connection)

        cursor.execute("SELECT id FROM machinetechnical WHERE machine_id = %s", (maquina_id,))
        existe = cursor.fetchone()

        params_comunes = (
            data.get('credits_virtual', 1),
            data.get('credits_machine', 1),
            data.get('game_duration_seconds', 180),
            data.get('reset_time_seconds', 5),
            data.get('machine_subtype', 'simple'),
            json.dumps(data.get('stations', [])),
            data.get('game_type', 'time_based'),
            data.get('has_failure_report', True),
            data.get('show_station_selection', False),
        )

        if existe:
            cursor.execute("""
                UPDATE machinetechnical
                SET credits_virtual=%s, credits_machine=%s, game_duration_seconds=%s,
                    reset_time_seconds=%s, machine_subtype=%s, station_names=%s,
                    game_type=%s, has_failure_report=%s, show_station_selection=%s,
                    updated_at=NOW()
                WHERE machine_id=%s
            """, (*params_comunes, maquina_id))
        else:
            cursor.execute("""
                INSERT INTO machinetechnical
                    (machine_id, credits_virtual, credits_machine, game_duration_seconds,
                     reset_time_seconds, machine_subtype, station_names, game_type,
                     has_failure_report, show_station_selection)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (maquina_id, *params_comunes))

        connection.commit()

        # Encolar comando UPDATE_STATION_NAMES al ESP32 si es multi-estación
        machine_subtype = data.get('machine_subtype', 'simple')
        stations = data.get('stations', [])
        if machine_subtype == 'multi_station' and stations:
            try:
                station_names_list = [s['name'] if isinstance(s, dict) else str(s) for s in stations]
                cursor.execute("""
                    INSERT INTO esp32_commands
                        (machine_id, command, parameters, triggered_by, status, triggered_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (
                    maquina_id,
                    'UPDATE_STATION_NAMES',
                    json.dumps({'station_names': station_names_list, 'station_count': len(station_names_list)}),
                    'admin_config',
                    'queued',
                ))
                connection.commit()
                logger.info(f"Comando UPDATE_STATION_NAMES encolado para máquina {maquina_id}")
            except Exception as cmd_err:
                logger.warning(f"No se pudo encolar UPDATE_STATION_NAMES: {cmd_err}")

        return api_response('S003', status='success')

    except Exception as e:
        logger.error(f"Error guardando datos técnicos: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/maquinas/<int:maquina_id>/propietarios', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def guardar_propietarios_maquina(maquina_id):
    """Reemplazar propietarios de la máquina."""
    connection = None
    cursor = None
    try:
        data = request.get_json()

        connection = get_db_connection()
        cursor = get_db_cursor(connection)

        cursor.execute("DELETE FROM maquinapropietario WHERE maquina_id = %s", (maquina_id,))

        for prop in data:
            cursor.execute(
                "INSERT INTO maquinapropietario (maquina_id, propietario_id, porcentaje_propiedad) VALUES (%s, %s, %s)",
                (maquina_id, prop['propietario_id'], prop['porcentaje'])
            )

        connection.commit()
        return api_response('S003', status='success')

    except Exception as e:
        logger.error(f"Error guardando propietarios: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()
