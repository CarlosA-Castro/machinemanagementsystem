# Inversiones Arcade — Sistema de Gestión de Máquinas

Sistema web completo para la gestión de máquinas arcade: ventas de turnos por QR, control de máquinas ESP32/TFT, liquidaciones a socios/propietarios y logs de actividad en tiempo real.

---

## Stack Tecnológico

| Capa | Tecnología |
|---|---|
| Backend | Python 3 + Flask |
| Base de datos | MySQL (Flyway para migraciones) |
| Frontend | HTML + Tailwind CSS + Chart.js + particles.js |
| Hardware | ESP32 + TFT ILI9341 + Lector QR GM67 + Relés SRD-05VDC |
| Deploy | Docker Compose + AWS EC2 |
| Dominio | inversionesarcade.com (HTTPS) |

---

## Ejecución local (desarrollo)

```powershell
# Crear y activar entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

# Ejecutar
python app.py
```

Variables de entorno requeridas:
```env
DB_HOST=...
DB_PORT=3306
DB_NAME=...
DB_USER=...
DB_PASSWORD=...
SECRET_KEY=...
```

---

## Deploy en producción

```bash
# En el servidor EC2
cd ~/machinemanagementsystem
git pull
docker compose up --build -d web
```

Para reiniciar sin rebuild (solo cambios en Python/HTML):
```bash
docker compose restart app
```

---

## Módulos del Sistema

### Panel Administrador (`/admin`)
- **Dashboard** — KPIs: ventas del día, máquinas activas, fallas, liquidaciones pendientes.
- **Gestión de Máquinas** — Alta/baja/edición, config de relés GPIO, estaciones, paquetes habilitados, estado.
- **Gestión de Usuarios** — Admins y cajeros con permisos por módulo.
- **Paquetes de Turnos** — Precios y número de turnos por paquete.
- **Locales** — Sedes donde están las máquinas.
- **Mensajes** — Mensajes internos entre usuarios del sistema.
- **Socios / Propietarios** — Inversores y propietarios con porcentajes de liquidación.
- **Logs Transaccionales** (`/admin/logs/transaccionales`) — Dashboard avanzado: KPIs, gráfica de ventas/turnos por hora o día, fallas ESP32, feeds de ventas y actividad en tiempo real.
- **Consola Completa** — Log técnico crudo del sistema.
- **Gestión de Logs** — Exportación y administración de logs del servidor.

### Panel Local / Cajero (`/local`)
- Generación y venta de códigos QR con paquete de turnos.
- Historial de ventas del día.
- Escaneo de QR para verificar estado.
- Envío de QR por WhatsApp (imagen con quiet zone de 60px para GM67).
- Devolución manual de turnos.
- Reimpresión de QR en PDF (papel térmico 58mm).

### Panel de Socios (`/socios`)
- Vista personalizada: máquinas asociadas, ingresos, liquidaciones por inversor.

---

## Hardware ESP32

Cada máquina tiene un ESP32 con:
- **Pantalla TFT ILI9341** (320×240 táctil) — menús, estado de juego, fallas.
- **Lector GM67** (UART2) — lee QR físico o en pantalla de celular.
- **Relés GPIO26/GPIO27** (activo-LOW, SRD-05VDC-SL-C) — controlan reinicio físico de la máquina.

### Flujo de uso (máquina simple):
1. TFT muestra pantalla LISTO PARA JUGAR.
2. Cliente escanea QR → ESP32 verifica con backend (`/api/esp32/registrar-uso`).
3. Si válido: activa relé (pulso 1.5s), muestra JUEGO ACTIVO con turnos restantes.
4. Auto-standby a los 15 segundos.
5. Si falla: cliente presiona REPORTAR FALLA → backend devuelve turno + registra en `machinefailures`.
6. A las 3 fallas sin resolver → máquina entra en MANTENIMIENTO (bloquea QR, actualiza estado en web).

### Flujo multi-estación:
- TFT muestra carrusel con flechas ◄ ► para seleccionar la estación.
- Cada estación tiene su propio relé y contador de fallas independiente.
- Si una estación llega a 3 fallas → solo esa estación queda en mantenimiento.

### Comandos remotos (backend → ESP32 via polling):
| Comando | Efecto |
|---|---|
| `MAINTENANCE` | Bloquea estación, muestra FUERA DE SERVICIO |
| `RESUME` | Desbloquea estación, resetea contadores |
| `ACTIVATE_RELAY` | Pulso de relé (turno manual desde admin) |
| `RESET` | Reinicio completo |
| `UPDATE_STATION_NAMES` | Actualiza nombres de estaciones en TFT |

---

## Base de Datos — Tablas Principales

| Tabla | Descripción |
|---|---|
| `machine` | Máquinas arcade (id, name, status, location_id, errorNote) |
| `machinetechnical` | Config técnica (relay pins, machine_subtype, station names) |
| `qrcode` | Códigos QR (code, remainingTurns, isActive, turnPackageId) |
| `turnpackage` | Paquetes de turnos (name, turns, price) |
| `turnusage` | Historial de usos de QR por máquina |
| `machinefailures` | Fallas desde TFT (station_index, turnos_devueltos, resolved) |
| `transaction_logs` | Log centralizado: ventas, fallas, pagos, inversiones |
| `esp32_commands` | Cola de comandos pendientes para cada ESP32 |
| `socios` | Inversores con porcentaje de participación |
| `propietarios` | Propietarios de máquinas |
| `liquidaciones` | Liquidaciones a socios/propietarios |
| `users` | Usuarios del sistema (admin, cajero) |

---

## Migraciones Flyway

Las migraciones están en `db/migrations/`. Última versión en servidor: **V35**. Siguiente disponible: **V36**.

Para agregar una migración:
1. Crear `V36__descripcion.sql` en `db/migrations/`.
2. Commit y push.
3. En servidor: `docker compose up --build -d web` (Flyway aplica automáticamente al iniciar).

---

## Endpoints ESP32 principales

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/esp32/heartbeat` | Latido del ESP32 |
| GET | `/api/esp32/machine-config/<id>` | Config completa de la máquina |
| POST | `/api/esp32/registrar-uso` | Verificar y consumir un turno de QR |
| POST | `/api/esp32/reportar-falla` | Reportar falla desde botón TFT |
| GET | `/api/esp32/check-commands/<id>` | Polling de comandos pendientes |
| POST | `/api/esp32/command-executed/<id>` | Confirmar ejecución de comando |
| GET | `/api/esp32/machine-technical/<id>` | Estado técnico (créditos actuales) |

---

*Última actualización: 2026-04-10*
