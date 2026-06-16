-- V63: fusionar el estado de máquina 'inactiva' dentro de 'mantenimiento'.
-- Decisión de producto: las máquinas solo manejan dos estados operativos,
-- 'activa' y 'mantenimiento' (mantenimiento == fuera de servicio == lo que antes
-- era 'inactiva'). Esto colapsa las filas existentes; el código ya no escribe
-- 'inactiva'. Idempotente: si no hay filas 'inactiva' no cambia nada.
UPDATE machine SET status = 'mantenimiento' WHERE status = 'inactiva';
