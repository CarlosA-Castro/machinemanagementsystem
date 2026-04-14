import time
import threading

# ── Estado en memoria de los heartbeats ESP32 ─────────────────────────────────
#
# Clave:  machine_id (int)
# Valor:  { 'wifi': bool, 'server': bool, 'rssi': int, 'ts': float (epoch) }
#
# No persiste entre reinicios — correcto: el ESP32 reenvía heartbeat en segundos.
# _lock garantiza acceso thread-safe cuando múltiples requests concurrentes
# leen o escriben el dict.
#
_heartbeats: dict = {}
_lock = threading.Lock()
_ONLINE_TIMEOUT = 90  # segundos sin heartbeat → considerado offline


def set_heartbeat(machine_id: int, wifi: bool, server: bool, rssi: int) -> None:
    """Actualiza o crea el registro de heartbeat para una máquina."""
    with _lock:
        _heartbeats[machine_id] = {
            'wifi':   wifi,
            'server': server,
            'rssi':   rssi,
            'ts':     time.time(),
        }


def get_heartbeat_fields(machine_id: int) -> dict:
    """
    Retorna los campos esp32_* para incluir en respuestas de /api/maquinas.
    Si no hay heartbeat reciente retorna todos los campos en False/0.
    """
    with _lock:
        hb = _heartbeats.get(int(machine_id))

    if hb and (time.time() - hb['ts']) < _ONLINE_TIMEOUT:
        return {
            'esp32_online': True,
            'esp32_wifi':   hb['wifi'],
            'esp32_server': hb['server'],
            'esp32_rssi':   hb['rssi'],
        }
    return {
        'esp32_online': False,
        'esp32_wifi':   False,
        'esp32_server': False,
        'esp32_rssi':   0,
    }
