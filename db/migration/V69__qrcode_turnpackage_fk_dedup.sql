-- V69: Resolver las FK duplicadas y contradictorias sobre qrcode.turnPackageId.
-- El esquema arrastraba DOS foreign keys en la misma columna:
--   - fk_qrcode_turnpackage  ON DELETE SET NULL  (el QR sobrevive, pierde el vínculo)
--   - qrcode_ibfk_1          ON DELETE CASCADE    (el QR se borra)
-- Con la CASCADE activa, borrar un turnpackage en uso arrastraba en cadena los QRs y,
-- a través de ellos, turnusage (ventas/usos), userturns y machinefailures: pérdida de
-- historial que alimenta liquidaciones y reportes.
-- Esta migración deja UNA sola FK con ON DELETE SET NULL, de modo que el borrado
-- forzado de un paquete preserve los QRs y todo su historial (turnPackageId queda NULL).
-- Idempotente: solo actúa sobre lo que exista; no-op si el esquema ya está limpio.

SET @db := DATABASE();

-- 1. Quitar la FK CASCADE si existe.
SET @fk := (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = @db AND TABLE_NAME = 'qrcode'
              AND CONSTRAINT_NAME = 'qrcode_ibfk_1' AND CONSTRAINT_TYPE = 'FOREIGN KEY');
SET @sql := IF(@fk > 0,
    'ALTER TABLE qrcode DROP FOREIGN KEY qrcode_ibfk_1',
    'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 2. Garantizar la FK SET NULL. Si no existe (esquema raro), crearla.
SET @fk := (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
            WHERE CONSTRAINT_SCHEMA = @db AND TABLE_NAME = 'qrcode'
              AND CONSTRAINT_NAME = 'fk_qrcode_turnpackage' AND CONSTRAINT_TYPE = 'FOREIGN KEY');
SET @sql := IF(@fk = 0,
    'ALTER TABLE qrcode ADD CONSTRAINT fk_qrcode_turnpackage FOREIGN KEY (turnPackageId) REFERENCES turnpackage (id) ON DELETE SET NULL',
    'SELECT 1');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;
