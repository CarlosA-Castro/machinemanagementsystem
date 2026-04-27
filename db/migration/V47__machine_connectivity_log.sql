-- V42: Registro de eventos de conectividad de máquinas (ONLINE / OFFLINE)
-- Permite el informe de horarios de activación/inactivación sin depender de emails.
-- Depuración bimestral (60 días) ejecutada automáticamente por el backend.

CREATE TABLE IF NOT EXISTS machine_connectivity_log (
    id          INT          NOT NULL AUTO_INCREMENT,
    machine_id  INT          NOT NULL,
    machine_name VARCHAR(255) NOT NULL DEFAULT '',
    location_id INT          NULL,
    event_type  ENUM('online','offline') NOT NULL,
    event_at    DATETIME     NOT NULL,
    PRIMARY KEY (id),
    INDEX idx_machine_event (machine_id, event_at),
    INDEX idx_event_at      (event_at),
    INDEX idx_location      (location_id, event_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
