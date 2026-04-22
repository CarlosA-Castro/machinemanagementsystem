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
from utils.location_scope import apply_location_filter, get_active_location, user_can_view_all
from blueprints.esp32.state import get_heartbeat_fields
from middleware.logging_mw import log_transaccion

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
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_maquinas():
    """Obtener todas las máquinas con información completa."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        # Filtro de local activo (no afecta a admins en modo "ver todos")
        active_id, _ = get_active_location()
        can_all = user_can_view_all()
        if can_all and active_id is None:
            loc_clause, loc_params = "", []
        else:
            eff_id = active_id if active_id is not None else -1
            loc_clause, loc_params = "WHERE m.location_id = %s", [eff_id]

        try:
            cursor.execute(f"""
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
                {loc_clause}
                ORDER BY m.name
            """, loc_params)
        except Exception:
            # Fallback si las columnas de V32 aún no existen
            cursor.execute(f"""
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
                {loc_clause}
                ORDER BY m.name
            """, loc_params)

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

        active_id, active_name = get_active_location()
        can_all = user_can_view_all()

        BASE_SQL = """
            SELECT
                m.id, m.name, m.type, m.status, m.location_id,
                l.name as location_name,
                COALESCE(m.dailyFailedTurns, 0) as dailyFailedTurns,
                m.dateLastQRUsed,
                COALESCE(m.valor_por_turno, 3000.00) as valor_por_turno
            FROM machine m
            LEFT JOIN location l ON m.location_id = l.id
            WHERE m.status IN ('activa', 'mantenimiento', 'inactiva')
        """

        if active_id is not None:
            # Filtro por ID de local (caso normal con sesión correcta)
            cursor.execute(BASE_SQL + " AND m.location_id = %s ORDER BY m.type, m.name", (active_id,))
        elif active_name:
            # Admin que seleccionó local: filtrar por nombre
            cursor.execute(BASE_SQL + " AND l.name = %s ORDER BY m.type, m.name", (active_name,))
        elif not can_all:
            # Cajero/admin_restaurante sin active_location_id → sesión incompleta, mostrar vacío
            cursor.execute(BASE_SQL + " AND 1=0 ORDER BY m.type, m.name")
        else:
            # Admin global sin local activo seleccionado → ver todas
            cursor.execute(BASE_SQL + " ORDER BY m.type, m.name")
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
            **get_heartbeat_fields(maquina['id']),
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

        # Filtro de local activo (vía subquery sobre machine)
        active_id, _ = get_active_location()
        can_all = user_can_view_all()
        if not (can_all and active_id is None):
            eff_id = active_id if active_id is not None else -1
            query += " AND tu.machineId IN (SELECT id FROM machine WHERE location_id = %s)"
            params.append(eff_id)

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
                COALESCE(qr.qr_name, '') as qr_name,
                mt.station_names
            FROM machinefailures mf
            LEFT JOIN qrcode qr ON mf.qr_code_id = qr.id
            LEFT JOIN machine m ON mf.machine_id = m.id
            LEFT JOIN machinetechnical mt ON m.id = mt.machine_id
            WHERE 1=1
        """
        params = []
        if machine_id:
            query += " AND mf.machine_id = %s"
            params.append(machine_id)
        if station is not None:
            query += " AND mf.station_index = %s"
            params.append(station)

        # Filtro de local activo
        active_id, _ = get_active_location()
        can_all = user_can_view_all()
        if not (can_all and active_id is None):
            eff_id = active_id if active_id is not None else -1
            query += " AND mf.machine_id IN (SELECT id FROM machine WHERE location_id = %s)"
            params.append(eff_id)

        query += " ORDER BY mf.reported_at DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        fallas = cursor.fetchall()

        resultado = []
        for f in fallas:
            d = dict(f)
            if d.get('reported_at') and hasattr(d['reported_at'], 'isoformat'):
                d['reported_at'] = d['reported_at'].isoformat()
            d['station_names'] = parse_json_col(d.get('station_names'), [])
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


# ==================== APIS PARA ACCIONES DE MÁQUINAS DESDE ADMIN ====================

@machines_bp.route('/api/maquinas/ingresar-turno', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['machine_id', 'machine_name'])
def ingresar_turno_manual():
    """
    Endpoint para que el administrador pueda INGRESAR UN TURNO MANUAL
    Ahora también envía comando al ESP32 para activar el relé
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        machine_id = data['machine_id']
        machine_name = data['machine_name']
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Administrador')

        logger.info(f"🔄 [ADMIN] Ingresando turno manual - Máquina: {machine_name} (ID: {machine_id})")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        # Verificar que la máquina existe
        cursor.execute("SELECT id, name, status FROM machine WHERE id = %s", (machine_id,))
        maquina = cursor.fetchone()

        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': machine_id})

        # Verificar estado de la máquina
        if maquina['status'] != 'activa':
            return api_response(
                'M003',
                http_status=400,
                data={
                    'machine_id': machine_id,
                    'current_status': maquina['status'],
                    'message': f'La máquina está en estado "{maquina["status"]}". Solo se pueden ingresar turnos en máquinas activas.'
                }
            )

        # Obtener estación (para máquinas multi-estación)
        station_index = data.get('estacion', 0)
        estacion_nombre = data.get('estacion_nombre', f'Estación {station_index + 1}')

        hora_actual = get_colombia_time()

        # ENVIAR COMANDO AL ESP32 — activar relé sin consumir ningún QR
        cursor.execute("""
            INSERT INTO esp32_commands (machine_id, command, parameters, triggered_by, status, triggered_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            machine_id,
            'ACTIVATE_RELAY',
            json.dumps({
                'duration_ms': 500,
                'machine_name': machine_name,
                'station': station_index,
                'station_index': station_index,
                'estacion_nombre': estacion_nombre,
                'origen': 'admin_manual'
            }),
            user_name,
            'queued',
            format_datetime_for_db(hora_actual)
        ))

        command_id = cursor.lastrowid
        logger.info(f"✅ Comando ACTIVATE_RELAY encolado con ID: {command_id} (estación {station_index})")

        connection.commit()

        log_transaccion(
            tipo='turno_manual',
            categoria='operacional',
            descripcion=f"Turno manual admin en {machine_name} — {estacion_nombre}",
            usuario=user_name,
            usuario_id=user_id,
            maquina_id=machine_id,
            maquina_nombre=machine_name,
            entidad='machine',
            entidad_id=machine_id,
            datos_extra={
                'command_id': command_id,
                'station_index': station_index,
                'estacion_nombre': estacion_nombre,
                'origen': 'admin_manual'
            }
        )

        logger.info(f"✅ Turno manual admin — Máquina: {machine_name} ({machine_id}) | Estación: {estacion_nombre} | Command ID: {command_id} | Admin: {user_name}")

        return api_response(
            'S014',
            status='success',
            data={
                'machine_id': machine_id,
                'machine_name': machine_name,
                'command_id': command_id,
                'station_index': station_index,
                'estacion_nombre': estacion_nombre,
                'timestamp': hora_actual.isoformat(),
                'message': f'Comando enviado al ESP32 (ID: {command_id}). Sin uso de QR.'
            }
        )

    except Exception as e:
        logger.error(f"Error ingresando turno manual: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/maquinas/reiniciar', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
@validate_required_fields(['machine_id', 'machine_name'])
def reiniciar_maquina_manual():
    """
    Endpoint para que el administrador pueda REINICIAR una máquina
    Envía comando de reinicio y registra el evento
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        machine_id = data['machine_id']
        machine_name = data['machine_name']
        user_id = session.get('user_id')
        user_name = session.get('user_name', 'Administrador')

        logger.info(f"🔄 [ADMIN] Reiniciando máquina - {machine_name} (ID: {machine_id})")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        # Verificar que la máquina existe
        cursor.execute("SELECT id, name, status FROM machine WHERE id = %s", (machine_id,))
        maquina = cursor.fetchone()

        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': machine_id})

        # Obtener datos técnicos de la máquina
        cursor.execute("""
            SELECT reset_time_seconds
            FROM machinetechnical
            WHERE machine_id = %s
        """, (machine_id,))

        tech_data = cursor.fetchone()
        reset_time = tech_data['reset_time_seconds'] if tech_data else 5

        # Obtener el último QR usado (si existe)
        cursor.execute("""
            SELECT qr.code, qr.qr_name, tu.id as usage_id
            FROM turnusage tu
            JOIN qrcode qr ON tu.qrCodeId = qr.id
            WHERE tu.machineId = %s
            ORDER BY tu.usedAt DESC
            LIMIT 1
        """, (machine_id,))

        ultimo_uso = cursor.fetchone()

        # Registrar el reinicio en machine_resets
        try:
            cursor.execute("""
                INSERT INTO machine_resets
                (machine_id, machine_name, triggered_by, triggered_by_name, reset_time_seconds,
                 qr_code, usage_id, status, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                machine_id,
                machine_name,
                user_id,
                user_name,
                reset_time,
                ultimo_uso['code'] if ultimo_uso else None,
                ultimo_uso['usage_id'] if ultimo_uso else None,
                'sent',
                f'Reinicio manual solicitado por {user_name}'
            ))
            reset_id = cursor.lastrowid
        except Exception as e:
            logger.warning(f"Error insertando en machine_resets: {e}")
            reset_id = None

        # Obtener estación desde el request (para multi-estación)
        station_index = data.get('estacion', 0)
        estacion_nombre = data.get('estacion_nombre', f'Estación {station_index + 1}')

        hora_actual = get_colombia_time()

        # Registrar en esp32_commands
        try:
            cursor.execute("""
                INSERT INTO esp32_commands (machine_id, command, parameters, triggered_by, status, triggered_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                machine_id,
                'RESET',
                json.dumps({
                    'reset_time': reset_time,
                    'machine_name': machine_name,
                    'station_index': station_index,
                    'estacion_nombre': estacion_nombre,
                    'restart_tft': True
                }),
                user_name,
                'queued',
                format_datetime_for_db(hora_actual)
            ))
            command_id = cursor.lastrowid
            logger.info(f"✅ Comando RESET encolado con ID: {command_id} (estación {station_index})")
        except Exception as e:
            logger.error(f"Error insertando en esp32_commands: {e}")
            command_id = None

        # Registrar en logs de aplicación
        cursor.execute("""
            INSERT INTO app_logs (level, module, message, user_id, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """, ('INFO', 'machine_action',
              f"Admin {user_name} solicitó reinicio de máquina {machine_name} (ID: {machine_id})",
              user_id,
              format_datetime_for_db(hora_actual)))

        connection.commit()

        logger.info(f"✅ Reinicio registrado para máquina {machine_name} - Reset ID: {reset_id}, Command ID: {command_id}")

        return api_response(
            'S015',
            status='success',
            data={
                'machine_id': machine_id,
                'machine_name': machine_name,
                'reset_id': reset_id,
                'command_id': command_id,
                'reset_time_seconds': reset_time,
                'message': f'Comando de reinicio enviado a la máquina. Tiempo estimado: {reset_time} segundos',
                'command': 'RESET'
            }
        )

    except Exception as e:
        logger.error(f"Error reiniciando máquina: {e}", exc_info=True)
        if connection:
            connection.rollback()
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/logs/accion', methods=['POST'])
@handle_api_errors
@require_login(['admin', 'cajero'])
def registrar_log_accion():
    """
    Endpoint para registrar logs de acciones desde el frontend
    """
    try:
        data = request.get_json()
        accion = data.get('accion')
        detalles = data.get('detalles', {})
        usuario = data.get('usuario', session.get('user_name', 'Desconocido'))
        timestamp = data.get('timestamp')

        logger.info(f"[LOG ACCIÓN] {accion} - {usuario} - {json.dumps(detalles)}")

        # Registrar en base de datos
        connection = get_db_connection()
        if connection:
            cursor = get_db_cursor(connection)
            try:
                fecha_mysql = None
                if timestamp:
                    try:
                        fecha_iso = timestamp.replace('Z', '').replace('T', ' ')
                        if len(fecha_iso) > 19:
                            fecha_iso = fecha_iso[:19]
                        fecha_mysql = fecha_iso
                    except Exception:
                        fecha_mysql = format_datetime_for_db(get_colombia_time())
                else:
                    fecha_mysql = format_datetime_for_db(get_colombia_time())

                mensaje = f"Acción: {accion} | Detalles: {json.dumps(detalles)} | Usuario: {usuario}"
                cursor.execute("""
                    INSERT INTO app_logs (level, module, message, user_id, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, ('INFO', 'frontend_action', mensaje[:500], session.get('user_id'), fecha_mysql))

                connection.commit()
            except Exception as db_error:
                logger.warning(f"No se pudo insertar en app_logs: {db_error}")
                try:
                    cursor.execute("""
                        INSERT INTO app_logs (level, module, message, user_id)
                        VALUES (%s, %s, %s, %s)
                    """, ('INFO', 'frontend_action', mensaje[:500], session.get('user_id')))
                    connection.commit()
                except Exception:
                    pass

            cursor.close()
            connection.close()

        return api_response('S001', status='success', data={'logged': True})

    except Exception as e:
        logger.error(f"Error registrando log de acción: {e}")
        return api_response('E001', http_status=500)


# ==================== APIS PARA REPORTES DE MÁQUINAS ====================

@machines_bp.route('/api/maquinas/<int:maquina_id>/reportes', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_reportes_maquina(maquina_id):
    """Obtener reportes de fallas de una máquina específica"""
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

        cursor.execute("""
            SELECT
                er.id,
                er.machineId,
                er.userId,
                er.description,
                er.reportedAt,
                er.isResolved,
                u.name as user_name
            FROM errorreport er
            JOIN users u ON er.userId = u.id
            WHERE er.machineId = %s
            ORDER BY er.reportedAt DESC
        """, (maquina_id,))

        reportes = cursor.fetchall()

        for reporte in reportes:
            if reporte['reportedAt']:
                fecha_colombia = parse_db_datetime(reporte['reportedAt'])
                reporte['reportedAt'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({
            'maquina_id': maquina_id,
            'maquina_nombre': maquina['name'],
            'reportes': reportes,
            'total': len(reportes)
        })

    except Exception as e:
        logger.error(f"Error obteniendo reportes de máquina: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()


@machines_bp.route('/api/maquinas/<int:maquina_id>/estadisticas', methods=['GET'])
@handle_api_errors
@require_login(['admin', 'cajero', 'admin_restaurante'])
def obtener_estadisticas_maquina(maquina_id):
    """Obtener estadísticas de una máquina"""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        cursor.execute("SELECT name, status FROM machine WHERE id = %s", (maquina_id,))
        maquina = cursor.fetchone()
        if not maquina:
            return api_response('M001', http_status=404, data={'machine_id': maquina_id})

        # Estadísticas globales de uso
        cursor.execute("""
            SELECT
                COUNT(*) as total_usos,
                COUNT(DISTINCT DATE(usedAt)) as dias_con_usos,
                MIN(usedAt) as primer_uso,
                MAX(usedAt) as ultimo_uso
            FROM turnusage
            WHERE machineId = %s
        """, (maquina_id,))

        uso_stats = cursor.fetchone()

        # Usos por día (últimos 30 días)
        cursor.execute("""
            SELECT
                DATE(usedAt) as fecha,
                COUNT(*) as usos
            FROM turnusage
            WHERE machineId = %s
            AND usedAt >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY DATE(usedAt)
            ORDER BY fecha DESC
        """, (maquina_id,))

        usos_por_dia = cursor.fetchall()

        # Estadísticas de reportes de fallas
        cursor.execute("""
            SELECT
                COUNT(*) as total_reportes,
                COUNT(CASE WHEN isResolved = TRUE THEN 1 END) as reportes_resueltos,
                COUNT(CASE WHEN isResolved = FALSE THEN 1 END) as reportes_pendientes
            FROM errorreport
            WHERE machineId = %s
        """, (maquina_id,))

        reportes_stats = cursor.fetchone()

        # Últimos 5 reportes
        cursor.execute("""
            SELECT
                er.description,
                er.reportedAt,
                er.isResolved,
                u.name as reportado_por
            FROM errorreport er
            JOIN users u ON er.userId = u.id
            WHERE er.machineId = %s
            ORDER BY er.reportedAt DESC
            LIMIT 5
        """, (maquina_id,))

        ultimos_reportes = cursor.fetchall()

        for reporte in ultimos_reportes:
            if reporte['reportedAt']:
                fecha_colombia = parse_db_datetime(reporte['reportedAt'])
                reporte['reportedAt'] = fecha_colombia.strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({
            'maquina_id': maquina_id,
            'maquina_nombre': maquina['name'],
            'estado': maquina['status'],
            'estadisticas': {
                'uso': {
                    'total_usos': uso_stats['total_usos'] or 0,
                    'dias_con_usos': uso_stats['dias_con_usos'] or 0,
                    'primer_uso': uso_stats['primer_uso'].isoformat() if uso_stats['primer_uso'] else None,
                    'ultimo_uso': uso_stats['ultimo_uso'].isoformat() if uso_stats['ultimo_uso'] else None
                },
                'reportes': {
                    'total': reportes_stats['total_reportes'] or 0,
                    'resueltos': reportes_stats['reportes_resueltos'] or 0,
                    'pendientes': reportes_stats['reportes_pendientes'] or 0
                }
            },
            'usos_por_dia': usos_por_dia,
            'ultimos_reportes': ultimos_reportes,
            'timestamp': get_colombia_time().isoformat()
        })

    except Exception as e:
        logger.error(f"Error obteniendo estadísticas de máquina: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:     cursor.close()
        if connection: connection.close()
