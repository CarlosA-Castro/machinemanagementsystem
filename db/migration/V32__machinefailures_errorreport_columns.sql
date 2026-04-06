-- V32: Agregar columnas faltantes para tracking de fallas por estación
-- Idempotente: usa INFORMATION_SCHEMA para no fallar si la columna ya existe

-- machinefailures: resolved
SET @s = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='machinefailures' AND COLUMN_NAME='resolved') = 0,
  'ALTER TABLE machinefailures ADD COLUMN resolved TINYINT(1) NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

-- machinefailures: resolved_at
SET @s = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='machinefailures' AND COLUMN_NAME='resolved_at') = 0,
  'ALTER TABLE machinefailures ADD COLUMN resolved_at DATETIME DEFAULT NULL',
  'SELECT 1'
);
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

-- machinefailures: station_index
SET @s = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='machinefailures' AND COLUMN_NAME='station_index') = 0,
  'ALTER TABLE machinefailures ADD COLUMN station_index INT DEFAULT NULL',
  'SELECT 1'
);
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

-- errorreport: station_index
SET @s = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='errorreport' AND COLUMN_NAME='station_index') = 0,
  'ALTER TABLE errorreport ADD COLUMN station_index INT DEFAULT NULL',
  'SELECT 1'
);
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

-- errorreport: problem_type
SET @s = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='errorreport' AND COLUMN_NAME='problem_type') = 0,
  'ALTER TABLE errorreport ADD COLUMN problem_type VARCHAR(50) DEFAULT ''mantenimiento''',
  'SELECT 1'
);
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;

-- errorreport: resolved_at
SET @s = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='errorreport' AND COLUMN_NAME='resolved_at') = 0,
  'ALTER TABLE errorreport ADD COLUMN resolved_at DATETIME DEFAULT NULL',
  'SELECT 1'
);
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
