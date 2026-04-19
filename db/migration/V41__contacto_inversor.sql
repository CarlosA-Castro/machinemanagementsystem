CREATE TABLE IF NOT EXISTS contacto_inversor (
    id            INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    nombre        VARCHAR(120)  NOT NULL,
    whatsapp      VARCHAR(30)   NOT NULL,
    email         VARCHAR(120)  NULL,
    maquinas_interes VARCHAR(20) NOT NULL DEFAULT '1',
    mensaje       TEXT          NULL,
    leido         TINYINT(1)    NOT NULL DEFAULT 0,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_leido (leido),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
