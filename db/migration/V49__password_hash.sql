-- V49: Agregar columna password_hash para almacenamiento seguro de contraseñas.
-- Los usuarios existentes quedan con password_hash = NULL y se migran
-- automáticamente (lazy) la primera vez que hacen login con el nuevo sistema.

ALTER TABLE users
    ADD COLUMN password_hash VARCHAR(256) DEFAULT NULL;
