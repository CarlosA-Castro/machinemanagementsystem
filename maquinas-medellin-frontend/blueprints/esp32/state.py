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
_alerted_offline: set = set()   # machine_ids ya notificados como offline
_newly_online: list = []        # machine_ids que volvieron online (consumir una vez)
_lock = threading.Lock()
_ONLINE_TIMEOUT = 90  # segundos sin heartbeat → considerado offline
_server_start = time.time()     # para ignorar reconexiones del reinicio de servidor


def set_heartbeat(machine_id: int, wifi: bool, server: bool, rssi: int) -> None:
    """Actualiza o crea el registro de heartbeat para una máquina."""
    with _lock:
        was_offline = machine_id in _alerted_offline
        is_first_seen = machine_id not in _heartbeats
        _heartbeats[machine_id] = {
            'wifi':   wifi,
            'server': server,
            'rssi':   rssi,
            'ts':     time.time(),
        }
        if was_offline:
            # Máquina que ya habíamos alertado como offline → notificar online
            _alerted_offline.discard(machine_id)
            _newly_online.append(machine_id)
        elif is_first_seen and (time.time() - _server_start) > _ONLINE_TIMEOUT:
            # Primer heartbeat de un machine_id nuevo (ej: ESP con ID cambiado),
            # pero solo si el servidor lleva > 90s corriendo — evita spam al reiniciar
            # cuando todos los ESP reconectan en los primeros ~30s.
            _newly_online.append(machine_id)


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


def check_offline_machines() -> list:
    """Retorna [(machine_id, segundos_sin_heartbeat)] de máquinas recién caídas.
    Las marca en _alerted_offline para no repetir la alerta."""
    now = time.time()
    result = []
    with _lock:
        for mid, hb in _heartbeats.items():
            age = int(now - hb['ts'])
            if age > _ONLINE_TIMEOUT and mid not in _alerted_offline:
                _alerted_offline.add(mid)
                result.append((mid, age))
    return result


def pop_newly_online() -> list:
    """Retorna y vacía la lista de máquinas que volvieron a conectarse."""
    with _lock:
        result = list(_newly_online)
        _newly_online.clear()
    return result
