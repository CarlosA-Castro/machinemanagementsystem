# Prueba 02 — Modo gracia ESP32 offline y reconexión

**Implementación:** Inicio de Fase 2 con caché local de QRs + cola de sincronización (commit `5d69f1e`)
**Duración:** ~1 hora en una sola sesión
**Quién la corre:** Andrés con acceso físico a la máquina y al router
**Estado:** ⏳ Pendiente (implementado, falta validación física)

---

## Objetivo

Verificar que la máquina acepta QRs conocidos durante un corte de WiFi,
sincroniza las transacciones al reconectar, y rechaza correctamente los
casos que no debe permitir.

Esta prueba marca el arranque formal de Fase 2.
La implementación base ya existe en código; falta comprobarla sobre hardware real y red real.

---

## Prerequisitos

- Firmware nuevo flasheado (Circuito_maquinas.ino con modo gracia)
- Backend deployado con `/api/esp32/sync-offline`
- Monitor serial abierto (115200 baud) para ver logs del ESP32
- Al menos 2 QRs con turnos disponibles
- Acceso al router para cortar y restaurar WiFi

---

## Prueba 1 — Caché se llena correctamente (online)

**Pasos:**
1. Arrancar la máquina con WiFi conectado.
2. Verificar en serial: línea `[OK] [NTP] Tiempo: ...` presente.
3. Escanear 2 QRs distintos, que cada uno juegue al menos una vez.

**Qué ver en serial:**
```
[OK] [NTP] Tiempo: 2026-...
[INFO] [Cache] Guardado QR0001 | turns=4 | slot=0
[INFO] [Cache] Guardado QR0002 | turns=9 | slot=1
```

**Qué verificar en la web:** Turnos bajaron correctamente en el historial.

**Criterio de éxito:** Cada QR usado aparece en los logs de caché con saldo correcto.

---

## Prueba 2 — Juego offline con QR conocido

**Pasos:**
1. Escanear QR0001 online → confirmar que quedó en caché (ver serial).
2. Desconectar el WiFi del router.
3. Esperar 30 segundos (el ESP32 detecta la caída).
4. Escanear QR0001.

**Qué ver en serial:**
```
[WARN] [QR] Reintento 1/3...
[WARN] [QR] Reintento 2/3...
[WARN] [QR] Reintento 3/3...
[INFO] [QR] OFFLINE via cache: QR0001 | turnos locales restantes: 3
[INFO] [Offline] TX encolada [1/20]: QR0001 estacion 0 ts=174...
```

**Qué verificar:** La máquina deja jugar. Pantalla muestra "Ahora (offline)".

**Criterio de éxito:** Juego completo sin WiFi. Cola tiene 1 transacción.

---

## Prueba 3 — Sincronización automática al reconectar

**Pasos:** Inmediatamente después de la Prueba 2, reconectar el WiFi.

**Qué ver en serial (en los siguientes 30 segundos):**
```
[OK] [WiFi] Reconectado!
[OK] [NTP] Tiempo: ...
[OK] [Backend] Servidor online
[INFO] [Offline] Sincronizando 1 tx con backend...
[OK] [Offline] Sincronizacion ok — vaciando cola
```

**Qué verificar en la web:**
- El turno jugado offline aparece en el historial del QR.
- El timestamp es la hora del juego, no la hora de reconexión.
- El saldo del QR bajó 1.

**Criterio de éxito:** Transacción visible en web con hora correcta. Cola vacía.

---

## Prueba 4 — QR desconocido sin WiFi (cache miss)

**Pasos:**
1. Cortar WiFi.
2. Escanear un QR que **nunca** se usó en esta máquina.

**Qué ver en serial:**
```
[ERROR] [QR] Sin conexion y sin cache para QR9999
```

**Qué verificar:** Pantalla muestra "Sin conexión al servidor". No hay juego.

**Criterio de éxito:** Rechazado correctamente. Sin juego gratis.

---

## Prueba 5 — QR sin turnos, offline

**Pasos:**
1. Usar un QR hasta agotar todos sus turnos (online).
2. Cortar WiFi.
3. Escanear ese QR.

**Qué ver en serial:**
```
[WARN] [QR] Sin turnos (cache) QR0001
```

**Qué verificar:** Pantalla muestra "Sin turnos disponibles". No hay juego.

**Criterio de éxito:** Rechazado correctamente aunque no haya servidor.

---

## Prueba 6 — Cola sobrevive reinicio del ESP32

**Pasos:**
1. Jugar offline (Prueba 2) sin reconectar WiFi.
2. Cortar y restaurar la corriente del ESP32.
3. Reconectar WiFi.

**Qué ver en serial al arrancar:**
```
[INFO] [Offline] Cola cargada de NVS: 1 transacciones pendientes
[OK] [Offline] Sincronizacion ok — vaciando cola
```

**Qué verificar en la web:** La transacción offline sincronizó tras el reinicio.

**Criterio de éxito:** La transacción no se pierde con el reinicio del ESP32.

---

## Prueba 7 — Anomalía de doble gasto (auditoría)

**Pasos:**
1. Jugar offline con QR0001 (queda en cola, 1 turno pendiente).
2. Antes de reconectar, consumir ese turno desde otra máquina online
   (o desde el portal admin descontarlo manualmente).
3. Reconectar WiFi → la cola intenta sincronizar.

**Qué verificar en la web** (portal admin → logs):
```
tipo: offline_conflict
descripcion: Conflicto offline: QR QR0001 sin turnos al sincronizar
```

**Criterio de éxito:** El conflicto queda registrado. No se descuenta un turno fantasma. El saldo no queda negativo.

---

## Orden de ejecución

```
Prueba 1 → 2 → 3    (15 min — caso principal)
Prueba 4 → 5        (5 min — rechazos correctos)
Prueba 6            (10 min — persistencia NVS)
Prueba 7            (10 min — opcional, auditoría)
```

---

## Resultado

| # | Prueba | Resultado | Notas |
|---|--------|-----------|-------|
| 1 | Caché online | | |
| 2 | Juego offline QR conocido | | |
| 3 | Sync al reconectar | | |
| 4 | Cache miss (QR desconocido) | | |
| 5 | QR sin turnos offline | | |
| 6 | Cola sobrevive reinicio | | |
| 7 | Anomalía doble gasto | | |

**Veredicto:** ⏳ Pendiente

Si 1–6 están ✅ → modo gracia en producción.
Prueba 7 es de auditoría — no bloquea el lanzamiento.
