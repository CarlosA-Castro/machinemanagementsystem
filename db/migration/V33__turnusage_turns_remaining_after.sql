-- V33: Guardar turnos restantes en el momento de cada uso (histórico correcto)
-- Antes: se hacía JOIN con userturns.turns_remaining (valor actual → todos los registros muestran lo mismo)
-- Ahora: se guarda turns_remaining_after en cada fila de turnusage al momento del uso

SET @s = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='turnusage' AND COLUMN_NAME='turns_remaining_after') = 0,
  'ALTER TABLE turnusage ADD COLUMN turns_remaining_after INT DEFAULT NULL COMMENT ''Turnos restantes después de este uso (guardado al momento del uso)''',
  'SELECT 1'
);
PREPARE st FROM @s; EXECUTE st; DEALLOCATE PREPARE st;
