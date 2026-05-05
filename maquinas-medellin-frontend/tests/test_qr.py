"""
test_qr.py — Smoke tests de los endpoints ESP32 / validación de QR.

Cubre:
- GET  /api/esp32/status            → siempre online (sin BD)
- POST /api/esp32/heartbeat         → machine_id requerido
- POST /api/esp32/registrar-uso     → campos requeridos
- POST /api/esp32/registrar-uso     → QR inexistente → Q001
- POST /api/esp32/registrar-uso     → QR vencido     → Q007
"""
import json
from datetime import date, timedelta
from unittest.mock import patch

from tests.conftest import make_mock_cursor, make_mock_connection


# ── GET /api/esp32/status ─────────────────────────────────────────────────────

def test_esp32_status_returns_online(client):
    """El endpoint de status siempre devuelve online sin necesidad de BD."""
    response = client.get('/api/esp32/status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'online'


# ── POST /api/esp32/heartbeat ─────────────────────────────────────────────────

def test_heartbeat_without_machine_id_returns_400(client):
    """Heartbeat sin machine_id → 400."""
    response = client.post(
        '/api/esp32/heartbeat',
        data=json.dumps({}),
        content_type='application/json',
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['status'] == 'error'


def test_heartbeat_with_machine_id_returns_ok(client):
    """Heartbeat con machine_id válido → 200 ok (sin BD, solo actualiza estado en memoria)."""
    response = client.post(
        '/api/esp32/heartbeat',
        data=json.dumps({'machine_id': 1, 'wifi_connected': True, 'server_online': True}),
        content_type='application/json',
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'ok'


# ── POST /api/esp32/registrar-uso — validación de campos ─────────────────────

def test_registrar_uso_missing_qr_code_returns_400(client):
    """registrar-uso sin qr_code → 400 (validate_required_fields)."""
    response = client.post(
        '/api/esp32/registrar-uso',
        data=json.dumps({'machine_id': 1}),
        content_type='application/json',
    )
    assert response.status_code == 400


def test_registrar_uso_missing_machine_id_returns_400(client):
    """registrar-uso sin machine_id → 400 (validate_required_fields)."""
    response = client.post(
        '/api/esp32/registrar-uso',
        data=json.dumps({'qr_code': 'QR0001'}),
        content_type='application/json',
    )
    assert response.status_code == 400


# ── POST /api/esp32/registrar-uso — QR inexistente ───────────────────────────

def test_registrar_uso_qr_not_found_returns_404(client):
    """
    Si el QR no existe en BD (fetchone → None) → error Q001 (404).
    """
    cursor = make_mock_cursor(fetchone_result=None)
    conn   = make_mock_connection(cursor)

    with patch('blueprints.esp32.routes.get_db_connection', return_value=conn), \
         patch('blueprints.esp32.routes.get_db_cursor', return_value=cursor):

        response = client.post(
            '/api/esp32/registrar-uso',
            data=json.dumps({'qr_code': 'QR9999', 'machine_id': 1}),
            content_type='application/json',
        )

    assert response.status_code == 404
    data = json.loads(response.data)
    assert data.get('code') == 'Q001'


# ── POST /api/esp32/registrar-uso — QR vencido ───────────────────────────────

def test_registrar_uso_expired_qr_returns_400(client):
    """
    Si el QR existe pero ya venció → error Q007 (400).
    """
    vencido = date.today() - timedelta(days=1)
    qr_row = {
        'id':              1,
        'qr_name':         'QR0001',
        'expiration_date': vencido,
    }
    cursor = make_mock_cursor(fetchone_result=qr_row)
    conn   = make_mock_connection(cursor)

    with patch('blueprints.esp32.routes.get_db_connection', return_value=conn), \
         patch('blueprints.esp32.routes.get_db_cursor', return_value=cursor):

        response = client.post(
            '/api/esp32/registrar-uso',
            data=json.dumps({'qr_code': 'QR0001', 'machine_id': 1}),
            content_type='application/json',
        )

    assert response.status_code == 400
    data = json.loads(response.data)
    assert data.get('code') == 'Q007'
