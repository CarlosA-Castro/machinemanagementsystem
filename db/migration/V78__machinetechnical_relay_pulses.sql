-- V78: pulsos que envia el relé de TURNO por cada jugada, configurable por máquina.
--  - relay_pulses_per_turn: la mayoría de máquinas acreditan la partida con 1 pulso,
--    pero algunas placas necesitan 2 (o más) para iniciar el juego. Antes estaba
--    hardcodeado a 1 pulso en el firmware.
--  - Es un eje INDEPENDIENTE de credits_machine: credits_machine son los turnos que se
--    descuentan en la contabilidad; los pulsos son cuántas veces cierra el relé para que
--    la máquina física arranque. Una jugada = N pulsos, descuente los turnos que descuente.
--  - Default 1 = comportamiento previo, así ninguna máquina en producción cambia.
-- Patrón condicional (information_schema + PREPARE) para sobrevivir el desync de EC2:
-- si la columna ya existe, no falla.

SET @col_pulses = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'machinetechnical'
    AND COLUMN_NAME  = 'relay_pulses_per_turn'
);

SET @ddl_pulses = IF(@col_pulses = 0,
  'ALTER TABLE machinetechnical ADD COLUMN relay_pulses_per_turn INT NOT NULL DEFAULT 1',
  'SELECT 1'
);

PREPARE stp FROM @ddl_pulses;
EXECUTE stp;
DEALLOCATE PREPARE stp;
