-- V32: Tracking de fallas consecutivas por estación y estado en tiempo real

-- Agregar station_index a turnusage para saber qué estación jugó (reset contador correctamente)
ALTER TABLE turnusage
  ADD COLUMN station_index INT DEFAULT NULL;

-- Fallas consecutivas por estación almacenadas en machine
-- Formato JSON: {"0": 2, "1": 0}  (índice → conteo consecutivo)
-- Para máquinas simples: {"all": 2}
ALTER TABLE machine
  ADD COLUMN consecutive_failures JSON DEFAULT NULL,
  ADD COLUMN stations_in_maintenance JSON DEFAULT NULL;
