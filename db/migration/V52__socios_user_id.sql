-- V52: Vincular socios con usuarios del sistema.
-- Agrega user_id a la tabla socios para que el portal del inversor
-- pueda resolver el socio autenticado via FK confiable en lugar del
-- fallback por nombre (que falla desde V50 con códigos cortos).

ALTER TABLE socios
    ADD COLUMN IF NOT EXISTS user_id INT DEFAULT NULL
        COMMENT 'FK al usuario del sistema (role=socio) vinculado a este socio';

ALTER TABLE socios
    ADD INDEX IF NOT EXISTS idx_socios_user_id (user_id);
