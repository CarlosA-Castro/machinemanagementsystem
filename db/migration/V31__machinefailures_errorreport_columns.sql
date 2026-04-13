-- V31: Agregar columnas faltantes para tracking de fallas por estación

-- machinefailures: columnas necesarias para resolver-falla endpoint
ALTER TABLE machinefailures
  ADD COLUMN resolved      TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN resolved_at   DATETIME             DEFAULT NULL,
  ADD COLUMN station_index INT                  DEFAULT NULL;

-- errorreport: columna station_index para reportes por estación
ALTER TABLE errorreport
  ADD COLUMN station_index INT DEFAULT NULL;
