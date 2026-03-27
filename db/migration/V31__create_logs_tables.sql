-- V31: Crear tablas de logs para sistema de monitoreo detallado
-- app_logs, access_logs, error_logs, transaction_logs

-- ============================================================
-- app_logs: logs generales de la aplicación Flask
-- ============================================================
CREATE TABLE IF NOT EXISTS `app_logs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `level` varchar(10) NOT NULL DEFAULT 'INFO' COMMENT 'DEBUG, INFO, WARNING, ERROR, CRITICAL',
  `module` varchar(100) DEFAULT NULL COMMENT 'Módulo o función que generó el log',
  `message` text DEFAULT NULL COMMENT 'Mensaje del log',
  `user_id` int(11) DEFAULT NULL COMMENT 'ID del usuario autenticado',
  `user_name` varchar(100) DEFAULT NULL,
  `ip_address` varchar(45) DEFAULT NULL,
  `endpoint` varchar(200) DEFAULT NULL COMMENT 'Ruta de la request que generó el log',
  `extra_data` json DEFAULT NULL COMMENT 'Datos adicionales estructurados',
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_level` (`level`),
  KEY `idx_created_at` (`created_at`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_module` (`module`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================
-- access_logs: log automático de cada request HTTP
-- ============================================================
CREATE TABLE IF NOT EXISTS `access_logs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `method` varchar(10) DEFAULT NULL COMMENT 'GET, POST, PUT, DELETE',
  `path` varchar(500) DEFAULT NULL COMMENT 'URL de la request',
  `status_code` int(11) DEFAULT NULL COMMENT 'Código HTTP de respuesta',
  `response_time_ms` int(11) DEFAULT NULL COMMENT 'Tiempo de respuesta en ms',
  `user_id` int(11) DEFAULT NULL,
  `user_name` varchar(100) DEFAULT NULL,
  `ip_address` varchar(45) DEFAULT NULL,
  `user_agent` varchar(500) DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_status_code` (`status_code`),
  KEY `idx_created_at` (`created_at`),
  KEY `idx_path` (`path`(100)),
  KEY `idx_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================
-- error_logs: errores y excepciones con traceback
-- ============================================================
CREATE TABLE IF NOT EXISTS `error_logs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `level` varchar(10) NOT NULL DEFAULT 'ERROR',
  `module` varchar(100) DEFAULT NULL,
  `message` text DEFAULT NULL,
  `traceback` text DEFAULT NULL COMMENT 'Stack trace completo',
  `user_id` int(11) DEFAULT NULL,
  `ip_address` varchar(45) DEFAULT NULL,
  `endpoint` varchar(200) DEFAULT NULL,
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_level` (`level`),
  KEY `idx_created_at` (`created_at`),
  KEY `idx_module` (`module`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================
-- transaction_logs: log transaccional financiero y operacional
-- Registra: turnos, pagos, liquidaciones, inversiones, fallas, configs
-- ============================================================
CREATE TABLE IF NOT EXISTS `transaction_logs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `tipo` varchar(50) NOT NULL COMMENT 'turno_qr, turno_manual, pago_cuota, inversion, liquidacion, falla_maquina, resolver_falla, config_maquina, login, logout',
  `categoria` varchar(30) NOT NULL DEFAULT 'operacional' COMMENT 'financiero, operacional, admin, seguridad',
  `descripcion` varchar(500) NOT NULL COMMENT 'Descripción legible del evento',
  `usuario` varchar(100) DEFAULT NULL COMMENT 'Nombre del usuario que realizó la acción',
  `usuario_id` int(11) DEFAULT NULL,
  `maquina_id` int(11) DEFAULT NULL,
  `maquina_nombre` varchar(200) DEFAULT NULL,
  `entidad` varchar(50) DEFAULT NULL COMMENT 'maquina, socio, qr, liquidacion, inversion',
  `entidad_id` int(11) DEFAULT NULL COMMENT 'ID del objeto relacionado',
  `monto` decimal(12,2) DEFAULT NULL COMMENT 'Monto económico involucrado en COP',
  `moneda` varchar(10) DEFAULT 'COP',
  `datos_extra` json DEFAULT NULL COMMENT 'Detalles adicionales estructurados (turnos restantes, porcentaje, etc.)',
  `ip_address` varchar(45) DEFAULT NULL,
  `estado` varchar(20) DEFAULT 'ok' COMMENT 'ok, error, advertencia',
  `created_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_tipo` (`tipo`),
  KEY `idx_categoria` (`categoria`),
  KEY `idx_created_at` (`created_at`),
  KEY `idx_maquina_id` (`maquina_id`),
  KEY `idx_usuario_id` (`usuario_id`),
  KEY `idx_estado` (`estado`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
