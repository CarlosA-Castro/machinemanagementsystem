-- V36: Guardar turnos restantes en el momento de cada uso (histórico correcto)
-- Antes: JOIN con userturns.turns_remaining (valor actual, igual para todos los registros del mismo QR)
-- Ahora: columna por fila guardada al momento del uso

SET @col_exists = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'turnusage'
    AND COLUMN_NAME  = 'turns_remaining_after'
);

SET @ddl = IF(@col_exists = 0,
  'ALTER TABLE turnusage ADD COLUMN turns_remaining_after INT DEFAULT NULL',
  'SELECT 1'
);

PREPARE st FROM @ddl;
EXECUTE st;
DEALLOCATE PREPARE st;
