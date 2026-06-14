import logging
import os

from flask import Blueprint, jsonify, request, send_from_directory, session
from werkzeug.utils import secure_filename

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_admin_access
from utils.location_scope import get_active_location
from utils.responses import handle_api_errors

logger = logging.getLogger(LOGGER_NAME)

firmware_bp = Blueprint('firmware', __name__)

FIRMWARE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static', 'firmware')
ALLOWED_EXTENSIONS = {'bin'}
MAX_FILE_SIZE = 4 * 1024 * 1024  # 4 MB — .bin de ESP32 puede superar 2 MB


def _allowed(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _ensure_firmware_dir() -> None:
    os.makedirs(FIRMWARE_DIR, exist_ok=True)


def _firmware_file_exists(filename: str) -> bool:
    return os.path.isfile(os.path.join(FIRMWARE_DIR, filename))


# ── Endpoints sin auth (los llama el ESP32) ───────────────────────────────────

@firmware_bp.route('/api/esp32/firmware/latest', methods=['GET'])
@handle_api_errors
def firmware_latest():
    """ESP32 consulta si hay una versión activa y cuál es."""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)
    cur.execute(
        "SELECT id, version, filename, file_size FROM firmware WHERE is_active = 1 LIMIT 1"
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return jsonify({'available': False}), 200
    if not _firmware_file_exists(row['filename']):
        logger.error(
            "Firmware activo sin archivo en disco: version=%s filename=%s",
            row['version'],
            row['filename'],
        )
        return jsonify({'available': False, 'error': 'active_firmware_missing_file'}), 200
    return jsonify({
        'available': True,
        'version':   row['version'],
        'filename':  row['filename'],
        'size':      row['file_size'],
    }), 200


@firmware_bp.route('/api/esp32/firmware/download', methods=['GET'])
@handle_api_errors
def firmware_download():
    """ESP32 descarga el .bin de la versión activa."""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)
    cur.execute(
        "SELECT filename FROM firmware WHERE is_active = 1 LIMIT 1"
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return jsonify({'error': 'no_active_firmware'}), 404
    if not _firmware_file_exists(row['filename']):
        logger.error("Descarga OTA fallida: archivo no existe para firmware activo: %s", row['filename'])
        return jsonify({'error': 'active_firmware_file_missing'}), 404
    return send_from_directory(FIRMWARE_DIR, row['filename'], as_attachment=True)


# ── Endpoints admin ───────────────────────────────────────────────────────────

@firmware_bp.route('/api/admin/firmware', methods=['GET'])
@require_admin_access('maquinas')
@handle_api_errors
def firmware_list():
    """Lista todas las versiones de firmware registradas."""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)
    cur.execute(
        "SELECT id, version, filename, notes, uploaded_at, uploaded_by, is_active, file_size "
        "FROM firmware ORDER BY uploaded_at DESC"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = []
    for r in rows:
        result.append({
            'id':          r['id'],
            'version':     r['version'],
            'filename':    r['filename'],
            'notes':       r['notes'],
            'uploaded_at': r['uploaded_at'].isoformat() if r['uploaded_at'] else None,
            'uploaded_by': r['uploaded_by'],
            'is_active':   bool(r['is_active']),
            'file_size':   r['file_size'],
        })
    return jsonify(result), 200


@firmware_bp.route('/api/admin/firmware/upload', methods=['POST'])
@require_admin_access('maquinas')
@handle_api_errors
def firmware_upload():
    """Sube un .bin y registra la versión en BD. No la activa automáticamente."""
    file = request.files.get('file')
    if not file or not _allowed(file.filename):
        return jsonify({'error': 'Solo se aceptan archivos .bin'}), 400

    version = request.form.get('version', '').strip()
    notes   = request.form.get('notes', '').strip() or None
    if not version or not version.isdigit():
        return jsonify({'error': 'version requerida (entero, ej: 20260426)'}), 400

    # Guardar con la versión en el nombre para que cada build quede separado
    # ej: Circuito_maquinas.ino.esp32.bin → Circuito_maquinas.ino.esp32_20260427.bin
    _ensure_firmware_dir()
    original = secure_filename(file.filename)
    base, ext = os.path.splitext(original)
    filename  = f"{base}_{version}{ext}"
    file_path = os.path.join(FIRMWARE_DIR, filename)

    if os.path.exists(file_path):
        return jsonify({'error': f'Ya existe la versión {version}. Usa un número de versión diferente.'}), 409

    file.save(file_path)
    size = os.path.getsize(file_path)

    if size > MAX_FILE_SIZE:
        os.remove(file_path)
        return jsonify({'error': 'Archivo demasiado grande (máx 2 MB)'}), 413

    conn = get_db_connection()
    if not conn:
        os.remove(file_path)
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)
    cur.execute(
        "INSERT INTO firmware (version, filename, notes, uploaded_by, file_size) "
        "VALUES (%s, %s, %s, %s, %s)",
        (int(version), filename, notes, session.get('user_id'), size),
    )
    conn.commit()
    new_id = cur.lastrowid
    cur.close()
    conn.close()

    logger.info(f"Firmware subido: {filename} v{version} ({size} bytes) por user {session.get('user_id')}")
    return jsonify({'id': new_id, 'filename': filename, 'size': size}), 201


@firmware_bp.route('/api/admin/machines/<int:machine_id>/reset-config', methods=['POST'])
@require_admin_access('maquinas')
@handle_api_errors
def machine_reset_config(machine_id: int):
    """
    Envía RESET_CONFIG al ESP32 indicado.
    La máquina borra sus Preferences (WiFi + machine_id) y entra al captive portal.
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)

    active_id, _ = get_active_location()
    cur.execute("SELECT id, name, location_id FROM machine WHERE id = %s", (machine_id,))
    machine = cur.fetchone()
    if not machine:
        cur.close(); conn.close()
        return jsonify({'error': 'Máquina no encontrada'}), 404
    if active_id is not None and machine['location_id'] != active_id:
        cur.close(); conn.close()
        return jsonify({'error': 'La máquina no pertenece al local activo'}), 403

    cur.execute(
        "INSERT INTO esp32_commands "
        "  (machine_id, command, parameters, triggered_by, status, triggered_at) "
        "VALUES (%s, 'RESET_CONFIG', '{}', %s, 'queued', NOW())",
        (machine_id, f"admin_reset_{session.get('user_id')}"),
    )
    conn.commit()
    cur.close()
    conn.close()

    logger.info(f"RESET_CONFIG enviado a máquina {machine_id} ({machine['name']}) por user {session.get('user_id')}")
    return jsonify({'ok': True, 'machine': machine['name']}), 200


@firmware_bp.route('/api/admin/machines', methods=['GET'])
@require_admin_access('maquinas')
@handle_api_errors
def machines_list_simple():
    """Lista de máquinas del local activo para el panel de firmware."""
    active_id, _ = get_active_location()
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)
    if active_id is not None:
        cur.execute(
            "SELECT id, name, status FROM machine WHERE location_id = %s ORDER BY name",
            (active_id,),
        )
    else:
        cur.execute("SELECT id, name, status FROM machine ORDER BY name")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{'id': r['id'], 'name': r['name'], 'status': r['status']} for r in rows]), 200


@firmware_bp.route('/api/admin/firmware/<int:firmware_id>/activar', methods=['PUT'])
@require_admin_access('maquinas')
@handle_api_errors
def firmware_activar(firmware_id: int):
    """
    Activa una versión de firmware y envía FORCE_OTA a todas las máquinas activas.
    Las máquinas lo reciben en ≤10 s (siguiente ciclo de checkPendingCommands).
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, filename FROM firmware WHERE id = %s", (firmware_id,))
    firmware = cur.fetchone()
    if not firmware:
        cur.close()
        conn.close()
        return jsonify({'error': 'Versión no encontrada'}), 404

    # Activar la versión seleccionada
    if not _firmware_file_exists(firmware['filename']):
        cur.close()
        conn.close()
        return jsonify({'error': 'El archivo .bin de esta versiÃ³n no existe en el servidor'}), 409

    cur.execute("UPDATE firmware SET is_active = 0")
    cur.execute("UPDATE firmware SET is_active = 1 WHERE id = %s", (firmware_id,))

    # Obtener máquinas activas del local activo (o todas si admin en modo global)
    active_id, _ = get_active_location()
    if active_id is not None:
        cur.execute(
            "SELECT id FROM machine WHERE status = 'activa' AND location_id = %s",
            (active_id,),
        )
    else:
        cur.execute("SELECT id FROM machine WHERE status = 'activa'")
    machines = cur.fetchall()

    # Insertar FORCE_OTA para cada una — las recibirán en ≤10 s
    triggered_by = f"firmware_ota_v{firmware_id}"
    for m in machines:
        cur.execute(
            "INSERT INTO esp32_commands "
            "  (machine_id, command, parameters, triggered_by, status, triggered_at) "
            "VALUES (%s, 'FORCE_OTA', '{}', %s, 'queued', NOW())",
            (m['id'], triggered_by),
        )

    conn.commit()
    notified = len(machines)
    cur.close()
    conn.close()

    logger.info(
        f"Firmware {firmware_id} activado por user {session.get('user_id')} "
        f"— FORCE_OTA enviado a {notified} máquina(s)"
    )
    return jsonify({'ok': True, 'active_id': firmware_id, 'machines_notified': notified}), 200


@firmware_bp.route('/api/admin/firmware/<int:firmware_id>/deploy-select', methods=['POST'])
@require_admin_access('maquinas')
@handle_api_errors
def firmware_deploy_select(firmware_id: int):
    """
    Envía FORCE_OTA solo a las máquinas indicadas en machine_ids.
    NO cambia la versión activa global — solo despacha el comando a las
    máquinas seleccionadas para que descarguen este firmware específico.
    Body: { machine_ids: [1, 3, 7] }
    """
    from flask import request as _req
    data       = _req.get_json(silent=True) or {}
    machine_ids = data.get('machine_ids', [])
    if not machine_ids or not isinstance(machine_ids, list):
        return jsonify({'error': 'machine_ids requerido (lista de IDs)'}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, filename FROM firmware WHERE id = %s", (firmware_id,))
    firmware = cur.fetchone()
    if not firmware:
        cur.close(); conn.close()
        return jsonify({'error': 'Versión no encontrada'}), 404

    if not _firmware_file_exists(firmware['filename']):
        cur.close(); conn.close()
        return jsonify({'error': 'El archivo .bin no existe en el servidor'}), 409

    triggered_by = f"firmware_select_v{firmware_id}_u{session.get('user_id', 0)}"
    notified = 0
    for mid in machine_ids:
        try:
            mid_int = int(mid)
        except (TypeError, ValueError):
            continue
        cur.execute(
            "INSERT INTO esp32_commands "
            "  (machine_id, command, parameters, triggered_by, status, triggered_at) "
            "VALUES (%s, 'FORCE_OTA', '{}', %s, 'queued', NOW())",
            (mid_int, triggered_by),
        )
        notified += 1

    conn.commit()
    cur.close(); conn.close()

    logger.info(
        f"Firmware {firmware_id} deploy selectivo por user {session.get('user_id')} "
        f"— FORCE_OTA enviado a {notified} máquina(s) seleccionadas: {machine_ids}"
    )
    return jsonify({'ok': True, 'firmware_id': firmware_id, 'machines_notified': notified}), 200


@firmware_bp.route('/api/admin/firmware/<int:firmware_id>', methods=['DELETE'])
@require_admin_access('maquinas')
@handle_api_errors
def firmware_delete(firmware_id: int):
    """
    Borra una versión de firmware: elimina la fila en BD y el .bin en disco.
    NO permite borrar la versión activa — los ESP32 la descargan por OTA y
    quedarían apuntando a un archivo inexistente. Hay que activar otra primero.
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)

    cur.execute("SELECT id, version, filename, is_active FROM firmware WHERE id = %s", (firmware_id,))
    firmware = cur.fetchone()
    if not firmware:
        cur.close(); conn.close()
        return jsonify({'error': 'Versión no encontrada'}), 404

    if firmware['is_active']:
        cur.close(); conn.close()
        return jsonify({
            'error': 'No se puede borrar la versión activa. Activa otra versión primero, luego podrás borrar esta.'
        }), 409

    cur.execute("DELETE FROM firmware WHERE id = %s", (firmware_id,))
    conn.commit()
    cur.close()
    conn.close()

    # Borrar el .bin del disco (después del commit en BD).
    # Si el archivo ya no existe, no es un error: la fila ya quedó eliminada.
    file_path = os.path.join(FIRMWARE_DIR, firmware['filename'])
    file_removed = False
    if os.path.isfile(file_path):
        try:
            os.remove(file_path)
            file_removed = True
        except OSError as exc:
            logger.error("Firmware %s borrado en BD pero falló borrar archivo %s: %s",
                         firmware_id, firmware['filename'], exc)

    logger.info(
        f"Firmware {firmware_id} (v{firmware['version']}, {firmware['filename']}) "
        f"borrado por user {session.get('user_id')} — archivo_borrado={file_removed}"
    )
    return jsonify({'ok': True, 'id': firmware_id, 'file_removed': file_removed}), 200
