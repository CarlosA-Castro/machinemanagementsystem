# Onboarding de Nuevo Local - Inversiones Arcade

## 1. Objetivo

Este documento define el proceso para abrir un nuevo local de forma repetible,
controlada y auditable, sin depender de memoria, improvisacion o coordinacion
por WhatsApp.

El onboarding termina solo cuando el local queda listo para vender, jugar,
reportar y cerrar de forma separada del resto de la operacion.

---

## 2. Que incluye

El onboarding de un nuevo local cubre cuatro frentes:

1. Alta administrativa del local en el sistema.
2. Configuracion comercial y financiera de la operacion.
3. Preparacion tecnica de maquina y modulo fisico.
4. Validacion operativa antes de activar ventas reales.

No incluye todavia automatizacion avanzada de firmware, captive portal ni
autoprovisionamiento de dispositivos.

---

## 3. Resultado esperado

Al finalizar este proceso, el nuevo local debe tener:

- Local creado y usable en el sistema.
- Usuarios correctos para operar el local.
- Paquetes y precios configurados.
- Maquina registrada con su local asignado.
- Porcentajes comerciales configurados.
- Propietarios o inversionistas asociados si aplica.
- Modulo ESP32 preparado y vinculado a la maquina.
- Prueba QR exitosa.
- Prueba de falla o contingencia validada.
- Cierre de prueba generado sin mezclar informacion con otros locales.

---

## 4. Checklist General

| # | Tarea | Estado | Responsable | Notas |
|---|---|---|---|---|
| 1 | Crear local | ⏳ | | |
| 2 | Crear usuarios del local | ⏳ | | |
| 3 | Crear paquetes del local | ⏳ | | |
| 4 | Registrar maquinas | ⏳ | | |
| 5 | Configurar porcentajes | ⏳ | | |
| 6 | Configurar propietarios/inversionistas | ⏳ | | |
| 7 | Preparar modulo fisico | ⏳ | | |
| 8 | Asociar modulo a maquina | ⏳ | | |
| 9 | Hacer prueba QR | ⏳ | | |
| 10 | Hacer prueba de falla | ⏳ | | |
| 11 | Hacer cierre de prueba | ⏳ | | |
| 12 | Activar operacion | ⏳ | | |

---

## 5. Fase 1 - Alta Administrativa

### 5.1 Crear local

Crear el local en el sistema con los datos minimos:

- Nombre comercial.
- Ciudad o sede.
- Responsable operativo.
- Estado inicial.
- Observaciones relevantes del acuerdo.

**Validacion:**
- El local aparece en el sistema.
- El local se puede seleccionar en el contexto administrativo.
- El local queda separado de los demas a nivel de reportes y configuracion.

### 5.2 Crear usuarios del local

Definir los usuarios que operaran esa sede:

- Admin local o encargado.
- Cajero o vendedor, si aplica.
- Soporte tecnico, si necesita acceso.

**Validacion:**
- Cada usuario puede iniciar sesion.
- Cada usuario ve solo lo que le corresponde.
- El contexto de local no permite trabajar sobre otra sede por error.

---

## 6. Fase 2 - Configuracion Comercial

### 6.1 Crear paquetes del local

Definir la oferta comercial del nuevo local:

- Paquetes disponibles.
- Cantidad de turnos por paquete.
- Precio de venta.
- Estado activo o inactivo.

**Validacion:**
- Los paquetes aparecen al vender.
- El local no hereda paquetes incorrectos de otra sede.
- Los precios coinciden con el acuerdo comercial real.

### 6.2 Configurar porcentajes

Por cada maquina del local, definir:

- Porcentaje del negocio.
- Porcentaje de administracion.
- Porcentaje de utilidad.

**Validacion:**
- La suma de porcentajes es correcta.
- La configuracion coincide con el acuerdo del restaurante.
- La maquina queda lista para liquidar sin ajustes manuales externos.

### 6.3 Configurar propietarios o inversionistas

Si la maquina tiene propietarios o inversionistas asignados, registrar:

- Quien participa.
- Cuanto porcentaje tiene.
- Desde que fecha aplica.

**Validacion:**
- La maquina queda asociada a sus participantes.
- Los porcentajes de propiedad son consistentes.
- La futura liquidacion puede calcular utilidad sin ambiguedades.

---

## 7. Fase 3 - Configuracion Tecnica

### 7.1 Registrar maquinas

Crear la maquina en el sistema con los datos minimos:

- Nombre de la maquina.
- Local asignado.
- Tipo comercial o categoria.
- Cantidad de estaciones si aplica.
- Estado operativo inicial.

**Validacion:**
- La maquina aparece en el panel de gestion.
- La maquina queda vinculada al local correcto.
- La maquina puede recibir configuracion tecnica y comandos.

### 7.2 Preparar modulo fisico

Preparar el ESP32 y perifericos del modulo:

- Firmware correcto cargado.
- Datos de WiFi configurados.
- Backend URL configurado.
- `MACHINE_ID` configurado.
- Token o identificacion del dispositivo, si aplica.
- Verificacion basica de TFT, lector QR y rele.

**Validacion:**
- El ESP32 enciende correctamente.
- El lector GM67 responde.
- La pantalla TFT muestra estado util.
- El rele se puede activar en prueba controlada.

### 7.3 Asociar modulo a maquina

Registrar internamente que ese modulo pertenece a esa maquina, idealmente con:

- Identificador del modulo.
- MAC del ESP32.
- Version de firmware.
- Fecha de instalacion.
- Tecnico instalador.

**Validacion:**
- No queda duda de que modulo corresponde a que maquina.
- El historial tecnico puede mantenerse desde la primera instalacion.

---

## 8. Fase 4 - Validacion Operativa

### 8.1 Prueba QR

Realizar una venta controlada y luego jugar en la maquina.

**Debe validar:**
- El paquete se vende en el local correcto.
- El QR queda con saldo correcto.
- La maquina acepta el QR.
- El turno se descuenta.
- El uso queda registrado en historial.

### 8.2 Prueba de falla

Simular una incidencia controlada:

- Falla de maquina o estacion.
- Devolucion de turno si corresponde.
- Registro de la incidencia.
- Confirmacion de alerta o trazabilidad.

**Debe validar:**
- El sistema registra la falla.
- El turno no se pierde injustamente.
- La operacion sabe que la maquina requiere atencion.

### 8.3 Cierre de prueba

Antes de activar el local, correr un cierre controlado del periodo de prueba.

**Debe validar:**
- El cierre toma solo las ventas de ese local.
- No mezcla informacion de otra sede.
- El PDF se genera correctamente.
- Los porcentajes se aplican segun configuracion.

---

## 9. Criterio de Activacion

El local puede pasar a operacion real solo si:

- El local y usuarios existen y funcionan.
- Los paquetes venden con precio correcto.
- La maquina responde al flujo QR.
- La trazabilidad de uso queda registrada.
- La prueba de falla deja evidencia util.
- El cierre de prueba cuadra por local.

Si uno de esos puntos falla, el local no debe activarse todavia.

---

## 10. Datos Minimos que Siempre Deben Quedar Registrados

### Local

- Nombre del local.
- Responsable.
- Ciudad o sede.
- Usuarios asociados.

### Maquina

- Nombre.
- Local.
- Tipo.
- Estado.
- Porcentajes de negocio.

### Modulo fisico

- MAC del ESP32.
- Version de firmware.
- Fecha de instalacion.
- Tecnico.
- Maquina asociada.

---

## 11. Riesgos que este onboarding debe evitar

- Abrir un local sin separar bien sus reportes.
- Instalar una maquina sin saber que ESP32 tiene.
- Configurar mal los porcentajes y romper liquidaciones.
- Vender paquetes con precios o turnos incorrectos.
- Dejar una maquina operando sin prueba QR real.
- Depender de Andres para recordar cada paso tecnico.
