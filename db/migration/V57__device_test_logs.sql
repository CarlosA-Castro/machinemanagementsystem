CREATE TABLE IF NOT EXISTS device_test_log (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    device_id  VARCHAR(30)  NOT NULL,          -- MAC o nombre corto del módulo
    event      VARCHAR(40)  NOT NULL,          -- BOOT, HEARTBEAT, QR_SCAN, WIFI_RETRY, ERROR, etc.
    message    TEXT         NULL,              -- detalle libre
    free_heap  INT          NULL,              -- bytes libres de RAM
    wifi_rssi  SMALLINT     NULL,              -- señal WiFi en dBm
    uptime_s   INT          NULL,              -- segundos desde encendido
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_device   (device_id),
    INDEX idx_event    (event),
    INDEX idx_created  (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
