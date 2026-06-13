-- V59: play_id liga entre sí los N turnos de una misma jugada (credits_machine
-- inserta 1 fila por turno) y liga cada falla con su jugada. Elimina la heurística
-- por tiempo del historial (gestionmaquinas) y del guard de devolución.

ALTER TABLE turnusage
  ADD COLUMN play_id VARCHAR(36) DEFAULT NULL;

ALTER TABLE machinefailures
  ADD COLUMN play_id VARCHAR(36) DEFAULT NULL;

CREATE INDEX idx_turnusage_play_id       ON turnusage (play_id);
CREATE INDEX idx_machinefailures_play_id ON machinefailures (play_id);
