-- V39: Asignar paquetes a locales
-- Cada paquete pertenece a un local específico.
-- Los paquetes existentes son del Mekatiadero.

ALTER TABLE turnpackage
    ADD COLUMN location_id INT(11) DEFAULT NULL AFTER id,
    ADD CONSTRAINT fk_turnpackage_location
        FOREIGN KEY (location_id) REFERENCES location(id) ON DELETE SET NULL;

-- Backfill: asignar todos los paquetes existentes al Mekatiadero
UPDATE turnpackage tp
JOIN location l ON TRIM(LOWER(l.name)) LIKE '%mekatiadero%'
SET tp.location_id = l.id
WHERE tp.location_id IS NULL;
