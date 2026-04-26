-- V46: Tabla de versiones de firmware OTA para ESP32

CREATE TABLE firmware (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    version       INT           NOT NULL COMMENT 'Fecha como entero, ej: 20260426',
    filename      VARCHAR(255)  NOT NULL,
    notes         TEXT          DEFAULT NULL,
    uploaded_at   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    uploaded_by   INT(11)       DEFAULT NULL,
    is_active     TINYINT(1)    NOT NULL DEFAULT 0,
    file_size     INT           DEFAULT NULL COMMENT 'Tamaño en bytes',

    CONSTRAINT fk_firmware_user
        FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Solo puede haber una versión activa a la vez (lo maneja la lógica de app,
-- pero el índice filtra rápido la consulta del ESP32)
CREATE INDEX idx_firmware_active ON firmware (is_active);
