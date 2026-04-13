-- V37: Asegurar existencia de columna stations_in_maintenance en machine
-- (puede faltar si V32 no fue aplicada correctamente en producción)

SET @col_exists = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'machine'
    AND COLUMN_NAME  = 'stations_in_maintenance'
);

SET @ddl = IF(@col_exists = 0,
  'ALTER TABLE machine ADD COLUMN stations_in_maintenance JSON DEFAULT NULL',
  'SELECT 1'
);

PREPARE st FROM @ddl;
EXECUTE st;
DEALLOCATE PREPARE st;

-- También asegurar consecutive_failures por si acaso
SET @col2_exists = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'machine'
    AND COLUMN_NAME  = 'consecutive_failures'
);

SET @ddl2 = IF(@col2_exists = 0,
  'ALTER TABLE machine ADD COLUMN consecutive_failures JSON DEFAULT NULL',
  'SELECT 1'
);

PREPARE st2 FROM @ddl2;
EXECUTE st2;
DEALLOCATE PREPARE st2;
