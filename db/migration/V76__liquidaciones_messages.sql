-- V76: migrar a system_messages los textos hardcodeados del blueprint
-- liquidaciones (blueprints/liquidaciones/routes.py). Cierra la migracion
-- progresiva (V72 roles, V73 admin/CRUD, V74 qr, V75 esp32). Idempotente.
--
-- Nota: 3 de estos endpoints devuelven jsonify({'success': bool, ...}) y el
-- frontend (liquidaciones.html) ramifica por .success; para NO cambiar la
-- forma de la respuesta, esos usan MessageService.get_error_message(code)
-- (solo el texto) en vez de api_response. Los otros 2 si usan api_response.
-- Prefijo LQ = liquidaciones.

INSERT IGNORE INTO `system_messages` (`message_code`, `message_type`, `message_text`, `language_code`) VALUES
('LQ001', 'error', 'Este periodo se cruza con un cierre oficial ya registrado.', 'es'),
('LQ002', 'info',  'No hay cierres registrados.',                                'es'),
('LQ003', 'error', 'Cierre no encontrado o fuera de tu alcance.',                'es'),
('LQ004', 'error', 'La tabla liquidaciones no está disponible',                  'es'),
('LQ005', 'error', 'Los porcentajes deben ser positivos y sumar menos de 100',   'es');
