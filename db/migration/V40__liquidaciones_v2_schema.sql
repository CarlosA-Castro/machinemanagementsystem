-- V40: Esquema para liquidaciones V2
-- Añade porcentaje_admin configurable por máquina y tabla de cierres oficiales.

-- 1. Agregar porcentaje_admin a la tabla de porcentajes por máquina.
--    Representa el % que cobra Inversiones Arcade por administración.
--    La utilidad del inversionista = 100 - porcentaje_restaurante - porcentaje_admin
ALTER TABLE maquinaporcentajerestaurante
    ADD COLUMN porcentaje_admin DECIMAL(5,2) NOT NULL DEFAULT 25.00
        COMMENT 'Porcentaje de administración cobrado por Inversiones Arcade';

-- 2. Tabla de cierres oficiales de liquidación.
--    Cada fila representa un período de liquidación cerrado y confirmado.
CREATE TABLE cierre_liquidacion (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    local_id        INT(11)         DEFAULT NULL,
    fecha_inicio    DATE            NOT NULL,
    fecha_fin       DATE            NOT NULL,
    total_ingresos  DECIMAL(14,2)   NOT NULL DEFAULT 0,
    total_negocio   DECIMAL(14,2)   NOT NULL DEFAULT 0,
    total_admin     DECIMAL(14,2)   NOT NULL DEFAULT 0,
    total_utilidad  DECIMAL(14,2)   NOT NULL DEFAULT 0,
    pct_negocio     DECIMAL(5,2)    NOT NULL DEFAULT 35.00,
    pct_admin       DECIMAL(5,2)    NOT NULL DEFAULT 25.00,
    observaciones   TEXT            DEFAULT NULL,
    usuario_id      INT(11)         DEFAULT NULL,
    creado_el       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_cierre_location
        FOREIGN KEY (local_id) REFERENCES location(id) ON DELETE SET NULL,
    CONSTRAINT fk_cierre_usuario
        FOREIGN KEY (usuario_id) REFERENCES users(id) ON DELETE SET NULL,

    INDEX idx_cierre_local_fecha (local_id, fecha_fin),
    INDEX idx_cierre_fecha_fin   (fecha_fin)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Cierres oficiales de liquidación por período y local';
