import logging
import os

from flask import Blueprint, jsonify, request, send_from_directory, session
from werkzeug.utils import secure_filename

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.responses import handle_api_errors

logger = logging.getLogger(LOGGER_NAME)

firmware_bp = Blueprint('firmware', __name__)

FIRMWARE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static', 'firmware')
ALLOWED_EXTENSIONS = {'bin'}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB


def _allowed(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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
    return send_from_directory(FIRMWARE_DIR, row['filename'], as_attachment=True)


# ── Endpoints admin ───────────────────────────────────────────────────────────

@firmware_bp.route('/api/admin/firmware', methods=['GET'])
@require_login(['admin'])
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
@require_login(['admin'])
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

    filename  = secure_filename(file.filename)
    file_path = os.path.join(FIRMWARE_DIR, filename)

    # Evitar sobreescribir sin querer
    if os.path.exists(file_path):
        return jsonify({'error': f'Ya existe un archivo con ese nombre: {filename}'}), 409

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


@firmware_bp.route('/api/admin/firmware/<int:firmware_id>/activar', methods=['PUT'])
@require_login(['admin'])
@handle_api_errors
def firmware_activar(firmware_id: int):
    """Activa una versión y desactiva todas las demás."""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'db_error'}), 500
    cur = get_db_cursor(conn)

    cur.execute("SELECT id FROM firmware WHERE id = %s", (firmware_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({'error': 'Versión no encontrada'}), 404

    cur.execute("UPDATE firmware SET is_active = 0")
    cur.execute("UPDATE firmware SET is_active = 1 WHERE id = %s", (firmware_id,))
    conn.commit()
    cur.close()
    conn.close()

    logger.info(f"Firmware {firmware_id} activado por user {session.get('user_id')}")
    return jsonify({'ok': True, 'active_id': firmware_id}), 200
