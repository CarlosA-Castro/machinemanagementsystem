-- V38: Agregar location_id a users para normalizar relación con local
-- Mantiene columna `local` (varchar) para compatibilidad temporal

-- 1. Agregar FK location_id a users
ALTER TABLE users
    ADD COLUMN location_id INT(11) DEFAULT NULL AFTER `local`,
    ADD CONSTRAINT fk_users_location
        FOREIGN KEY (location_id) REFERENCES location(id) ON DELETE SET NULL;

-- 2. Backfill: mapear users.local -> location.id por nombre (case-insensitive)
UPDATE users u
JOIN location l ON TRIM(LOWER(u.local)) = TRIM(LOWER(l.name))
SET u.location_id = l.id
WHERE u.location_id IS NULL;
