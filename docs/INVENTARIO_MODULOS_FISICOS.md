# Inventario de Modulos Fisicos - Inversiones Arcade

## 1. Objetivo

Este documento define que debe registrarse de cada modulo fisico para que el
ESP32 deje de ser una pieza anonima y pase a tratarse como un activo tecnico
con trazabilidad.

El inventario existe para saber, en cualquier momento:

- Que modulo esta instalado en que maquina.
- En que local se encuentra.
- Que firmware tiene.
- Quien lo instalo.
- Cuando fue reemplazado o movido.

---

## 2. Problema que resuelve

Sin inventario formal, aparecen estos riesgos:

- Se cambia un ESP32 y luego no se sabe cual quedo en cada maquina.
- No hay trazabilidad de firmware ni de instalacion.
- Soporte no puede reconstruir que paso ante una falla.
- El crecimiento a varios locales depende de memoria manual.

---

## 3. Unidad inventariable

Cada modulo fisico debe registrarse como una unidad independiente, aunque hoy
todavia no exista una entidad dedicada en base de datos.

La unidad minima a identificar es el conjunto:

- ESP32
- Lector GM67
- TFT
- Rele
- Cableado o encapsulado asociado

Si en la practica solo se puede identificar de forma confiable el ESP32, el
inventario puede arrancar por ahi y luego enriquecerse.

---

## 4. Datos minimos por modulo

Cada modulo debe tener, como minimo:

- ID interno del modulo.
- Serial o codigo interno.
- MAC address del ESP32.
- Version de firmware.
- Maquina actual asociada.
- Local actual.
- Fecha de instalacion.
- Tecnico instalador.
- Estado.
- Ultima conexion conocida.
- Notas.

---

## 5. Estados recomendados

El inventario debe manejar, al menos, estos estados:

- Disponible
- Instalado
- En mantenimiento
- Reemplazado
- Baja

Esto evita que un mismo modulo parezca libre e instalado al mismo tiempo.

---

## 6. Eventos que deben registrarse

Cada vez que pase uno de estos eventos, el inventario debe actualizarse:

1. Alta inicial del modulo.
2. Instalacion en una maquina.
3. Cambio de maquina.
4. Cambio de local.
5. Actualizacion de firmware.
6. Entrada a mantenimiento.
7. Reemplazo por otro modulo.
8. Baja definitiva.

---

## 7. Tareas operativas del inventario

### 7.1 Alta del modulo

Antes de instalarlo, registrar:

- Identificador interno.
- MAC.
- Firmware base.
- Estado inicial.

**Validacion:**
- El modulo ya existe en inventario antes de tocar una maquina real.

### 7.2 Vinculacion modulo-maquina

Al instalarlo en una maquina, registrar:

- Maquina asociada.
- Local asociado.
- Fecha de instalacion.
- Tecnico responsable.

**Validacion:**
- Queda una sola relacion activa entre modulo y maquina.
- No hay ambiguedad sobre donde esta instalado.

### 7.3 Seguimiento tecnico

Mientras el modulo esta en operacion, registrar cuando aplique:

- Firmware actualizado.
- Reconexion o ultima comunicacion.
- Incidencias tecnicas relevantes.
- Observaciones de soporte.

**Validacion:**
- El historial tecnico permite saber si una falla vino por hardware, firmware o contexto de instalacion.

### 7.4 Reemplazo o movimiento

Si un modulo se mueve o se reemplaza, registrar:

- Modulo saliente.
- Modulo entrante.
- Motivo del cambio.
- Fecha.
- Tecnico.

**Validacion:**
- Nunca quedan dos modulos "activos" para la misma maquina sin explicacion.
- El historial deja claro que estaba instalado antes y que quedo despues.

---

## 8. Formato minimo sugerido

| Campo | Obligatorio | Ejemplo |
|---|---|---|
| modulo_id | Si | MOD-ESP32-001 |
| serial | Opcional al inicio | IA-MOD-001 |
| mac_address | Si | AA:BB:CC:DD:EE:FF |
| firmware_version | Si | v2026.04.25 |
| machine_id_actual | Si cuando esta instalado | 14 |
| local_actual | Si cuando esta instalado | El Mekatiadero |
| fecha_instalacion | Si cuando esta instalado | 2026-04-25 |
| tecnico_instalador | Si cuando esta instalado | Andres |
| estado | Si | Instalado |
| ultima_conexion | Recomendado | 2026-04-25 15:42 |
| notas | Opcional | Cambio por falla de WiFi |

---

## 9. Criterio de calidad del inventario

El inventario esta suficientemente bien solo si permite responder sin adivinar:

- Que modulo tiene hoy cada maquina.
- Donde fue instalado.
- Con que firmware esta corriendo.
- Quien hizo la ultima instalacion o reemplazo.
- Si un modulo esta activo, libre o fuera de servicio.

---

## 10. Relacion con el onboarding de nuevo local

El inventario de modulos es complemento directo del onboarding documentado en
`docs/ONBOARDING_NUEVO_LOCAL.md`.

El onboarding dice que hay que preparar y asociar el modulo.
Este documento define que datos minimos deben quedar registrados al hacerlo.

---

## 11. Siguiente paso practico

Antes de automatizar esto en base de datos o UI, el siguiente paso recomendado
es definir un formato manual unico para registrar modulos nuevos, instalaciones
y reemplazos con la misma estructura en todas las sedes.
