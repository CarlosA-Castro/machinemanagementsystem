-- V45: Agrega fecha de expiración a qrcode (15 días desde generación)
ALTER TABLE qrcode ADD COLUMN expiration_date DATE NULL;

-- Retroactivamente asigna expiración a QRs existentes basada en createdAt
UPDATE qrcode SET expiration_date = DATE_ADD(DATE(createdAt), INTERVAL 15 DAY) WHERE expiration_date IS NULL;
