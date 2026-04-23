-- V43: Metodo de pago y auditoria basica para ventas QR
-- Permite cuadre de caja diario y futura edicion auditada del metodo de pago.

ALTER TABLE qrhistory
    ADD COLUMN payment_method VARCHAR(20) NULL
        COMMENT 'Metodo de pago de la venta real: efectivo, transferencia, tarjeta, mixto',
    ADD COLUMN payment_method_updated_at DATETIME NULL
        COMMENT 'Fecha/hora de la ultima actualizacion manual del metodo de pago',
    ADD COLUMN payment_method_updated_by INT(11) NULL
        COMMENT 'Usuario que actualizo por ultima vez el metodo de pago',
    ADD COLUMN payment_method_update_reason VARCHAR(255) NULL
        COMMENT 'Motivo de la ultima actualizacion del metodo de pago';

CREATE INDEX idx_qrhistory_payment_method_fecha
    ON qrhistory (payment_method, fecha_hora);
