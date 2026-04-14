from datetime import datetime

from flask import session, request, redirect, url_for, jsonify

from config import SESSION_TIMEOUT, SESSION_SKIP


def check_session_timeout():
    """
    before_request hook.
    Cierra la sesión automáticamente tras SESSION_TIMEOUT horas de inactividad.
    Las rutas en SESSION_SKIP (login, static) se omiten.
    """
    if request.endpoint in SESSION_SKIP:
        return

    if not session.get('logged_in'):
        return

    last = session.get('last_activity')
    if last:
        try:
            idle = datetime.utcnow() - datetime.fromisoformat(last)
            if idle > SESSION_TIMEOUT:
                session.clear()
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'error': 'session_expired', 'redirect': '/login'}), 401
                return redirect(url_for('mostrar_login'))
        except Exception:
            pass

    session['last_activity'] = datetime.utcnow().isoformat()
    session.modified = True
