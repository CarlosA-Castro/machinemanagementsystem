# Inversiones Arcade — Sistema de Gestión de Máquinas

Sistema web + firmware ESP32 para la gestión de máquinas arcade, simuladores y peluchers. Administra créditos QR, liquidaciones, reportes de fallas en tiempo real y control remoto de hardware.

---

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Backend | Python · Flask · MySQL |
| Frontend | HTML · Tailwind CSS · Alpine.js |
| Hardware | ESP32 · TFT ILI9341 · GM67 (lector QR) · Relés SRD-05VDC-SL-C |
| Infraestructura | Docker Compose · AWS EC2 · Flyway (migraciones) · Nginx |
| Dominio | inversionesarcade.com (HTTPS) |

---

## Módulos del sistema web

### 🏠 Panel principal (`/`)
Dashboard de administración con acceso a todos los módulos. Roles: `admin`, `cajero`, `admin_restaurante`.

---

### 🎮 Gestión de Máquinas (`/admin/maquinas`)
**Archivo:** `templates/admin/maquinas/gestionmaquinas.html`

- Lista todas las máquinas con estado en tiempo real (activa / mantenimiento / inactiva)
- **Indicadores ESP32 en vivo:** dos círculos de color por máquina (WiFi · Servidor), actualizados cada 20 s via heartbeat
- **Acciones por máquina:**
  - Ingresar turno manual (activa el relé vía comando remoto)
  - Reiniciar máquina remotamente
  - Resolver falla activa (cambia estado a activa, limpia reportes)
  - Ver historial completo de juegos y fallas
- **Máquinas multi-estación:** muestra cada estación con sus fallas activas y botón "Resolver" individual
- **Historial Global** (panel inferior): juegos recientes con nombre del QR y turnos tras uso · fallas con nombre del QR y notas
- **Historial por máquina (modal):** combina juegos (`turnusage`), fallas físicas (`machinefailures`) y reportes de cajero (`errorreport`), ordenados por fecha descendente

---

### 📱 Reporte de Fallas — Cajero (`/local/machinereport`)
**Archivo:** `templates/machinereport.html`

- Vista para cajeros: lista de máquinas con estado y dos círculos ESP32
- Formulario de reporte de falla por máquina (tipo: mantenimiento)
- Para máquinas multi-estación: selector de estación afectada
- Al reportar: cambia estado de la máquina, encola comando MAINTENANCE al ESP32

---

### 🎫 Generación y Venta de QR (`/local`)
**Archivo:** `templates/local.html`

- **Generar venta de QR:** selecciona paquete de turnos, nombre del cliente, cantidad de códigos
- Genera PDF imprimible con QR, nombre del paquete e información del cliente
- **Envío por WhatsApp:** genera imagen del QR con borde blanco (quiet zone) para lectura correcta en GM67 · muestra confirmación con hora de venta antes de abrir WhatsApp
- **Reimprimir QR:** búsqueda por código o nombre del cliente

---

### 💰 Liquidaciones (`/admin/liquidaciones`)
- Liquidación por socio/propietario con porcentajes configurables
- Historial de liquidaciones con detalle de ventas

### 👥 Gestión de Socios (`/admin/socios`)
- CRUD de socios y propietarios de máquinas

### 📊 Estadísticas
- Ventas por período, máquina, cajero
- Fallas y tiempos de inactividad

---

## Hardware ESP32

**Archivo firmware:** `Circuito_maquinas/Circuito_maquinas.ino`

### Pines

| Componente | GPIO |
|---|---|
| TFT ILI9341 (SPI) | MOSI:23 · SCK:18 · CS:15 · DC:2 · RST:4 |
| Touch XPT2046 | CS:33 · IRQ:27 |
| GM67 UART | RX:16 · TX:17 |
| Relé estación 1 | GPIO 26 |
| Relé estación 2 | GPIO 27 |
| Backlight | GPIO 32 |

> **Importante:** Módulo SRD-05VDC-SL-C es **activo-LOW**: LOW = relé ON, HIGH = relé OFF.

### Estados TFT

| Estado | Descripción |
|---|---|
| `STATE_STANDBY` | Esperando escaneo de QR |
| `STATE_STATION_SELECT` | Selección de estación (multi-estación): dos paneles laterales con flechas |
| `STATE_PROCESSING_QR` | Verificando QR con backend |
| `STATE_QR_VALID` | QR válido, iniciando juego |
| `STATE_GAME_ACTIVE` | Juego en curso · botón rojo "!! REPORTAR FALLA !!" |
| `STATE_CONFIRM_FAILURE` | Confirmación de falla del usuario |
| `STATE_MAINTENANCE` | Fuera de servicio (3+ fallas sin resolver) |
| `STATE_RESETTING` | Reinicio de máquina en progreso |

### Comunicación backend ↔ ESP32

- **Heartbeat (cada 30 s):** `POST /api/esp32/heartbeat` → backend almacena estado WiFi/servidor por máquina
- **Verificar QR:** `POST /api/esp32/verificar-qr` → descuenta 1 turno, retorna info del QR
- **Reportar falla:** `POST /api/esp32/reportar-falla` → registra en `machinefailures`, devuelve turno, encola MAINTENANCE si ≥3 fallas
- **Comandos pendientes (cada 10 s):** `GET /api/esp32/check-commands` → ejecuta MAINTENANCE / RESUME / ACTIVATE_RELAY / RESET
- **Confirmar comando:** `POST /api/esp32/command-executed`

### Comandos remotos (`esp32_commands`)

| Comando | Efecto en TFT |
|---|---|
| `MAINTENANCE` | Pantalla "FUERA DE SERVICIO". Multi-estación: solo marca esa estación en rojo en el selector |
| `RESUME` | Vuelve a standby / selección de estación |
| `ACTIVATE_RELAY` | Pulsa relé (turno manual). Si `origen=admin_manual`: muestra "TURNO MANUAL ADMIN" |
| `RESET` | Pulsa relé de reset. Si `origen=admin_web`: muestra "REINICIO ADMIN WEB" |

---

## Migraciones Flyway

| Versión | Descripción |
|---|---|
| V1 | Schema inicial |
| V14 | Tabla `esp32_commands` |
| V22 | Pines de relé en `machinetechnical` |
| V23 | `station_index` en `turnusage` |
| V30 | Fix roles |
| V31 | Tablas de logs |
| V32 | `station_index`, `problem_type`, `resolved_at` en `machinefailures` y `errorreport` |
| V33–V34 | Comandos ESP32 adicionales |
| **V35** | `turns_remaining_after` en `turnusage` (histórico correcto de turnos por uso) |

---

## Deploy

```bash
# En el servidor EC2
cd ~/machinemanagementsystem
git pull
docker compose up --build -d web
# Flyway aplica las migraciones pendientes automáticamente al arrancar
```

### Variables de entorno requeridas (`.env`)
```
MYSQL_HOST=mysql
MYSQL_USER=...
MYSQL_PASSWORD=...
MYSQL_DATABASE=...
SECRET_KEY=...
```

---

## Estructura del repositorio

```
machinemanagementsystem/
├── db/
│   └── migration/              # Migraciones Flyway (V1 → V35)
├── maquinas-medellin-frontend/
│   ├── app.py                  # Flask — rutas, APIs, lógica de negocio
│   ├── templates/
│   │   ├── admin/
│   │   │   └── maquinas/
│   │   │       └── gestionmaquinas.html
│   │   ├── local.html          # Venta y generación de QR
│   │   └── machinereport.html  # Reporte de fallas (cajero)
│   └── static/
└── Circuito_maquinas/
    └── Circuito_maquinas.ino   # Firmware ESP32 (TFT + QR + relés)
```
