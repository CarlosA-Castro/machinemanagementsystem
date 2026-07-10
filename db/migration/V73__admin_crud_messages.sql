-- V73: migrar a system_messages los textos hardcodeados de los endpoints
-- admin/CRUD (paquetes, maquinas, usuarios, propietarios, locales, socios,
-- mensajes, dashboard, devoluciones, pagos, inversiones, historial, app.py).
-- Continua el piloto V72 (roles). Solo endpoints admin, sin hardware ESP32.
-- Idempotente: INSERT IGNORE respeta UNIQUE(message_code).
--
-- Prefijos: P=paquetes, M=maquinas, U=usuarios, O=propietarios, L=locales,
-- SC=socios, W=warning, S=success, G=general, D=devoluciones, Q=qr/turnos.

INSERT IGNORE INTO `system_messages` (`message_code`, `message_type`, `message_text`, `language_code`) VALUES
-- paquetes
('P002', 'error', 'Turnos debe ser mayor a 0',                      'es'),
('P003', 'error', 'Precio debe ser mayor a $1,000',                 'es'),
('P004', 'error', 'Duración debe ser mayor a 0 días',               'es'),
('P005', 'error', 'Paquete ya existe en este local',                'es'),
('P006', 'error', 'Nombre de paquete ya existe',                    'es'),
-- maquinas
('M008', 'error', 'Tipo de máquina inválido',                       'es'),
('M009', 'error', 'Estado inválido',                                'es'),
('M010', 'error', 'Porcentaje debe estar entre 0 y 100',            'es'),
('M011', 'error', 'Máquina ya existe',                              'es'),
('M012', 'error', 'Nombre de máquina ya existe',                    'es'),
-- usuarios
('U006', 'error', 'Rol no válido',                                  'es'),
('U007', 'error', 'El nombre de usuario debe tener entre 4 y 20 caracteres, solo letras mayúsculas y números. Ej: AFGOMEZ', 'es'),
('U008', 'error', 'Local no encontrado o inactivo',                 'es'),
('U009', 'error', 'No puedes cambiar tu propio estado',             'es'),
-- propietarios
('O001', 'error', 'Propietario ya existe',                          'es'),
('O002', 'error', 'Nombre de propietario ya existe',                'es'),
-- locales
('L001', 'error', 'Local ya existe',                                'es'),
('L002', 'error', 'Nombre de local ya existe',                      'es'),
-- socios
('SC001', 'error', 'Socio no encontrado',                          'es'),
('SC002', 'error', 'No se encontró información de socio asociada a tu usuario', 'es'),
-- mensajes (admin)
('W007', 'warning', 'No se pueden eliminar mensajes del sistema esenciales', 'es'),
('S016', 'success', 'Caché de mensajes recargado',                 'es'),
-- generales / compartidos
('G001', 'error', 'No hay campos para actualizar',                 'es'),
('D002', 'error', 'La máquina no pertenece al local activo',       'es'),
('Q009', 'error', 'Registro de uso no encontrado',                 'es'),
('Q010', 'info',  'No hay historial para este QR',                 'es');
