-- V30: Quitar admin_panel del rol admin_restaurante
-- admin_restaurante debe tener los mismos permisos que cajero

UPDATE roles
SET permisos       = '["ver","crear"]',
    nivel_acceso   = 'medio',
    descripcion    = 'Gestión de restaurante - acceso equivalente a cajero'
WHERE id = 'admin_restaurante';
