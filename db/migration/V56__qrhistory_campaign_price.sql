-- V56: Precio real de venta y campaña aplicada en qrhistory
-- Permite que liquidaciones usen el precio efectivo cobrado
-- en lugar del precio base del paquete.
-- NULL = sin campaña = usar tp.price (retrocompatible).

ALTER TABLE qrhistory
    ADD COLUMN final_price  DECIMAL(12,2) NULL    COMMENT 'Precio real cobrado; NULL = precio base del paquete',
    ADD COLUMN campaign_id  INT           NULL     COMMENT 'FK a campaign si se aplicó una campaña',
    ADD INDEX  idx_qrh_campaign (campaign_id);
