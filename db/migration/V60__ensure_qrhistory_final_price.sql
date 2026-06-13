-- V60: Garantizar columnas final_price y campaign_id en qrhistory.
-- V56 las definía, pero en EC2 no quedaron aplicadas (flyway_schema_history las
-- registra pero la columna no existe en la BD), rompiendo el dashboard de gráficas
-- y todas las queries de "precio real de venta" (qh.final_price) y campaña
-- (qh.campaign_id, usado además por /api/turnusage/recientes).
-- Idempotente: solo agrega lo que falte (no-op si ya existen, p. ej. en local).

SET @db := DATABASE();

-- final_price
SET @col := (SELECT COUNT(*) FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'qrhistory' AND COLUMN_NAME = 'final_price');
SET @sql := IF(@col = 0,
    "ALTER TABLE qrhistory ADD COLUMN final_price DECIMAL(12,2) NULL COMMENT 'Precio real cobrado; NULL = precio base del paquete'",
    'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- campaign_id
SET @col := (SELECT COUNT(*) FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'qrhistory' AND COLUMN_NAME = 'campaign_id');
SET @sql := IF(@col = 0,
    "ALTER TABLE qrhistory ADD COLUMN campaign_id INT NULL COMMENT 'FK a campaign si se aplicó una campaña'",
    'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- índice idx_qrh_campaign
SET @idx := (SELECT COUNT(*) FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'qrhistory' AND INDEX_NAME = 'idx_qrh_campaign');
SET @sql := IF(@idx = 0,
    "ALTER TABLE qrhistory ADD INDEX idx_qrh_campaign (campaign_id)",
    'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
