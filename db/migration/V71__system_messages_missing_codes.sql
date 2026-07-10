-- V71: sembrar los codigos de system_messages que el codigo usa pero que
-- faltaban en la tabla, y llevar la tabla a Flyway (antes solo vivia en el
-- dump maquinas_medellin_xampp.sql, por lo que un entorno reconstruido desde
-- cero no la tenia).
--
-- Codigos referenciados por endpoints pero ausentes del seed:
--   D001, Q007, Q008, S011, S012, S013, S014, S015, W006
-- Sin estas filas, MessageService cae al fallback y responde
-- "Mensaje no configurado: <codigo>" (salvo D001, que si estaba en el
-- fallback de Python). Ver utils/messages.py y utils/responses.py.

-- 1) La tabla, idempotente (no la toca si ya existe, p.ej. en EC2/produccion).
CREATE TABLE IF NOT EXISTS `system_messages` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `message_code` varchar(50) NOT NULL,
  `message_type` enum('error','success','warning','info') NOT NULL,
  `message_text` text NOT NULL,
  `language_code` varchar(10) DEFAULT 'es',
  `created_at` datetime DEFAULT current_timestamp(),
  `updated_at` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `message_code` (`message_code`),
  KEY `idx_message_code` (`message_code`),
  KEY `idx_message_type` (`message_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2) Sembrado idempotente: INSERT IGNORE respeta el UNIQUE de message_code,
--    asi que si un codigo ya existe en produccion no se pisa ni falla.
INSERT IGNORE INTO `system_messages` (`message_code`, `message_type`, `message_text`, `language_code`) VALUES
('D001', 'error',   'El turno de este juego ya fue devuelto',              'es'),
('Q007', 'error',   'QR vencido',                                          'es'),
('Q008', 'error',   'Este QR es de otro local',                            'es'),
('S011', 'success', 'Configuración técnica obtenida correctamente',        'es'),
('S012', 'success', 'Falla reportada y turno devuelto automáticamente',    'es'),
('S013', 'success', 'Reinicio registrado correctamente',                   'es'),
('S014', 'success', 'Turno manual enviado correctamente',                  'es'),
('S015', 'success', 'Reinicio de máquina registrado correctamente',        'es'),
('W006', 'warning', 'No se puede eliminar: tiene registros asociados',     'es');
