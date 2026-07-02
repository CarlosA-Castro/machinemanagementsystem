-- V70: eliminar el sistema de token por máquina (ESP32)
--
-- El token (machine_token, agregado en V51) resultó innecesario para el
-- modelo de autenticación actual y bloqueaba la creación de máquinas nuevas
-- (columna NOT NULL sin default, el INSERT de /api/maquinas no la incluía).
-- Se quita la columna y su índice único.

ALTER TABLE machine
    DROP INDEX idx_machine_token,
    DROP COLUMN machine_token;
