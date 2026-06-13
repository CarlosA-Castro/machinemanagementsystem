-- V61: Agregar qrhistory.final_price y campaign_id si faltan (desfase EC2: flyway_schema_history
-- marca V56 como aplicada pero las columnas no existen).
-- V60 reportó "success" pero NO ejecutó el ALTER: su DDL iba dentro de un COMMENT que contenía
-- un ';', y el parser de Flyway parte los statements por ';' → rompió el SET @sql.
-- Aquí se usa el patrón probado de V36/V37: comillas simples, DATABASE() directo, SIN ';' en el DDL.

SET @fp = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'qrhistory'
    AND COLUMN_NAME  = 'final_price'
);
SET @ddl = IF(@fp = 0,
  'ALTER TABLE qrhistory ADD COLUMN final_price DECIMAL(12,2) NULL',
  'SELECT 1'
);
PREPARE st FROM @ddl;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @ci = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'qrhistory'
    AND COLUMN_NAME  = 'campaign_id'
);
SET @ddl2 = IF(@ci = 0,
  'ALTER TABLE qrhistory ADD COLUMN campaign_id INT NULL',
  'SELECT 1'
);
PREPARE st2 FROM @ddl2;
EXECUTE st2;
DEALLOCATE PREPARE st2;

SET @ix = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'qrhistory'
    AND INDEX_NAME   = 'idx_qrh_campaign'
);
SET @ddl3 = IF(@ix = 0,
  'ALTER TABLE qrhistory ADD INDEX idx_qrh_campaign (campaign_id)',
  'SELECT 1'
);
PREPARE st3 FROM @ddl3;
EXECUTE st3;
DEALLOCATE PREPARE st3;
