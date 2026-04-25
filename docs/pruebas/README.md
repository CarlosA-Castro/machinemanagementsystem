# Pruebas de implementación — Inversiones Arcade

Cada archivo documenta la batería de pruebas de una implementación mayor.
Este directorio es el tablero operativo para cerrar una fase o feature ya implementada.
Se corre una vez por implementación, antes de declarar la feature como producción.

| # | Implementación | Archivo | Estado |
|---|---------------|---------|--------|
| 01 | Liquidaciones y flujo base operacional | [01_liquidaciones_flujo_base.md](01_liquidaciones_flujo_base.md) | ⏳ En curso |
| 02 | Modo gracia ESP32 offline y reconexión | [02_modo_gracia_offline.md](02_modo_gracia_offline.md) | ⏳ Pendiente |

## Convención de estados
- ⏳ Pendiente / En curso
- ✅ Aprobada — feature en producción
- ❌ Bloqueada — bug encontrado, ver notas en el archivo

## Regla de uso
- Si una implementación ya existe en código pero no ha pasado su batería de pruebas, su estado oficial sigue siendo "En curso".
- Fase 1 hoy se considera funcionalmente terminada pero pendiente de validación semanal.
- Fase 2 ya inició con modo gracia offline y reconexión, pero no debe marcarse como lista hasta aprobar la prueba 02.
