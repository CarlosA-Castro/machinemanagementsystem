-- V44: Refuerza consultas de cierre por periodo y deja la migracion segura para reintentos.
-- El bloqueo funcional de cierres duplicados ya ocurre en la aplicacion.
-- Aqui solo dejamos soporte de indice y limpieza de restos de intentos previos.

SET @idx_exists := (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'cierre_liquidacion'
      AND index_name = 'idx_cierre_scope_periodo'
);

SET @idx_sql := IF(
    @idx_exists = 0,
    'ALTER TABLE cierre_liquidacion ADD INDEX idx_cierre_scope_periodo (local_id, fecha_inicio, fecha_fin)',
    'SELECT 1'
);

PREPARE stmt_idx FROM @idx_sql;
EXECUTE stmt_idx;
DEALLOCATE PREPARE stmt_idx;

DROP TRIGGER IF EXISTS trg_cierre_liquidacion_prevent_duplicate_insert;
