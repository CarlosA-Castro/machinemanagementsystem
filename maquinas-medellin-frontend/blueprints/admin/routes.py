import logging

from flask import Blueprint, session, redirect, render_template

from config import LOGGER_NAME
from utils.auth import get_user_permissions, require_login
from utils.timezone import get_colombia_time

logger = logging.getLogger(LOGGER_NAME)

admin_bp = Blueprint('admin', __name__)


# ── Dashboard principal ───────────────────────────────────────────────────────

@admin_bp.route('/admin')
def mostrar_admin():
    if not session.get('logged_in'):
        return redirect('/login')
    permisos = get_user_permissions()
    if 'admin_panel' not in permisos:
        return redirect('/local')
    hora_colombia = get_colombia_time()
    return render_template(
        'admin/index.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


# ── Gestión usuarios / paquetes / locales / máquinas ─────────────────────────

@admin_bp.route('/admin/usuarios/gestionusuarios')
def mostrar_gestion_usuarios():
    if not session.get('logged_in'):
        return redirect('/login')
    permisos = get_user_permissions()
    if 'admin_panel' not in permisos or 'ver_usuarios' not in permisos:
        return redirect('/local')
    return render_template(
        'admin/usuarios/gestionusuarios.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
    )


@admin_bp.route('/admin/paquetes/gestionpaquetes')
@require_login(['admin'])
def mostrar_gestion_paquetes():
    return render_template(
        'admin/paquetes/gestionpaquetes.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
    )


@admin_bp.route('/admin/locales/listalocales')
@admin_bp.route('/admin/locales/gestionlocales')
@require_login(['admin'])
def mostrar_gestion_locales():
    return render_template(
        'admin/locales/gestionlocales.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
    )


@admin_bp.route('/admin/maquinas/gestionmaquinas')
def mostrar_gestion_maquinas():
    if not session.get('logged_in'):
        return redirect('/login')
    permisos = get_user_permissions()
    if 'admin_panel' not in permisos or 'ver_maquinas' not in permisos:
        return redirect('/local')
    return render_template(
        'admin/maquinas/gestionmaquinas.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
    )


@admin_bp.route('/admin/maquinas/tickets-mantenimiento')
def mostrar_tickets_mantenimiento():
    if not session.get('logged_in'):
        return redirect('/login')
    permisos = get_user_permissions()
    if 'admin_panel' not in permisos or 'ver_maquinas' not in permisos:
        return redirect('/local')
    hora_colombia = get_colombia_time()
    return render_template(
        'admin/maquinas/ticketsmantenimiento.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


# ── Ventas / reportes ─────────────────────────────────────────────────────────

@admin_bp.route('/admin/ventas/liquidaciones')
def mostrar_liquidaciones():
    if not session.get('logged_in'):
        return redirect('/login')
    permisos = get_user_permissions()
    if 'admin_panel' not in permisos or 'ver_liquidaciones' not in permisos:
        return redirect('/local')
    hora_colombia = get_colombia_time()
    return render_template(
        'ventas/liquidaciones.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


@admin_bp.route('/admin/ventas/reportes')
@require_login(['admin'])
def mostrar_reportes():
    hora_colombia = get_colombia_time()
    return render_template(
        'ventas/reportes.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


# ── Mensajes ──────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/mensajes/gestionmensajes')
@require_login(['admin'])
def mostrar_gestion_mensajes():
    hora_colombia = get_colombia_time()
    return render_template(
        'admin/mensajes/gestionmensajes.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


# ── Logs ──────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/logs/transaccionales')
def mostrar_logs_transaccionales():
    if not session.get('logged_in'):
        return redirect('/login')
    permisos = get_user_permissions()
    if 'admin_panel' not in permisos or 'ver_logs' not in permisos:
        return redirect('/local')
    hora_colombia = get_colombia_time()
    return render_template(
        'admin/logs/logtransaccional.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


@admin_bp.route('/admin/logs/gestionlogs')
def mostrar_gestion_logs():
    if not session.get('logged_in'):
        return redirect('/login')
    permisos = get_user_permissions()
    if 'admin_panel' not in permisos or 'ver_logs' not in permisos:
        return redirect('/local')
    hora_colombia = get_colombia_time()
    return render_template(
        'admin/logs/gestionlogs.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


@admin_bp.route('/admin/logs/consola-completa')
def mostrar_consola_completa():
    if not session.get('logged_in'):
        return redirect('/login')
    permisos = get_user_permissions()
    if 'admin_panel' not in permisos or 'ver_logs' not in permisos:
        return redirect('/local')
    hora_colombia = get_colombia_time()
    return render_template(
        'admin/logs/consola-completa.html',
        nombre_usuario=session.get('user_name', 'Administrador'),
        local_usuario=session.get('user_local', 'Sistema'),
        hora_actual=hora_colombia.strftime('%H:%M:%S'),
        fecha_actual=hora_colombia.strftime('%Y-%m-%d'),
    )


# ── Inversores ────────────────────────────────────────────────────────────────
# FASE 3: /admin/inversores/gestionsocios se migra junto con el blueprint partners
