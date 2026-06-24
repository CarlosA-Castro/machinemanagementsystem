-- V68: rellenar gastos_liquidacion.fecha en filas viejas que quedaron en NULL
-- (gastos creados antes de V67). Se usa DATE(createdAt) como fecha del gasto.
-- Condicional: solo si la columna createdAt existe en este entorno.
-- Patrón condicional. NUNCA usar ; dentro de un string.

SET @has_created = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'gastos_liquidacion' AND COLUMN_NAME = 'createdAt'
);

SET @sql_backfill = IF(@has_created = 1,
  'UPDATE gastos_liquidacion SET fecha = DATE(createdAt) WHERE fecha IS NULL AND createdAt IS NOT NULL',
  'SELECT 1'
);

PREPARE s_bf FROM @sql_backfill;
EXECUTE s_bf;
DEALLOCATE PREPARE s_bf;
