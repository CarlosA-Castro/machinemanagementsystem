-- V72: mover a system_messages los textos de cara al usuario de los endpoints
-- de roles (blueprints/roles/routes.py), que hoy estan hardcodeados en el
-- codigo dentro de data={'message': ...}.
--
-- Contexto: esos endpoints devolvian api_response('E005', data={'message':'X'}),
-- por lo que el 'message' raiz era el generico de E005 ("Parametros invalidos")
-- y el texto especifico viajaba en data.message anidado. El frontend
-- (gestionusuarios.html) lee el 'message' raiz, no el anidado, asi que los
-- textos especificos nunca se mostraban. Al darle a cada uno su propio codigo,
-- el 'message' raiz pasa a ser el texto correcto.
--
-- Prefijo R = roles (no existia en la tabla). Piloto de la migracion progresiva
-- de mensajes hardcodeados. Idempotente: INSERT IGNORE respeta UNIQUE(message_code).

INSERT IGNORE INTO `system_messages` (`message_code`, `message_type`, `message_text`, `language_code`) VALUES
('R001', 'error', 'El nombre del rol es requerido',                          'es'),
('R002', 'error', 'Solo letras minúsculas y guiones bajos',                  'es'),
('R003', 'error', 'Máximo 50 caracteres',                                    'es'),
('R004', 'error', 'El rol ya existe',                                        'es'),
('R005', 'error', 'El rol administrador es inmutable y no se puede editar',  'es'),
('R006', 'error', 'Permisos inválidos',                                      'es'),
('R007', 'error', 'Rol no encontrado',                                       'es'),
('R008', 'error', 'El rol administrador no se puede eliminar',               'es'),
('R009', 'error', 'Hay {count} usuarios con este rol. Reasígnalos primero.', 'es');
