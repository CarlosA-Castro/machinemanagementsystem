-- V74: migrar a system_messages los textos hardcodeados del blueprint qr
-- (blueprints/qr/routes.py): QR, contadores, ventas y reportes. Continua la
-- migracion progresiva (V72 roles, V73 admin/CRUD). Idempotente (INSERT IGNORE).
--
-- Reusos sin codigo nuevo: 'No hay historial para este QR'->Q010 (V73),
-- 'Usuario no autenticado'->A004. Duplicados unificados: 'Metodo de pago
-- invalido' x3->Q016, 'Reporte no encontrado' x2->Q022.

INSERT IGNORE INTO `system_messages` (`message_code`, `message_type`, `message_text`, `language_code`) VALUES
('Q011', 'error', 'Contador no encontrado',                              'es'),
('Q012', 'error', 'El valor debe estar entre 0 y 9999',                  'es'),
('Q013', 'error', 'Valor inválido',                                      'es'),
('Q014', 'error', 'Cantidad debe estar entre 1 y 1000',                  'es'),
('Q015', 'error', 'No se pueden generar más de 9999 códigos a la vez',   'es'),
('Q016', 'error', 'Método de pago inválido',                             'es'),
('Q017', 'error', 'Lista de QR vacía',                                   'es'),
('Q018', 'error', 'Debes indicar un motivo de al menos 5 caracteres',    'es'),
('Q019', 'error', 'La venta no existe o no pertenece al alcance actual', 'es'),
('Q020', 'error', 'El nuevo método debe ser diferente al actual',        'es'),
('Q021', 'error', 'Solo administradores pueden resolver reportes',       'es'),
('Q022', 'error', 'Reporte no encontrado',                               'es'),
('Q023', 'info',  'Función de exportación PDF en desarrollo',            'es');
