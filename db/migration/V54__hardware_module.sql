-- V54: tabla hardware_module
-- Registra cada módulo físico (ESP32 + TFT + GM67 + carcaza 3D) como entidad independiente.
-- El ESP32 actualiza firmware_version, total_heap, free_heap automáticamente vía heartbeat.

CREATE TABLE IF NOT EXISTS hardware_module (
    id               INT           NOT NULL AUTO_INCREMENT,
    module_code      VARCHAR(30)   NOT NULL UNIQUE COMMENT 'ID legible, ej: MOD-001',
    machine_id       INT           NULL      COMMENT 'FK a machine; NULL = módulo sin asignar',
    status           VARCHAR(20)   NOT NULL  DEFAULT 'activo'
                        COMMENT 'activo | inactivo | mantenimiento | falla',
    firmware_version VARCHAR(30)   NULL      COMMENT 'Reportado por ESP32',
    total_heap       INT           NULL      COMMENT 'Bytes totales RAM reportados por ESP32',
    free_heap        INT           NULL      COMMENT 'Bytes RAM libre reportados por ESP32',
    components       JSON          NULL      COMMENT 'Lista de componentes: [{name, model, ok}]',
    notes            TEXT          NULL,
    last_seen        DATETIME      NULL      COMMENT 'Último heartbeat recibido del ESP32',
    created_at       DATETIME      NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME      NOT NULL  DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    CONSTRAINT fk_hm_machine FOREIGN KEY (machine_id)
        REFERENCES machine(id) ON DELETE SET NULL ON UPDATE CASCADE,
    INDEX idx_hm_machine  (machine_id),
    INDEX idx_hm_status   (status),
    INDEX idx_hm_last_seen (last_seen)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
