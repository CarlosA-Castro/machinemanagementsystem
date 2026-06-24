-- V66: límites de período de liquidación con fecha+hora.
--  - inicio_dt / fin_dt DATETIME: marcan el instante exacto en que arranca y
--    termina un período. Permite cerrar varios períodos el mismo día (uno
--    termina a las 5:00pm, el siguiente arranca a las 5:00pm) y atribuir cada
--    venta al período correcto por hora, no solo por día.
--  - Backfill de cierres existentes: inicio_dt = fecha_inicio 00:00:00,
--    fin_dt = creado_el (el instante real en que se registró el cierre). Así el
--    próximo período arranca donde de verdad terminó el anterior.
-- Patrón condicional (information_schema + PREPARE). NUNCA usar ; dentro de un string.

SET @col_inicio_dt = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'cierre_liquidacion'
    AND COLUMN_NAME  = 'inicio_dt'
);

SET @ddl_inicio_dt = IF(@col_inicio_dt = 0,
  'ALTER TABLE cierre_liquidacion ADD COLUMN inicio_dt DATETIME NULL',
  'SELECT 1'
);

PREPARE s_inicio FROM @ddl_inicio_dt;
EXECUTE s_inicio;
DEALLOCATE PREPARE s_inicio;

SET @col_fin_dt = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'cierre_liquidacion'
    AND COLUMN_NAME  = 'fin_dt'
);

SET @ddl_fin_dt = IF(@col_fin_dt = 0,
  'ALTER TABLE cierre_liquidacion ADD COLUMN fin_dt DATETIME NULL',
  'SELECT 1'
);

PREPARE s_fin FROM @ddl_fin_dt;
EXECUTE s_fin;
DEALLOCATE PREPARE s_fin;

SET @idx_fin_dt = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'cierre_liquidacion'
    AND INDEX_NAME   = 'idx_cierre_fin_dt'
);

SET @ddl_idx_fin = IF(@idx_fin_dt = 0,
  'CREATE INDEX idx_cierre_fin_dt ON cierre_liquidacion (local_id, fin_dt)',
  'SELECT 1'
);

PREPARE s_idx FROM @ddl_idx_fin;
EXECUTE s_idx;
DEALLOCATE PREPARE s_idx;

-- Backfill idempotente: solo filas sin datetime aún.
UPDATE cierre_liquidacion
   SET inicio_dt = TIMESTAMP(fecha_inicio, '00:00:00')
 WHERE inicio_dt IS NULL;

UPDATE cierre_liquidacion
   SET fin_dt = creado_el
 WHERE fin_dt IS NULL;
