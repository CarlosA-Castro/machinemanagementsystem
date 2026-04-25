# Prueba 01 — Liquidaciones y flujo base operacional

**Implementación:** Fase 1 funcionalmente completa (ventas, cierres, PDFs, filtros por local)
**Duración:** 1 semana de operación real en El Mekatiadero
**Quién la corre:** Andrés + cajero del local
**Estado:** ⏳ En curso (97 % completada, pendiente solo validación operativa)

---

## Objetivo

Verificar que el sistema opera completamente sin cuadre manual externo durante
una semana: ventas reales, turnos en máquinas, cierres diarios y liquidaciones
cuadran solos sin necesidad de hojas de cálculo adicionales.

Esta prueba existe porque el desarrollo de Fase 1 ya quedó cerrado a nivel funcional.
Lo pendiente es demostrar estabilidad operativa real antes de declararla al 100 %.

---

## Prerequisitos

- Backend deployado en EC2 con última versión de `main`
- Al menos una máquina activa con ESP32 funcionando
- Cajero entrenado en el flujo de venta de paquetes
- Acceso al portal admin para revisar reportes

---

## Pruebas diarias (repetir cada día de la semana)

### D1 — Venta de paquetes por método de pago

**Cómo:** Vender al menos 3 paquetes en el día usando métodos distintos
(efectivo, transferencia o tarjeta).

**Qué verificar al final del día:**
- En el reporte de ventas del día, cada transacción aparece con su método correcto.
- El total por método de pago cuadra con lo recibido físicamente en caja.
- No hay ventas con método vacío o `sin_registrar` cuando sí se registró.

**Criterio de éxito:** Total físico de caja == total del reporte, desglosado por método.

---

### D2 — Uso de turnos en máquina

**Cómo:** Un cliente usa su QR en la máquina durante el día.

**Qué verificar:**
- En el historial del QR, cada juego aparece con timestamp correcto.
- El saldo del QR baja en 1 por cada juego.
- Si el QR llega a 0, la máquina lo rechaza.

**Criterio de éxito:** Saldo en sistema == turnos restantes reales del cliente.

---

### D3 — Cierre del día

**Cómo:** El cajero realiza el cierre del turno al final del día.

**Qué verificar:**
- El cierre se crea sin errores.
- No se puede crear un segundo cierre para el mismo período (duplicado bloqueado).
- El PDF de liquidación se genera y descarga correctamente.
- Los números del PDF cuadran con las ventas del día.

**Criterio de éxito:** Un solo cierre por día, PDF correcto, sin intervención manual.

---

### D4 — Devoluciones

**Cómo:** Procesar al menos una devolución durante la semana (falla de máquina
o solicitud de cliente).

**Qué verificar:**
- El turno devuelto aparece en el historial del QR.
- El saldo del QR sube en 1.
- La devolución aparece en el reporte de caja con su razón.

**Criterio de éxito:** Devolución registrada, saldo correcto, trazable en historial.

---

## Prueba de cierre semanal

**Al final de la semana:**

1. Abrir el portal de liquidaciones.
2. Revisar que hay un cierre por cada día operado (sin duplicados, sin huecos).
3. Generar el PDF de liquidación semanal.
4. Comparar el total del sistema contra el dinero físico recaudado.

**Criterio de éxito:** Diferencia <= $0 (o diferencia explicable por cortesías/ajustes registrados).

---

## Qué anotar si algo falla

Abrir un issue o anotarlo aquí con:
- Fecha y hora del evento
- Qué acción se hizo
- Qué mostró el sistema
- Qué era lo esperado

---

## Resultado final

| Día | Ventas cuadran | Cierre ok | PDF ok | Notas |
|-----|---------------|-----------|--------|-------|
| Lunes | | | | |
| Martes | | | | |
| Miércoles | | | | |
| Jueves | | | | |
| Viernes | | | | |
| Sábado | | | | |
| Domingo | | | | |

**Veredicto:** ⏳ Pendiente

Si todos los días están ✅ → Fase 1 pasa de 97 % a 100 % y queda cerrada.
