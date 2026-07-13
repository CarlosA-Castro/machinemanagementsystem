-- V75: migrar a system_messages los textos hardcodeados del blueprint esp32
-- (blueprints/esp32/routes.py). Ultimo bloque de la migracion progresiva
-- (V72 roles, V73 admin/CRUD, V74 qr). Idempotente (INSERT IGNORE).
--
-- El firmware (.ino) lee SOLO 'status' y 'message' RAIZ (nunca 'code'), y en
-- exito lee data[...]; por eso convertir errores a api_response('CODE') es
-- seguro: status y message raiz se preservan. Prefijo H = hardware/esp32.
--
-- Reusos sin codigo nuevo: 'QR vencido'->Q007, 'Este QR es de otro local'->
-- Q008, 'Maquina no encontrada'->M001. Se quitaron data.message redundantes
-- en respuestas de exito cuyo root ya trae el texto (S012, S013) y en el 500
-- de reportar falla (E001). No se tocaron 2 respuestas diagnosticas cuyo
-- 'message' no es texto de usuario y cuya forma lee el firmware: healthcheck
-- (status 'online') y el fallback de maquina no registrada (status 'offline').

INSERT IGNORE INTO `system_messages` (`message_code`, `message_type`, `message_text`, `language_code`) VALUES
('H001', 'error', 'machine_id requerido',                                          'es'),
('H002', 'error', 'Faltan datos: usage_id, machine_id y station_index son requeridos', 'es'),
('H003', 'error', 'Faltan datos: machine_id y qr_code son requeridos',             'es'),
('H004', 'error', 'Código QR no existe en el sistema',                             'es'),
('H005', 'error', 'No hay juegos registrados para este QR en esta máquina',        'es'),
('H006', 'error', 'module_code requerido',                                         'es'),
('H007', 'error', 'Módulo no encontrado',                                          'es'),
('H008', 'error', 'device_id y event son requeridos',                              'es');
