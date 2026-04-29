-- V48: Historial de reparaciones técnicas reales por máquina
-- Tabla para que el técnico o encargado registre qué arregló, cuándo y cómo.
-- Se integra con el flujo de fallas (machinefailures + errorreport):
-- cuando resuelve_fallas = 1 en una reparación correctiva, los endpoints
-- marcan las fallas abiertas de esa máquina como resueltas.

CREATE TABLE machine_repair_log (
    id               INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
    machine_id       INT           NOT NULL,
    fecha_reparacion DATETIME      NOT NULL,
    tipo             ENUM('preventivo', 'correctivo') NOT NULL DEFAULT 'correctivo',
    tecnico          VARCHAR(150)  NOT NULL DEFAULT '',
    descripcion      TEXT,
    costo            DECIMAL(10,2) DEFAULT NULL,
    resuelve_fallas  TINYINT(1)    NOT NULL DEFAULT 0,
    created_by       VARCHAR(150)  NOT NULL DEFAULT '',
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_repair_machine
        FOREIGN KEY (machine_id) REFERENCES machine(id) ON DELETE CASCADE,

    INDEX idx_repair_machine (machine_id),
    INDEX idx_repair_fecha   (fecha_reparacion)
);
