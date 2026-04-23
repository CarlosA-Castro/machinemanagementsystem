-- V44: Evita cierres duplicados por periodo y alcance (local o global)
-- Blindaje a nivel BD para que un mismo rango no pueda confirmarse dos veces.

ALTER TABLE cierre_liquidacion
    ADD INDEX idx_cierre_scope_periodo (local_id, fecha_inicio, fecha_fin);

DROP TRIGGER IF EXISTS trg_cierre_liquidacion_prevent_duplicate_insert;

DELIMITER $$

CREATE TRIGGER trg_cierre_liquidacion_prevent_duplicate_insert
BEFORE INSERT ON cierre_liquidacion
FOR EACH ROW
BEGIN
    IF EXISTS (
        SELECT 1
        FROM cierre_liquidacion cl
        WHERE cl.fecha_inicio = NEW.fecha_inicio
          AND cl.fecha_fin = NEW.fecha_fin
          AND IFNULL(cl.local_id, 0) = IFNULL(NEW.local_id, 0)
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Ya existe un cierre de liquidacion para este periodo y alcance';
    END IF;
END$$

DELIMITER ;
