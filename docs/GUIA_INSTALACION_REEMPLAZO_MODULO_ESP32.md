# Guia Tecnica de Instalacion y Reemplazo de Modulo ESP32

## 1. Objetivo

Este documento define como instalar, validar y reemplazar el modulo fisico de
una maquina arcade sin improvisacion y sin perder trazabilidad tecnica.

La meta es que cualquier instalacion deje evidencia clara de:

- Que modulo se instalo.
- En que maquina y en que local quedo.
- Con que configuracion de firmware se desplego.
- Que pruebas pasaron antes de activar la operacion.

---

## 2. Alcance

Esta guia cubre:

1. Preparacion del modulo nuevo.
2. Instalacion del modulo en una maquina.
3. Validaciones tecnicas minimas antes de activar.
4. Reemplazo de un modulo existente.
5. Datos que deben quedar registrados en inventario.

No cubre aun captive portal ni provisionamiento automatico.
La configuracion sigue siendo manual para `MACHINE_ID`, WiFi, backend y datos
del dispositivo.

---

## 3. Componentes del modulo

El modulo fisico que hoy conecta la maquina con el backend esta compuesto por:

- ESP32
- TFT ILI9341
- Lector QR GM67
- Rele
- Cableado de alimentacion y senal

Si algun componente cambia, la instalacion debe tratarse como intervencion
tecnica y no como ajuste menor.

---

## 4. Configuracion de hardware confirmada

Antes de instalar o reemplazar un modulo, usar esta configuracion de pines
confirmada en memoria del proyecto.

```cpp
#define BACKLIGHT_PIN 32
#define TFT_RST_PIN   4
#define TFT_CS        5
#define TFT_DC        15
#define TFT_MOSI      23
#define TFT_SCLK      18
#define TFT_MISO      19
#define TOUCH_CS      33
#define TOUCH_IRQ     27
#define GM67_RX_PIN   16
#define GM67_TX_PIN   17
#define RELAY_PIN_1   26
#define RELAY_PIN_2   27
```

### Regla de oro

No cambiar pines en una instalacion de campo.
Si un cambio de hardware obliga a hacerlo, debe validarse primero contra la
memoria tecnica del proyecto y documentarse aparte.

### Ajuste adicional confirmado

- `SPI_FREQUENCY 10000000` en `User_Setup.h`

---

## 5. Prerequisitos antes de instalar

Antes de tocar una maquina real, tener listo:

- Modulo identificado en inventario o, si aun no existe, al menos su MAC y ID interno.
- Firmware correcto para la version actual del sistema.
- `MACHINE_ID` correcto de la maquina destino.
- Credenciales WiFi del local.
- URL del backend.
- Token o identificador del dispositivo, si aplica.
- Destornilladores, cableado y fuente estables.
- Acceso al panel admin para validar heartbeat, maquina y reportes.

Si falta uno de estos datos, no activar la instalacion todavia.

---

## 6. Procedimiento de instalacion

### 6.1 Preparar el modulo fuera de operacion

Antes de montarlo en la maquina:

1. Identificar el modulo con su ID interno.
2. Confirmar MAC del ESP32.
3. Cargar el firmware correcto.
4. Configurar manualmente:
   - WiFi SSID
   - WiFi password
   - Backend URL
   - `MACHINE_ID`
   - Token del dispositivo, si aplica
5. Verificar que el build o firmware corresponde a la maquina correcta.

**Objetivo:**
Que el modulo llegue a la maquina ya listo para conectarse y no se configure a ciegas en sitio.

### 6.2 Montaje fisico en la maquina

Con la maquina apagada:

1. Conectar alimentacion del ESP32.
2. Conectar TFT.
3. Conectar lector GM67.
4. Conectar rele correspondiente.
5. Revisar que no haya cables flojos ni inversiones de polaridad.
6. Asegurar el modulo en su posicion definitiva.

**Objetivo:**
Que la instalacion fisica quede estable y no dependa de movimiento manual o presion de cables.

### 6.3 Primer arranque

Encender la maquina y observar:

1. El ESP32 arranca correctamente.
2. La TFT muestra estado entendible.
3. El lector GM67 queda operativo.
4. El modulo intenta conectarse a WiFi.
5. El modulo reporta al backend si la red esta disponible.

**Objetivo:**
Detectar fallas electricas o de configuracion antes de declarar la maquina operativa.

---

## 7. Validaciones tecnicas minimas

La instalacion no se considera terminada si no pasan estas validaciones.

### 7.1 Validacion de encendido

- El ESP32 enciende sin reinicios anormales.
- La TFT responde.
- El backlight funciona.

### 7.2 Validacion de lector QR

- El GM67 lee un QR valido.
- El escaneo llega al firmware sin errores visibles.

### 7.3 Validacion de backend

- El modulo se conecta a WiFi.
- El backend responde.
- La maquina aparece online o con heartbeat reciente.

### 7.4 Validacion de juego

- La maquina acepta un QR valido.
- Se descuenta el turno.
- El rele activa el juego.
- El uso queda registrado en historial.

### 7.5 Validacion de falla

- Se puede reportar una falla o probar la contingencia definida.
- La incidencia deja trazabilidad util.

---

## 8. Criterio de instalacion exitosa

Una instalacion queda aprobada solo si:

- El modulo correcto quedo en la maquina correcta.
- La maquina se conecta al backend.
- La lectura QR funciona.
- El rele responde.
- Hay registro de uso real o de prueba.
- La vinculacion modulo-maquina quedo documentada.

Si una de esas condiciones falla, la instalacion queda incompleta.

---

## 9. Procedimiento de reemplazo

### 9.1 Antes de retirar el modulo actual

Registrar:

- Que modulo estaba instalado.
- En que maquina.
- En que local.
- Motivo del reemplazo.
- Fecha y tecnico responsable.

Si es posible, conservar evidencia minima del problema:

- Fotos
- Nota tecnica
- Version de firmware
- Estado de conectividad

### 9.2 Retiro del modulo saliente

1. Apagar la maquina.
2. Desconectar el modulo con cuidado.
3. Etiquetar el modulo saliente como:
   - En mantenimiento
   - Reemplazado
   - Baja

**Objetivo:**
Que el modulo viejo no quede "perdido" ni parezca disponible sin revision.

### 9.3 Instalacion del modulo entrante

Seguir el procedimiento completo de instalacion del capitulo 6.

No se debe asumir que por ser reemplazo se puede omitir:

- Configuracion
- Validacion
- Registro en inventario

### 9.4 Cierre del reemplazo

Al terminar, debe quedar claro:

- Cual modulo salio.
- Cual modulo entro.
- Quien hizo el cambio.
- Desde cuando aplica.
- Que pruebas pasaron despues del cambio.

---

## 10. Datos que deben registrarse despues de instalar o reemplazar

Cada intervencion debe dejar registrados, como minimo:

- ID interno del modulo.
- MAC address.
- Version de firmware.
- Maquina asociada.
- Local asociado.
- Fecha de instalacion.
- Tecnico responsable.
- Estado del modulo.
- Notas de instalacion o reemplazo.

Si fue un reemplazo, agregar:

- Motivo del cambio.
- Modulo saliente.
- Modulo entrante.

---

## 11. Errores que esta guia busca evitar

- Instalar un modulo sin saber su MAC o identidad.
- Cargar firmware con `MACHINE_ID` incorrecto.
- Dejar una maquina operativa sin prueba QR.
- Cambiar un ESP32 sin registrar el reemplazo.
- Perder trazabilidad de firmware o tecnico responsable.
- Cambiar pines en campo y romper una instalacion funcional.

---

## 12. Checklist rapido de campo

| Paso | Verificacion | Estado |
|---|---|---|
| 1 | Modulo identificado | ⏳ |
| 2 | MAC registrada | ⏳ |
| 3 | Firmware correcto cargado | ⏳ |
| 4 | WiFi configurado | ⏳ |
| 5 | Backend URL configurado | ⏳ |
| 6 | `MACHINE_ID` correcto | ⏳ |
| 7 | TFT operativa | ⏳ |
| 8 | GM67 operativo | ⏳ |
| 9 | Rele responde | ⏳ |
| 10 | Heartbeat visible | ⏳ |
| 11 | QR de prueba exitoso | ⏳ |
| 12 | Inventario actualizado | ⏳ |
