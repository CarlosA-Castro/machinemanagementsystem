# Sistema de Gestión de Máquinas — Inversiones Arcade

Plataforma completa para gestionar máquinas arcade en locales comerciales. Digitaliza el control de acceso mediante QR, registra fallas, gestiona socios/propietarios, y se integra con hardware ESP32 físico en cada máquina.

---

## Stack tecnológico

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.13 / Flask |
| Base de datos | MySQL 8 (migraciones con Flyway) |
| Frontend admin | HTML + Tailwind CSS + Alpine.js |
| Frontend cajero | HTML + Tailwind CSS (local.html) |
| Infraestructura | Docker Compose + AWS EC2 |
| Hardware máquinas | ESP32 + TFT ILI9341 + GM67 (lector QR) |

---

## Estructura del proyecto

```
machinemanagementsystem/
├── maquinas-medellin-frontend/
│   ├── app.py                        # Flask app (API + rutas)
│   └── templates/
│       ├── local.html                # Interfaz del cajero (genera/vende QR)
│       ├── machinereport.html        # Reporte de fallas desde web
│       └── admin/
│           ├── index.html            # Dashboard admin
│           ├── maquinas/
│           │   ├── gestionmaquinas.html   # CRUD máquinas + estado ESP32
│           │   └── machinereport.html    # Reporte fallas admin
│           ├── usuarios/gestionusuarios.html
│           ├── paquetes/gestionpaquetes.html
│           ├── locales/gestionlocales.html
│           ├── inversores/gestionsocios.html
│           ├── mensajes/gestionmensajes.html
│           └── logs/
│               ├── gestionlogs.html
│               └── consola-completa.html
├── db/migration/                     # Migraciones Flyway (V1..V35+)
├── docker-compose.yml
└── Circuito_maquinas/
    └── Circuito_maquinas.ino         # Firmware ESP32
```

---

## Flujo de operación

1. El cajero abre `local.html`, busca al cliente, selecciona paquete y genera un QR.
2. El QR lleva el código del cliente. Se puede descargar o compartir por WhatsApp.
3. El cliente escanea el QR en la máquina. El ESP32 llama a `/api/esp32/registrar-uso`.
4. El backend valida el QR, descuenta un turno, activa el relé (habilita la máquina).
5. Si hay falla, el ESP32 reporta a `/api/esp32/reportar-falla` y devuelve el turno.
6. Tras 3 fallas consecutivas en la misma estación, el backend pone la estación en mantenimiento y encola un comando `MAINTENANCE` para el ESP32.

---

## Máquinas multi-estación

Las máquinas pueden ser `simple` (1 estación) o `multi_station` (hasta 4 estaciones).
- Cada estación tiene su propio relé y nombre.
- El carrusel TFT muestra cada estación; las que están en mantenimiento se muestran en rojo.
- Solo se manda a mantenimiento global (`machine.status = 'mantenimiento'`) cuando **todas** las estaciones están bloqueadas.

---

## Estado en tiempo real (ESP32)

Cada ESP32 envía un heartbeat a `/api/esp32/heartbeat` cada 30 segundos con:
- `wifi_connected` — si tiene WiFi
- `server_online` — si el servidor respondió
- `rssi` — señal WiFi en dBm

En `gestionmaquinas.html` se muestran dos puntos de color por máquina: WiFi y servidor. Punto gris = sin heartbeat en los últimos 90 segundos.

---

## Deploy

```bash
# En el servidor EC2
cd ~/machinemanagementsystem
git pull
docker compose up --build -d web
```

Las migraciones Flyway corren automáticamente al iniciar el contenedor.

---

## Migraciones de base de datos

La próxima migración disponible es **V36**. Nunca reutilizar un número ya usado.

Migraciones recientes:
- V31 — columnas `resolved`, `resolved_at`, `station_index` en `machinefailures` y `errorreport`
- V32 — `station_index` en `turnusage`; `consecutive_failures` y `stations_in_maintenance` en `machine`

---

## Firmware ESP32

Archivo: `Circuito_maquinas/Circuito_maquinas.ino`

Configurar al inicio del archivo:
```cpp
const char* WIFI_SSID     = "...";
const char* WIFI_PASSWORD = "...";
const char* BACKEND_URL   = "https://inversionesarcade.com";
const int   MACHINE_ID    = 13;   // ID de esta máquina en la BD
```

Verificar que el firmware tiene los marcadores `browseStation` y `stationInMaintenance` — si no están, fue reemplazado por una versión antigua de Arduino IDE.
