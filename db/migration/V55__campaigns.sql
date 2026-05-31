-- V55: Sistema de Campañas dinámicas para paquetes
-- Permite descuentos, turnos gratis, bonus de turnos, etc.
-- con horarios únicos, recurrentes (días/horas) o flash.

-- ── Campaña ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign (
    id              INT           NOT NULL AUTO_INCREMENT,
    name            VARCHAR(100)  NOT NULL            COMMENT 'ej: Happy Hour Jueves',
    description     TEXT          NULL,
    location_id     INT           NULL                COMMENT 'NULL = todos los locales',
    schedule_type   VARCHAR(20)   NOT NULL DEFAULT 'once'
                        COMMENT 'once | recurring | flash',
    -- once: date_from/date_to definen el único período
    -- recurring: schedule_config define días y horas; date_from/date_to el rango de validez
    -- flash: ends_at = NOW() + duration; se crea y activa en el momento
    schedule_config JSON          NULL
                        COMMENT '{"days":[1,4],"time_from":"15:00","time_to":"18:00"}  (days: 0=dom…6=sáb)',
    date_from       DATE          NULL                COMMENT 'Inicio de vigencia (inclusive)',
    date_to         DATE          NULL                COMMENT 'Fin de vigencia (inclusive); NULL = sin límite',
    time_from       TIME          NULL                COMMENT 'Hora de inicio diaria (para once/recurring)',
    time_to         TIME          NULL                COMMENT 'Hora de fin diaria',
    priority        TINYINT       NOT NULL DEFAULT 0  COMMENT 'Mayor número = mayor prioridad en conflictos',
    is_active       TINYINT(1)    NOT NULL DEFAULT 1,
    created_by      VARCHAR(60)   NULL,
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    CONSTRAINT fk_camp_location FOREIGN KEY (location_id)
        REFERENCES location(id) ON DELETE SET NULL ON UPDATE CASCADE,
    INDEX idx_camp_active   (is_active),
    INDEX idx_camp_location (location_id),
    INDEX idx_camp_dates    (date_from, date_to)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ── Regla de campaña ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_rule (
    id           INT          NOT NULL AUTO_INCREMENT,
    campaign_id  INT          NOT NULL,
    -- A qué paquetes aplica
    applies_to   VARCHAR(20)  NOT NULL DEFAULT 'all_packages'
                     COMMENT 'all_packages | specific_packages',
    package_ids  JSON         NULL     COMMENT '[1,3,7] si applies_to = specific_packages',
    -- Tipo de beneficio
    rule_type    VARCHAR(30)  NOT NULL
                     COMMENT 'free | discount_pct | discount_fixed | fixed_price | bonus_turns | buy_x_get_y',
    rule_value   JSON         NOT NULL
                     COMMENT 'free:{} | discount_pct:{"pct":20} | discount_fixed:{"amount":2000}
                              | fixed_price:{"price":5000} | bonus_turns:{"bonus":3}
                              | buy_x_get_y:{"buy":5,"get":8}',

    PRIMARY KEY (id),
    CONSTRAINT fk_rule_campaign FOREIGN KEY (campaign_id)
        REFERENCES campaign(id) ON DELETE CASCADE ON UPDATE CASCADE,
    INDEX idx_rule_campaign (campaign_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ── Analítica de redención ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_redemption (
    id              INT         NOT NULL AUTO_INCREMENT,
    campaign_id     INT         NOT NULL,
    campaign_rule_id INT        NOT NULL,
    package_id      INT         NULL,
    qr_code         VARCHAR(20) NULL,
    user_id         INT         NULL,
    location_id     INT         NULL,
    original_turns  INT         NOT NULL,
    final_turns     INT         NOT NULL,
    original_price  DECIMAL(12,2) NOT NULL,
    final_price     DECIMAL(12,2) NOT NULL,
    savings         DECIMAL(12,2) GENERATED ALWAYS AS (original_price - final_price) STORED,
    redeemed_at     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    CONSTRAINT fk_red_campaign FOREIGN KEY (campaign_id)
        REFERENCES campaign(id) ON DELETE CASCADE,
    INDEX idx_red_campaign  (campaign_id),
    INDEX idx_red_redeemed  (redeemed_at),
    INDEX idx_red_location  (location_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
