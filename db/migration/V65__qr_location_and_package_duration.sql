-- V65: alcance de QR por local + vencimiento configurable de paquetes.
--  - qrcode.location_id: ancla el QR al local donde se vendio. registrar-uso
--    rechaza el QR si la maquina es de otro local. NULL = QR legacy, se permite
--    en cualquier local (no rompe lo ya vendido).
--  - turnpackage.duration_days: dias de validez de un paquete normal. Default 30
--    (1 mes). La generacion de QR calcula expiration_date = hoy + duration_days.
--  - campaign.qr_duration_days: dias de validez del QR comprado en campana.
--    NULL = usa la duracion del paquete. Permite vencer paquetes de campana
--    para controlar el dinero sobrante en liquidaciones.
-- Patron condicional (information_schema + PREPARE) para sobrevivir el desync de
-- EC2: si la columna ya existe, no falla. NUNCA usar ; dentro de un string.

SET @col_qr_loc = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'qrcode'
    AND COLUMN_NAME  = 'location_id'
);

SET @ddl_qr_loc = IF(@col_qr_loc = 0,
  'ALTER TABLE qrcode ADD COLUMN location_id INT NULL',
  'SELECT 1'
);

PREPARE sql_qr_loc FROM @ddl_qr_loc;
EXECUTE sql_qr_loc;
DEALLOCATE PREPARE sql_qr_loc;

SET @idx_qr_loc = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'qrcode'
    AND INDEX_NAME   = 'idx_qrcode_location'
);

SET @ddl_idx_qr_loc = IF(@idx_qr_loc = 0,
  'CREATE INDEX idx_qrcode_location ON qrcode (location_id)',
  'SELECT 1'
);

PREPARE sql_idx_qr_loc FROM @ddl_idx_qr_loc;
EXECUTE sql_idx_qr_loc;
DEALLOCATE PREPARE sql_idx_qr_loc;

SET @col_pkg_dur = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'turnpackage'
    AND COLUMN_NAME  = 'duration_days'
);

SET @ddl_pkg_dur = IF(@col_pkg_dur = 0,
  'ALTER TABLE turnpackage ADD COLUMN duration_days INT NOT NULL DEFAULT 30',
  'SELECT 1'
);

PREPARE sql_pkg_dur FROM @ddl_pkg_dur;
EXECUTE sql_pkg_dur;
DEALLOCATE PREPARE sql_pkg_dur;

SET @col_camp_dur = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'campaign'
    AND COLUMN_NAME  = 'qr_duration_days'
);

SET @ddl_camp_dur = IF(@col_camp_dur = 0,
  'ALTER TABLE campaign ADD COLUMN qr_duration_days INT NULL',
  'SELECT 1'
);

PREPARE sql_camp_dur FROM @ddl_camp_dur;
EXECUTE sql_camp_dur;
DEALLOCATE PREPARE sql_camp_dur;
