-- V58: polaridad de inversión del panel TFT configurable por máquina.
-- Distintos lotes de paneles ILI9341 requieren invertDisplay(true) o (false):
-- con el valor equivocado el fondo se ve blanco (colores invertidos).
-- Antes estaba hardcodeado en el firmware; ahora cada máquina trae el suyo.
-- Default 1 (true) = comportamiento actual y mayoría de paneles; los paneles
-- que se ven blancos se ponen en 0.
ALTER TABLE machinetechnical
    ADD COLUMN invert_display TINYINT(1) NOT NULL DEFAULT 1;
