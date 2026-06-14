-- V62: RBAC real (sección + acción) — alinear permisos de roles
--
-- Contexto: el enforcement pasa de "todo-o-nada" (solo admin_panel) a control por
-- sección (ver_*) y por acción (crear/editar/eliminar). Para que NADIE pierda
-- acceso en el deploy, a todo rol custom que hoy ya tiene admin_panel (= admin
-- completo de facto) se le otorga el set completo de permisos. Luego podrá
-- editarse desde la UI para restringirlo.
--
-- Roles base: admin hace bypass total en el backend; cajero/socio/admin_restaurante
-- no tienen admin_panel y no se tocan.
-- NOTA gotcha Flyway: ningún ';' dentro de strings, sin COMMENT.

-- 1) Garantizar default activo = TRUE en la tabla roles
ALTER TABLE roles ALTER COLUMN activo SET DEFAULT TRUE;

-- 2) Backfill de compatibilidad: roles custom con admin_panel → set completo
UPDATE roles
SET permisos = '["ver","crear","editar","eliminar","reportes","configurar","admin_panel","ver_dashboard","ver_usuarios","ver_maquinas","ver_paquetes","ver_locales","ver_liquidaciones","ver_logs","ver_mensajes","ver_socios"]'
WHERE id NOT IN ('admin', 'cajero', 'socio', 'admin_restaurante')
  AND permisos LIKE '%admin_panel%';

-- 3) Rol admin: reflejar todos los permisos en BD (solo para que la UI los muestre
--    marcados; el backend ya le da acceso total por bypass)
UPDATE roles
SET permisos = '["ver","crear","editar","eliminar","reportes","configurar","admin_panel","ver_dashboard","ver_usuarios","ver_maquinas","ver_paquetes","ver_locales","ver_liquidaciones","ver_logs","ver_mensajes","ver_socios"]'
WHERE id = 'admin';
