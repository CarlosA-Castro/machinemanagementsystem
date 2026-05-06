-- V51: token de autenticación por máquina para el API Gateway ESP32
--
-- Cada máquina tiene un token único de 32 chars (UUID sin guiones).
-- El ESP32 lo envía como header X-Machine-Token en cada request.
-- El backend valida el token antes de procesar cualquier llamada del firmware.

ALTER TABLE machine
    ADD COLUMN machine_token VARCHAR(64) NULL
        COMMENT 'Token de autenticación ESP32 — generado automáticamente, único por máquina';

-- Generar token para cada máquina existente
UPDATE machine
SET machine_token = REPLACE(UUID(), '-', '')
WHERE machine_token IS NULL;

-- Ahora que todas las filas tienen valor, agregar restricciones
ALTER TABLE machine
    MODIFY COLUMN machine_token VARCHAR(64) NOT NULL,
    ADD UNIQUE INDEX idx_machine_token (machine_token);
