-- V77: Eliminar qrcode.expiresAt — columna muerta y peligrosa.
--
-- El vencimiento real vive en qrcode.expiration_date (DATE), que es la que escribe la
-- creacion de QR (CURDATE + duration_days, con prioridad campana > paquete > 30) y la
-- unica que leen las validaciones: /api/esp32/registrar-uso y el cache del firmware.
--
-- expiresAt (DATETIME) no la lee ni la escribe NINGUN endpoint. Tenia 366 filas con datos
-- sembrados por el script del Excel, que escribio en la columna vieja: 342 QR quedaron con
-- expiresAt lleno y expiration_date NULL, o sea "eternos" a ojos del codigo. No hubo riesgo
-- porque esos QR no tienen ni una fila en userturns (son fichas historicas inertes), pero
-- dos columnas de vencimiento son una trampa: el proximo que lea el esquema puede elegir la
-- equivocada.
--
-- No se hace backfill a proposito: sus datos son del seed, no de ventas reales, y los QR
-- afectados no tienen turnos que proteger. Copiarlos solo ensuciaria la columna buena.
--
-- Condicional (patron V36) porque el dump de EC2 y el esquema local pueden diferir.

SET @col_exists = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'qrcode'
    AND COLUMN_NAME  = 'expiresAt'
);

SET @ddl = IF(@col_exists = 1,
  'ALTER TABLE qrcode DROP COLUMN expiresAt',
  'SELECT 1'
);

PREPARE st FROM @ddl;
EXECUTE st;
DEALLOCATE PREPARE st;
