-- V50: Renombrar usuarios existentes al nuevo formato de código (AFGOMEZ).
-- Fórmula: iniciales de nombre(s) propio(s) + primer apellido, todo mayúsculas.
-- Los usuarios sin apellido en BD usan su primer nombre en mayúsculas.

UPDATE users SET name = 'CCASTRO'  WHERE name = 'Carlos Andrés Castro';
UPDATE users SET name = 'AFGOMEZ'  WHERE name = 'Andrés Felipe Gomez';
UPDATE users SET name = 'CAROLINA' WHERE name = 'Carolina';
UPDATE users SET name = 'CAMILA'   WHERE name = 'Camila';
UPDATE users SET name = 'PRUEBA'   WHERE name = 'Prueba';
UPDATE users SET name = 'JSOCIO'   WHERE name = 'Juan Socio';
