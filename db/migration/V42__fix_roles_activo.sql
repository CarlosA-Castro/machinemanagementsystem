-- V42: Garantizar que los 4 roles base existan y estén activos
-- Causa: SELECT id FROM roles WHERE id = %s AND activo = TRUE retornaba vacío (U004 400)

INSERT INTO roles (id, nombre, descripcion, color, icono, nivel_acceso, permisos, activo)
VALUES
    ('admin',            'Administrador',          'Acceso total al sistema',                         'purple', 'fa-shield-alt',   'administrador', '["ver","crear","editar","eliminar","admin_panel"]', TRUE),
    ('cajero',           'Cajero',                 'Gestión de ventas y turnos',                      'blue',   'fa-cash-register','medio',         '["ver","crear"]',                                   TRUE),
    ('socio',            'Socio',                  'Visualización de rendimientos propios',           'teal',   'fa-user-tie',     'bajo',          '["ver"]',                                           TRUE),
    ('admin_restaurante','Administrador Restaurante','Gestión de restaurante - acceso equivalente a cajero', 'pink', 'fa-utensils', 'medio',        '["ver","crear"]',                                   TRUE)
ON DUPLICATE KEY UPDATE
    activo = TRUE;
