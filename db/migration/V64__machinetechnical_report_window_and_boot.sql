-- V64: dos tiempos configurables por máquina, conectados al firmware.
--  - failure_report_window_seconds: ventana (desde el inicio del juego) durante la cual
--    aparece el botón REPORTAR FALLA en el TFT. Corta a propósito: suficiente para
--    demostrar una falla real sin regalar turnos. Default 15s (= comportamiento previo).
--  - boot_time_seconds: lapso de encendido tras arrancar el ESP32 en el que la máquina
--    muestra "Encendiendo..." y NO escanea QR hasta que realmente esté lista. Default 30s.
-- Patrón condicional (information_schema + PREPARE) para sobrevivir el desync de EC2:
-- si la columna ya existe, no falla.

SET @col_window = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'machinetechnical'
    AND COLUMN_NAME  = 'failure_report_window_seconds'
);

SET @ddl_window = IF(@col_window = 0,
  'ALTER TABLE machinetechnical ADD COLUMN failure_report_window_seconds INT NOT NULL DEFAULT 15',
  'SELECT 1'
);

PREPARE stw FROM @ddl_window;
EXECUTE stw;
DEALLOCATE PREPARE stw;

SET @col_boot = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'machinetechnical'
    AND COLUMN_NAME  = 'boot_time_seconds'
);

SET @ddl_boot = IF(@col_boot = 0,
  'ALTER TABLE machinetechnical ADD COLUMN boot_time_seconds INT NOT NULL DEFAULT 30',
  'SELECT 1'
);

PREPARE stb FROM @ddl_boot;
EXECUTE stb;
DEALLOCATE PREPARE stb;
