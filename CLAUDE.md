# Inversiones Arcade — Machine Management System

## Reglas obligatorias (leer antes de tocar cualquier archivo)

- **NUNCA hacer commit sin aprobación explícita del usuario.** Siempre terminar con "¿Apruebas el commit?" y esperar respuesta.
- **NUNCA arreglar bugs encontrados al pasar.** Documentarlos en memoria, notificar al usuario, esperar orden.
- **SIEMPRE declarar qué líneas se van a cambiar y por qué** antes de editar cualquier archivo grande (app.py, HTMLs).
- **SIEMPRE explicar el por qué** de cada cambio, no solo el qué.
- Commits directos a `main`. No crear ramas salvo que el usuario lo pida.
- **SIEMPRE hacer push a `main` inmediatamente después del commit**, en el mismo paso. Nunca dejar un commit sin push.

## Stack

- **Backend:** Flask 3.0 + MySQL 8.3 + Docker + EC2 (AWS)
- **Frontend:** HTML/JS/TailwindCSS (templates Jinja2)
- **Hardware:** ESP32 + TFT ILI9341 + GM67 QR reader
- **Migrations:** Flyway — próxima disponible: **V51**
- **Deploy:** `cd ~/machinemanagementsystem && git pull && docker compose up --build -d web`

## Archivos críticos

- `maquinas-medellin-frontend/app.py` — backend Flask (~12,800 líneas). Leer solo las secciones relevantes, nunca el archivo completo.
- `db/migration/` — migraciones Flyway. Último aplicado: V37.
- `C:\Users\Andrés\Desktop\Circuito_maquinas\Circuito_maquinas.ino` — firmware ESP32. Verificar que contenga `browseStation` y `stationInMaintenance` antes de editar.

## Base de datos

- Tabla de máquinas: `machine` (NO `maquinas`)
- Conexión: variables de entorno en docker-compose.yml
- Cambios de esquema: SIEMPRE via migración Flyway, nunca directo a la BD

## Regla de filtro por local (multi-location)

**TODO endpoint admin que devuelva o afecte máquinas DEBE filtrar por `active_location_id` de sesión.**
Usar `get_active_location()` de `utils/location_scope.py`. Si `active_id is None` = admin en modo "todos los locales" → no filtrar.
Aplica especialmente a: listados de máquinas, FORCE_OTA, RESET_CONFIG, comandos ESP32, y cualquier nuevo endpoint que toque la tabla `machine`.

## ESP32 — regla de oro

No cambiar pines sin verificar `memory/project_hardware_esp32.md`. Los pines actuales están confirmados y funcionando.

## Al iniciar una sesión

1. Preguntar al usuario: ¿cuál es el objetivo de hoy?
2. Leer solo los archivos que se van a tocar
3. Confirmar el plan antes de ejecutar

## Al cerrar una sesión

Actualizar los archivos de memoria afectados en:
`C:\Users\Andrés\.claude\projects\C--Users-Andr-s-Desktop-machinemanagementsystem-main\memory\`
