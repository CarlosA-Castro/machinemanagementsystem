-- V53: Cambiar socios.estado de ENUM a VARCHAR(30).
-- Motivación: el auto-registro desde la landing usa el estado 'pendiente_activacion'
-- que no estaba en el ENUM original, causando error 1265 en MySQL.
-- VARCHAR(30) acepta todos los estados actuales y futuros sin necesidad de
-- nuevas migraciones cada vez que se agregue un estado.
--
-- Estados actuales en uso:
--   activo | inactivo | pendiente_pago | suspendido | pendiente_activacion

ALTER TABLE socios
    MODIFY COLUMN estado VARCHAR(30) NOT NULL DEFAULT 'activo'
        COMMENT 'Estado del socio: activo, inactivo, pendiente_pago, suspendido, pendiente_activacion';
