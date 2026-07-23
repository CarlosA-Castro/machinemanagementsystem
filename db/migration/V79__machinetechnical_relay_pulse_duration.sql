-- V79: duración (ms) del pulso del relé de TURNO, configurable por máquina.
--  - relay_pulse_duration_ms: cuánto tiempo permanece cerrado el relé en CADA pulso,
--    sea la máquina de 1 pulso o multi-pulso (V78). Antes el ancho estaba hardcodeado en
--    el firmware (1500ms para 1 pulso, 500ms para multi-pulso).
--  - Default 250: un pulso de 250ms es de sobra para cualquier placa de créditos (registran
--    con 50-100ms). Aplica a todas las máquinas; el admin lo sube/baja si la placa lo pide.
--  - Rango práctico 50..3000 (el backend y el firmware clampean); fuera de eso o dejaría el
--    relé castañeteando o el tren multi-pulso se saldría de la pantalla "RELE ACTIVADO".
-- Patrón condicional (information_schema + PREPARE) para sobrevivir el desync de EC2:
-- si la columna ya existe, no falla.

SET @col_pulse_ms = (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME   = 'machinetechnical'
    AND COLUMN_NAME  = 'relay_pulse_duration_ms'
);

SET @ddl_pulse_ms = IF(@col_pulse_ms = 0,
  'ALTER TABLE machinetechnical ADD COLUMN relay_pulse_duration_ms INT NOT NULL DEFAULT 250',
  'SELECT 1'
);

PREPARE stpms FROM @ddl_pulse_ms;
EXECUTE stpms;
DEALLOCATE PREPARE stpms;
