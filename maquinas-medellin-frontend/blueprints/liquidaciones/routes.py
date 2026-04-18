import logging
import traceback

from flask import Blueprint, request, jsonify

from config import LOGGER_NAME
from database import get_db_connection, get_db_cursor
from utils.auth import require_login
from utils.location_scope import apply_location_name_filter, get_active_location, user_can_view_all
from utils.responses import api_response, handle_api_errors
from utils.timezone import get_colombia_time

logger = logging.getLogger(LOGGER_NAME)

liquidaciones_bp = Blueprint('liquidaciones', __name__)


@liquidaciones_bp.route('/api/ventas-liquidadas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def obtener_ventas_liquidadas():
    """Obtener ventas liquidadas con distribución real."""
    connection = None
    cursor = None
    try:
        logger.info("=== INICIANDO OBTENER VENTAS LIQUIDADAS ===")

        fecha_inicio = request.args.get('fechaInicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = request.args.get('fechaFin', get_colombia_time().strftime('%Y-%m-%d'))
        pagina = int(request.args.get('pagina', 1))
        por_pagina = int(request.args.get('porPagina', 50))
        offset = (pagina - 1) * por_pagina

        logger.info(f"Parámetros recibidos: fecha_inicio={fecha_inicio}, fecha_fin={fecha_fin}, pagina={pagina}")

        connection = get_db_connection()
        if not connection:
            logger.error("No se pudo conectar a la BD")
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        _sql_count = """
            SELECT COUNT(*) as total
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            """
        _sql_count, _params_count = apply_location_name_filter(
            _sql_count, [fecha_inicio, fecha_fin], column='local', table_alias='qh'
        )
        cursor.execute(_sql_count, _params_count)
        total_result = cursor.fetchone()
        total = total_result['total'] if total_result else 0

        logger.info(f"Total de ventas encontradas: {total}")

        try:
            cursor.execute("SHOW TABLES LIKE 'maquinaporcentajerestaurante'")
            tiene_porcentaje = cursor.fetchone() is not None

            cursor.execute("SHOW TABLES LIKE 'maquinapropietario'")
            tiene_propietarios = cursor.fetchone() is not None

            logger.info(f"Tablas disponibles: porcentaje={tiene_porcentaje}, propietarios={tiene_propietarios}")
        except Exception as e:
            logger.warning(f"Error verificando tablas: {e}")
            tiene_porcentaje = False
            tiene_propietarios = False

        if total == 0:
            logger.info("No hay ventas en el período especificado")
            return jsonify(
                {
                    'datos': [],
                    'totalRegistros': 0,
                    'totalIngresos': 0,
                    'gananciaTotal': 0,
                    'gananciaProveedor': 0,
                    'gananciaRestaurante': 0,
                    'paginaActual': pagina,
                    'totalPaginas': 1,
                    'mensaje': 'No hay ventas registradas en el período seleccionado',
                }
            )

        if tiene_porcentaje and tiene_propietarios:
            logger.info("Usando consulta completa con tablas de porcentaje y propietarios")
            _sql, _params = apply_location_name_filter(
                """
                SELECT
                    DATE(qh.fecha_hora) as fecha,
                    qh.qr_code,
                    qh.user_name as vendedor,
                    tp.name as paquete_nombre,
                    tp.price as precio_unitario,
                    1 as cantidad_paquetes,
                    tp.price as ingresos_totales,
                    COALESCE(m.name, 'Máquina no especificada') as maquina_nombre,
                    COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante,
                    (tp.price * COALESCE(mpr.porcentaje_restaurante, 35.00) / 100) as ingresos_restaurante,
                    (tp.price * (100 - COALESCE(mpr.porcentaje_restaurante, 35.00)) / 100) as ingresos_proveedor,
                    (tp.price * 0.30) as ingresos_30_porciento,
                    (tp.price * 0.35) as ingresos_35_porciento,
                    COALESCE(p.nombre, 'Propietario general') as propietario,
                    COALESCE(mp.porcentaje_propiedad, 100.00) as porcentaje_propiedad
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                LEFT JOIN turnusage tu ON qr.id = tu.qrCodeId
                LEFT JOIN machine m ON tu.machineId = m.id
                LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                LEFT JOIN maquinapropietario mp ON m.id = mp.maquina_id
                LEFT JOIN propietarios p ON mp.propietario_id = p.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                ORDER BY qh.fecha_hora DESC
                LIMIT %s OFFSET %s
                """,
                [fecha_inicio, fecha_fin, por_pagina, offset],
                column='local', table_alias='qh',
            )
            cursor.execute(_sql, _params)
        else:
            logger.info("Usando consulta simplificada (sin tablas de porcentaje/propietarios)")
            _sql, _params = apply_location_name_filter(
                """
                SELECT
                    DATE(qh.fecha_hora) as fecha,
                    qh.qr_code,
                    qh.user_name as vendedor,
                    tp.name as paquete_nombre,
                    tp.price as precio_unitario,
                    1 as cantidad_paquetes,
                    tp.price as ingresos_totales,
                    'Máquina no especificada' as maquina_nombre,
                    35.00 as porcentaje_restaurante,
                    (tp.price * 35.00 / 100) as ingresos_restaurante,
                    (tp.price * 65.00 / 100) as ingresos_proveedor,
                    (tp.price * 0.30) as ingresos_30_porciento,
                    (tp.price * 0.35) as ingresos_35_porciento,
                    'Propietario general' as propietario,
                    100.00 as porcentaje_propiedad
                FROM qrhistory qh
                JOIN qrcode qr ON qr.code = qh.qr_code
                JOIN turnpackage tp ON qr.turnPackageId = tp.id
                WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                  AND qr.turnPackageId IS NOT NULL
                  AND qr.turnPackageId != 1
                  AND qh.es_venta_real = TRUE
                ORDER BY qh.fecha_hora DESC
                LIMIT %s OFFSET %s
                """,
                [fecha_inicio, fecha_fin, por_pagina, offset],
                column='local', table_alias='qh',
            )
            cursor.execute(_sql, _params)

        ventas = cursor.fetchall()
        logger.info(f"Ventas obtenidas: {len(ventas)} registros")

        total_ingresos = sum(float(v['ingresos_totales']) for v in ventas)
        total_restaurante = sum(float(v['ingresos_restaurante']) for v in ventas)
        total_proveedor = sum(float(v['ingresos_proveedor']) for v in ventas)

        logger.info(
            f"Totales calculados: ingresos={total_ingresos}, restaurante={total_restaurante}, proveedor={total_proveedor}"
        )

        return jsonify(
            {
                'datos': ventas,
                'totalRegistros': total,
                'totalIngresos': total_ingresos,
                'gananciaTotal': total_ingresos,
                'gananciaProveedor': total_proveedor,
                'gananciaRestaurante': total_restaurante,
                'paginaActual': pagina,
                'totalPaginas': (total + por_pagina - 1) // por_pagina,
            }
        )
    except Exception as e:
        logger.error(f"Error obteniendo ventas liquidadas: {e}", exc_info=True)
        logger.error(f"Traceback completo: {traceback.format_exc()}")
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@liquidaciones_bp.route('/api/liquidaciones/calcular', methods=['POST'])
@handle_api_errors
@require_login(['admin'])
def calcular_liquidacion():
    """Calcular liquidación detallada por período."""
    connection = None
    cursor = None
    try:
        logger.info("=== INICIANDO CALCULO DE LIQUIDACIÓN ===")

        data = request.get_json()
        fecha_inicio = data.get('fecha_inicio', get_colombia_time().strftime('%Y-%m-%d'))
        fecha_fin = data.get('fecha_fin', get_colombia_time().strftime('%Y-%m-%d'))

        logger.info(f"Calculando liquidación para {fecha_inicio} a {fecha_fin}")

        connection = get_db_connection()
        if not connection:
            logger.error("No se pudo conectar a la BD")
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)

        try:
            cursor.execute("SHOW TABLES LIKE 'maquinaporcentajerestaurante'")
            tiene_porcentaje = cursor.fetchone() is not None

            cursor.execute("SHOW TABLES LIKE 'maquinapropietario'")
            tiene_propietarios = cursor.fetchone() is not None

            cursor.execute("SHOW TABLES LIKE 'propietarios'")
            tiene_tabla_propietarios = cursor.fetchone() is not None

            logger.info(
                f"Tablas disponibles: porcentaje={tiene_porcentaje}, "
                f"maquinapropietario={tiene_propietarios}, propietarios={tiene_tabla_propietarios}"
            )
        except Exception as e:
            logger.warning(f"Error verificando tablas: {e}")
            tiene_porcentaje = False
            tiene_propietarios = False
            tiene_tabla_propietarios = False

        _sql_p, _params_p = apply_location_name_filter(
            """
            SELECT
                COUNT(DISTINCT qh.qr_code) as total_ventas,
                COALESCE(SUM(tp.price), 0) as total_ingresos,
                COUNT(DISTINCT m.id) as maquinas_utilizadas
            FROM qrhistory qh
            JOIN qrcode qr ON qr.code = qh.qr_code
            JOIN turnpackage tp ON qr.turnPackageId = tp.id
            LEFT JOIN turnusage tu ON qr.id = tu.qrCodeId
            LEFT JOIN machine m ON tu.machineId = m.id
            WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
              AND qr.turnPackageId IS NOT NULL
              AND qr.turnPackageId != 1
              AND qh.es_venta_real = TRUE
            """,
            [fecha_inicio, fecha_fin],
            column='local', table_alias='qh',
        )
        cursor.execute(_sql_p, _params_p)
        periodo = cursor.fetchone()
        logger.info(f"Estadísticas período: {periodo}")

        if not periodo or periodo['total_ventas'] == 0:
            logger.info("No hay ventas en el período")
            return jsonify(
                {
                    'success': True,
                    'periodo': {
                        'fecha_inicio': fecha_inicio,
                        'fecha_fin': fecha_fin,
                        'total_ventas': 0,
                        'total_ingresos': 0,
                        'total_restaurante': 0,
                        'total_proveedor': 0,
                        'maquinas_utilizadas': 0,
                    },
                    'distribucion_propietarios': {},
                    'resumen_maquinas': {},
                    'datos_tabla': [],
                    'totales': {
                        'ingresos_totales': 0,
                        'ganancia_restaurante': 0,
                        'ganancia_proveedores': 0,
                    },
                }
            )

        total_ingresos = float(periodo['total_ingresos'] or 0)

        distribucion_propietarios = {}
        if tiene_propietarios and tiene_tabla_propietarios:
            try:
                _sql_dp, _params_dp = apply_location_name_filter(
                    """
                    SELECT
                        p.id as propietario_id,
                        p.nombre as propietario_nombre,
                        COUNT(DISTINCT qh.qr_code) as ventas_asociadas,
                        COALESCE(SUM(
                            (tp.price * (100 - COALESCE(mpr.porcentaje_restaurante, 35.00)) / 100)
                            * (mp.porcentaje_propiedad / 100)
                        ), 0) as total_ingresos,
                        GROUP_CONCAT(DISTINCT m.name SEPARATOR ', ') as maquinas_nombres
                    FROM qrhistory qh
                    JOIN qrcode qr ON qr.code = qh.qr_code
                    JOIN turnpackage tp ON qr.turnPackageId = tp.id
                    JOIN turnusage tu ON qr.id = tu.qrCodeId
                    JOIN machine m ON tu.machineId = m.id
                    JOIN maquinapropietario mp ON m.id = mp.maquina_id
                    JOIN propietarios p ON mp.propietario_id = p.id
                    LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                    WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                      AND qr.turnPackageId IS NOT NULL
                      AND qr.turnPackageId != 1
                      AND qh.es_venta_real = TRUE
                    GROUP BY p.id, p.nombre
                    """,
                    [fecha_inicio, fecha_fin],
                    column='local', table_alias='qh',
                )
                cursor.execute(_sql_dp, _params_dp)
                propietarios_data = cursor.fetchall()

                for prop in propietarios_data:
                    distribucion_propietarios[prop['propietario_nombre']] = {
                        'total_ingresos': float(prop['total_ingresos']),
                        'ventas_asociadas': prop['ventas_asociadas'],
                        'detalles_maquinas': prop['maquinas_nombres'].split(', ') if prop['maquinas_nombres'] else [],
                    }

                logger.info(f"Distribución por propietarios: {len(distribucion_propietarios)} propietarios")
            except Exception as e:
                logger.warning(f"Error obteniendo distribución de propietarios: {e}")
                distribucion_propietarios = {}
        else:
            logger.info("Saltando distribución por propietarios (tablas no disponibles)")

        resumen_maquinas = {}
        try:
            if tiene_porcentaje:
                _sql_rm, _params_rm = apply_location_name_filter(
                    """
                    SELECT
                        m.id as maquina_id,
                        m.name as maquina_nombre,
                        m.type as tipo_maquina,
                        COUNT(DISTINCT qh.qr_code) as ventas_realizadas,
                        COALESCE(SUM(tp.price), 0) as ingresos_totales,
                        COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante,
                        COALESCE(SUM(tp.price * COALESCE(mpr.porcentaje_restaurante, 35.00) / 100), 0) as ingresos_restaurante,
                        COALESCE(SUM(tp.price * (100 - COALESCE(mpr.porcentaje_restaurante, 35.00)) / 100), 0) as ingresos_proveedor
                    FROM qrhistory qh
                    JOIN qrcode qr ON qr.code = qh.qr_code
                    JOIN turnpackage tp ON qr.turnPackageId = tp.id
                    JOIN turnusage tu ON qr.id = tu.qrCodeId
                    JOIN machine m ON tu.machineId = m.id
                    LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                    WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                      AND qr.turnPackageId IS NOT NULL
                      AND qr.turnPackageId != 1
                      AND qh.es_venta_real = TRUE
                    GROUP BY m.id, m.name, m.type, mpr.porcentaje_restaurante
                    ORDER BY ingresos_totales DESC
                    """,
                    [fecha_inicio, fecha_fin],
                    column='local', table_alias='qh',
                )
                cursor.execute(_sql_rm, _params_rm)
            else:
                _sql_rm, _params_rm = apply_location_name_filter(
                    """
                    SELECT
                        m.id as maquina_id,
                        m.name as maquina_nombre,
                        m.type as tipo_maquina,
                        COUNT(DISTINCT qh.qr_code) as ventas_realizadas,
                        COALESCE(SUM(tp.price), 0) as ingresos_totales,
                        35.00 as porcentaje_restaurante,
                        COALESCE(SUM(tp.price * 35.00 / 100), 0) as ingresos_restaurante,
                        COALESCE(SUM(tp.price * 65.00 / 100), 0) as ingresos_proveedor
                    FROM qrhistory qh
                    JOIN qrcode qr ON qr.code = qh.qr_code
                    JOIN turnpackage tp ON qr.turnPackageId = tp.id
                    JOIN turnusage tu ON qr.id = tu.qrCodeId
                    JOIN machine m ON tu.machineId = m.id
                    WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                      AND qr.turnPackageId IS NOT NULL
                      AND qr.turnPackageId != 1
                      AND qh.es_venta_real = TRUE
                    GROUP BY m.id, m.name, m.type
                    ORDER BY ingresos_totales DESC
                    """,
                    [fecha_inicio, fecha_fin],
                    column='local', table_alias='qh',
                )
                cursor.execute(_sql_rm, _params_rm)

            maquinas_data = cursor.fetchall()
            for maq in maquinas_data:
                resumen_maquinas[maq['maquina_nombre']] = {
                    'tipo_maquina': maq['tipo_maquina'],
                    'ventas_realizadas': maq['ventas_realizadas'],
                    'ingresos_totales': float(maq['ingresos_totales']),
                    'porcentaje_restaurante': float(maq['porcentaje_restaurante']),
                    'ingresos_restaurante': float(maq['ingresos_restaurante']),
                    'ingresos_proveedor': float(maq['ingresos_proveedor']),
                }

            logger.info(f"Resumen por máquinas: {len(resumen_maquinas)} máquinas")
        except Exception as e:
            logger.warning(f"Error obteniendo resumen por máquinas: {e}")
            resumen_maquinas = {}

        datos_tabla = []
        try:
            if tiene_porcentaje and tiene_propietarios and tiene_tabla_propietarios:
                _sql_dt, _params_dt = apply_location_name_filter(
                    """
                    SELECT
                        DATE(qh.fecha_hora) as fecha,
                        qh.qr_code,
                        tp.name as paquete_nombre,
                        COALESCE(m.name, 'No especificada') as maquina_nombre,
                        tp.price as ingresos_totales,
                        COALESCE(mpr.porcentaje_restaurante, 35.00) as porcentaje_restaurante,
                        (tp.price * COALESCE(mpr.porcentaje_restaurante, 35.00) / 100) as ingresos_restaurante,
                        (tp.price * (100 - COALESCE(mpr.porcentaje_restaurante, 35.00)) / 100) as ingresos_proveedor,
                        COALESCE(p.nombre, 'No asignado') as propietario
                    FROM qrhistory qh
                    JOIN qrcode qr ON qr.code = qh.qr_code
                    JOIN turnpackage tp ON qr.turnPackageId = tp.id
                    LEFT JOIN turnusage tu ON qr.id = tu.qrCodeId
                    LEFT JOIN machine m ON tu.machineId = m.id
                    LEFT JOIN maquinaporcentajerestaurante mpr ON m.id = mpr.maquina_id
                    LEFT JOIN maquinapropietario mp ON m.id = mp.maquina_id
                    LEFT JOIN propietarios p ON mp.propietario_id = p.id
                    WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                      AND qr.turnPackageId IS NOT NULL
                      AND qr.turnPackageId != 1
                      AND qh.es_venta_real = TRUE
                    ORDER BY qh.fecha_hora DESC
                    """,
                    [fecha_inicio, fecha_fin],
                    column='local', table_alias='qh',
                )
                cursor.execute(_sql_dt, _params_dt)
            else:
                _sql_dt, _params_dt = apply_location_name_filter(
                    """
                    SELECT
                        DATE(qh.fecha_hora) as fecha,
                        qh.qr_code,
                        tp.name as paquete_nombre,
                        'No especificada' as maquina_nombre,
                        tp.price as ingresos_totales,
                        35.00 as porcentaje_restaurante,
                        (tp.price * 35.00 / 100) as ingresos_restaurante,
                        (tp.price * 65.00 / 100) as ingresos_proveedor,
                        'No asignado' as propietario
                    FROM qrhistory qh
                    JOIN qrcode qr ON qr.code = qh.qr_code
                    JOIN turnpackage tp ON qr.turnPackageId = tp.id
                    WHERE DATE(qh.fecha_hora) BETWEEN %s AND %s
                      AND qr.turnPackageId IS NOT NULL
                      AND qr.turnPackageId != 1
                      AND qh.es_venta_real = TRUE
                    ORDER BY qh.fecha_hora DESC
                    """,
                    [fecha_inicio, fecha_fin],
                    column='local', table_alias='qh',
                )
                cursor.execute(_sql_dt, _params_dt)

            datos_tabla = cursor.fetchall()
            logger.info(f"Datos tabla: {len(datos_tabla)} registros")
        except Exception as e:
            logger.warning(f"Error obteniendo datos tabla: {e}")
            datos_tabla = []

        total_restaurante = (
            sum(float(m['ingresos_restaurante']) for m in resumen_maquinas.values())
            if resumen_maquinas
            else total_ingresos * 0.35
        )
        total_proveedor = (
            sum(float(m['ingresos_proveedor']) for m in resumen_maquinas.values())
            if resumen_maquinas
            else total_ingresos * 0.65
        )

        logger.info(
            f"Cálculo completado: ingresos={total_ingresos}, restaurante={total_restaurante}, proveedor={total_proveedor}"
        )

        return jsonify(
            {
                'success': True,
                'periodo': {
                    'fecha_inicio': fecha_inicio,
                    'fecha_fin': fecha_fin,
                    'total_ventas': periodo['total_ventas'],
                    'total_ingresos': total_ingresos,
                    'total_restaurante': total_restaurante,
                    'total_proveedor': total_proveedor,
                    'maquinas_utilizadas': periodo['maquinas_utilizadas'],
                },
                'distribucion_propietarios': distribucion_propietarios,
                'resumen_maquinas': resumen_maquinas,
                'datos_tabla': datos_tabla,
                'totales': {
                    'ingresos_totales': total_ingresos,
                    'ganancia_restaurante': total_restaurante,
                    'ganancia_proveedores': total_proveedor,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error calculando liquidación: {e}", exc_info=True)
        logger.error(f"Traceback completo: {traceback.format_exc()}")
        return api_response('E001', http_status=500, data={'error_detail': str(e)})
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@liquidaciones_bp.route('/api/liquidaciones/verificar-tablas', methods=['GET'])
@handle_api_errors
@require_login(['admin'])
def verificar_tablas_liquidaciones():
    """Verificar qué tablas existen para liquidaciones."""
    connection = None
    cursor = None
    try:
        logger.info("Verificando tablas para liquidaciones...")

        connection = get_db_connection()
        if not connection:
            return api_response('E006', http_status=500)

        cursor = get_db_cursor(connection)
        tablas_requeridas = [
            'maquinaporcentajerestaurante',
            'maquinapropietario',
            'propietarios',
            'liquidaciones',
            'liquidacion_detalles',
            'reportes_generados',
        ]

        resultados = {}
        for tabla in tablas_requeridas:
            cursor.execute("SHOW TABLES LIKE %s", (tabla,))
            existe = cursor.fetchone() is not None
            resultados[tabla] = existe

            if existe:
                try:
                    cursor.execute(f"DESCRIBE {tabla}")
                    columnas = cursor.fetchall()
                    resultados[f"{tabla}_columnas"] = [col['Field'] for col in columnas]
                except Exception as e:
                    resultados[f"{tabla}_error"] = str(e)

        tablas_con_datos = {}
        for tabla in ['maquinaporcentajerestaurante', 'maquinapropietario', 'propietarios']:
            if resultados.get(tabla):
                cursor.execute(f"SELECT COUNT(*) as count FROM {tabla}")
                count_result = cursor.fetchone()
                tablas_con_datos[tabla] = count_result['count'] if count_result else 0

        logger.info(f"Resultados verificación tablas: {resultados}")

        return jsonify(
            {
                'tablas': resultados,
                'tablas_con_datos': tablas_con_datos,
                'recomendaciones': [
                    'Todas las tablas existen' if all(resultados.values()) else 'Faltan algunas tablas',
                    (
                        'Configurar porcentajes de restaurante en maquinaporcentajerestaurante'
                        if resultados.get('maquinaporcentajerestaurante')
                        else 'Crear tabla maquinaporcentajerestaurante'
                    ),
                    (
                        'Configurar propietarios en maquinapropietario y propietarios'
                        if resultados.get('maquinapropietario') and resultados.get('propietarios')
                        else 'Crear tablas de propietarios'
                    ),
                ],
            }
        )
    except Exception as e:
        logger.error(f"Error verificando tablas: {e}")
        return api_response('E001', http_status=500)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
