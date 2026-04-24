# Plan Maestro Operacional - Inversiones Arcade

Version: 1.0  
Fecha: Abril 2026  
Estado: Documento rector inicial

---

## 1. Vision

Inversiones Arcade es una operacion de maquinas arcade conectadas, administradas y auditables para restaurantes, locales comerciales y negocios aliados.

El sistema `machinemanagementsystem` no debe verse solo como una aplicacion web. Es la plataforma que permite convertir maquinas fisicas en activos operables, medibles y financiables:

- El restaurante presta el espacio y recibe un porcentaje.
- Inversiones Arcade administra la operacion y cobra administracion.
- Los inversionistas financian maquinas y reciben utilidad segun su participacion.
- Los clientes compran turnos mediante QR y los usan directamente en la maquina.
- El modulo fisico ESP32 + GM67 + TFT + reles conecta la experiencia real con el backend.

La meta es que el negocio pueda pasar de un piloto en El Mekatiadero a una red multi-local, multi-maquina y multi-inversionista, sin depender de controles manuales, confianza verbal o revisiones constantes por WhatsApp.

---

## 2. Principio rector

Toda decision tecnica debe responder esta pregunta:

> Esto ayuda a Inversiones Arcade a operar mas maquinas, con mas confianza, menos intervencion manual y mejores numeros?

Si la respuesta es si, tiene prioridad. Si la respuesta es no, puede esperar.

---

## 3. Unidades centrales del negocio

### 3.1 Local

El local es la unidad operacional.

Representa el restaurante, negocio o sede donde estan instaladas las maquinas. Cada local puede tener:

- Usuarios propios.
- Paquetes disponibles.
- Maquinas asignadas.
- Reportes propios.
- Porcentaje comercial negociado.
- Liquidaciones separadas.

Estado actual en el repo:

- Existe `location`.
- Existe `location_id` en maquinas.
- Existe `location_id` en usuarios.
- Existe `location_id` en paquetes.
- Existe `location_scope.py` para limitar datos por local activo.
- Existe selector de local para admins globales.

Riesgo actual:

- No todos los endpoints necesariamente aplican filtro por local.
- Algunas tablas legacy siguen usando nombre de local como texto.
- Hay que terminar de aplicar `apply_location_filter()` / `apply_location_name_filter()` en blueprints pendientes.

### 3.2 Maquina

La maquina es la unidad economica y tecnica.

Cada maquina debe tener:

- Local asignado.
- Tipo comercial.
- Estado operativo.
- Configuracion tecnica.
- Modulo fisico instalado.
- Propietarios/inversionistas.
- Porcentajes de negocio, administracion y utilidad.
- Historial de usos, fallas, reinicios, devoluciones y liquidaciones.

Estado actual en el repo:

- Existe CRUD de maquinas.
- Existe configuracion tecnica en `machinetechnical`.
- Existe soporte multi-estacion.
- Existe asociacion con propietarios/inversionistas.
- Existe control remoto: turno manual, reinicio, comandos al ESP32.
- Existe heartbeat del ESP32.
- Existe mantenimiento por fallas consecutivas.

Riesgo actual:

- Falta tratar el modulo fisico como inventario propio.
- Falta historial formal de instalacion, reemplazo y firmware.
- Falta flujo completo de mantenimiento tipo ticket.

### 3.3 Inversionista / socio

El socio es la unidad financiera.

Un socio puede invertir en una o varias maquinas, en uno o varios locales. Debe poder ver:

- Capital invertido.
- Porcentaje de propiedad.
- Ingresos generados.
- Participacion calculada.
- Pagos recibidos.
- Saldos pendientes.
- ROI.
- Evolucion historica.

Estado actual en el repo:

- Existe `socios_bp`.
- Existe `socios_finance.py`.
- Existe panel `socios.html`.
- Existen endpoints de resumen, maquinas, pagos, ingresos, ROI y evolucion.
- Existe rol `socio`.

Riesgo actual:

- Hay que validar si el login independiente de socio esta completamente listo para uso real.
- Hay que confirmar que el socio ve solo su informacion.
- Hay que cerrar trazabilidad de pagos con comprobante, estado y responsable.

---

## 4. Lo que ya esta construido

El sistema no es un MVP simple. Ya tiene una base relevante:

- Mas de 100 endpoints.
- 20 blueprints.
- 21 plantillas.
- Logica financiera real.
- Integracion con hardware.
- Rutas administrativas.
- Panel cajero/local.
- Panel socios.
- Liquidaciones.
- Logs y auditoria.
- Roles.
- Locales.
- Paquetes.
- Maquinas multi-estacion.

Modulos principales:

- `auth`: login, sesion, landing, contacto inversionista, contexto de local.
- `qr`: generacion, venta, asignacion, verificacion, historial, reportes.
- `esp32`: endpoints del modulo fisico, heartbeat, comandos, fallas.
- `machines`: CRUD, estado, configuracion tecnica, propietarios, acciones remotas.
- `locations`: gestion de locales.
- `users`: gestion de usuarios.
- `socios`: gestion y portal de inversionistas.
- `inversiones`: inversion por socio/maquina.
- `pagos`: pagos/cuotas.
- `liquidaciones`: calculo y cierre financiero.
- `logs`: trazabilidad, exportacion, consola y alertas internas.
- `dashboard`: KPIs y vistas ejecutivas.

Conclusion:

Lo que falta no es construir desde cero. Falta cerrar huecos operacionales especificos para que el sistema soporte crecimiento real.

---

## 5. Flujos operacionales base

### 5.1 Venta de QR

Flujo deseado:

1. Cajero ingresa al panel local.
2. Selecciona paquete.
3. Genera o asigna QR.
4. Registra venta real.
5. Se guarda vendedor, local, paquete, precio, fecha, metodo de pago y estado.
6. Cliente recibe QR fisico o digital.

Estado actual:

- Generacion y asignacion existen.
- Historial de ventas existe.
- Paquetes por local existen.
- Falta metodo de pago formal.

Decision pendiente clave:

> Cuando se considera vendido un QR: al generarlo, al asignarlo a paquete, al registrar venta, o al primer escaneo?

Recomendacion:

Un QR debe considerarse vendido solo cuando se registra la venta comercial. La generacion del codigo no debe representar ingreso. El primer escaneo representa uso, no venta.

Esto permite:

- Cuadrar caja.
- Separar inventario de codigos de ventas reales.
- Controlar QRs generados pero no vendidos.
- Reportar ingresos aunque el cliente no use todos los turnos.

### 5.2 Uso de QR en maquina

Flujo deseado:

1. Cliente escanea QR en GM67.
2. ESP32 consulta backend.
3. Backend valida saldo, maquina y estado.
4. Backend descuenta turno.
5. ESP32 activa rele.
6. Backend registra `turnusage`.
7. La TFT muestra turnos restantes.

Estado actual:

- El firmware llama `/api/esp32/registrar-uso`.
- Se descuenta `userturns`.
- Se registra `turnusage`.
- Soporta estacion seleccionada.
- Soporta `turns_remaining_after`.

Riesgos a revisar:

- Consistencia entre `qrcode.remainingTurns` y `userturns.turns_remaining`.
- Definir fuente canonica de saldo.
- Evitar doble consumo por reintento de red.

### 5.3 Falla y devolucion

Flujo deseado:

1. Cliente reporta falla desde TFT.
2. Backend valida ultimo uso.
3. Se registra falla.
4. Se devuelve turno.
5. Se actualiza contador de fallas consecutivas por estacion.
6. Si llega a 3 fallas, se bloquea estacion o maquina.
7. Admin recibe alerta.
8. Soporte resuelve y documenta.

Estado actual:

- Existe reporte de falla desde ESP32.
- Existe devolucion automatica.
- Existe mantenimiento por estacion.
- Existe resolucion de falla desde admin.
- Existe registro en logs transaccionales.

Hueco:

- Falta flujo de mantenimiento tipo ticket con responsable, evidencia, diagnostico, repuesto y costo.
- Falta alerta externa cuando una maquina entra en mantenimiento.

### 5.4 Cierre y liquidacion

Flujo deseado:

1. Admin selecciona periodo y local.
2. Sistema calcula ingresos reales.
3. Muestra desglose negocio / administracion / utilidad.
4. Muestra inversionistas y participaciones.
5. Admin revisa gastos informativos.
6. Admin confirma cierre.
7. El cierre queda congelado.
8. Se genera comprobante/PDF.
9. Se registran pagos a socios/restaurante.

Estado actual:

- Existe calculo 3-way.
- Existe `cierre_liquidacion`.
- Existe historial.
- Existen gastos informativos.
- Existe vista de liquidaciones.

Huecos:

- Evitar liquidar dos veces el mismo local y periodo.
- Generar PDF de liquidacion.
- Congelar snapshot detallado de participantes y porcentajes.
- Registrar estado del cierre: borrador, confirmado, pagado, anulado.
- Registrar quien confirmo, cuando y desde que local.

---

## 6. Preguntas de negocio que deben responderse

### Ventas y caja

- Que metodos de pago acepta el local: efectivo, transferencia, tarjeta, mixto?
- El restaurante recauda el dinero o Inversiones Arcade lo recauda?
- Si el restaurante recauda, como se concilia contra ventas del sistema?
- Si Inversiones Arcade recauda, como se liquida al restaurante?
- Que pasa si el vendedor registra mal el metodo de pago?
- Se permite editar una venta despues de registrada?
- Quien puede editar una venta?
- Toda edicion queda auditada con usuario, fecha, motivo y valor anterior?

### Liquidaciones

- El ingreso se reconoce al vender el QR o al usar el turno?
- Se liquida por venta, por uso, o se muestran ambas vistas?
- Que pasa con QRs vendidos y nunca usados?
- Que pasa con turnos restantes al cerrar periodo?
- Se puede cerrar dos veces el mismo periodo?
- Se puede anular un cierre?
- Quien aprueba un cierre?
- Quien marca un cierre como pagado?

### Mantenimiento

- Quien recibe alerta cuando una maquina falla?
- Quien puede poner una maquina en mantenimiento manualmente?
- Quien puede sacarla de mantenimiento?
- Se registra evidencia de reparacion?
- Los gastos de mantenimiento descuentan utilidad o son informativos?
- Como se reporta una maquina que estuvo 15 dias fuera de servicio?

### Inversionistas

- El socio entra con usuario propio?
- Puede ver todos sus locales en una vista unificada?
- Puede descargar comprobantes?
- Ve rentabilidad por maquina y por local?
- Ve pagos pendientes y pagados?
- Que pasa si un socio vende su participacion?

### Hardware

- Como se identifica cada modulo fisico?
- Se guarda MAC del ESP32?
- Se guarda version de firmware?
- Que pasa si se reemplaza un ESP32?
- Como se instala un segundo local sin recompilar firmware manualmente?
- Se necesita captive portal WiFi?

---

## 7. Huecos confirmados y propuestas

### 7.1 Metodo de pago en ventas

Estado actual:

Implementado en backend y panel de ventas.

Cobertura actual:

El sistema ya registra metodo de pago en la venta con:

- `efectivo`
- `transferencia`
- `tarjeta`
- `mixto`
- `cortesia`
- `ajuste`

Tambien incluye:

- Reporte ventas del dia por metodo.
- Edicion posterior con auditoria.
- Motivo obligatorio al editar.
- Usuario responsable.
- Valor anterior y valor nuevo.

Resultado:

El cuadre diario de caja ya puede apoyarse en el sistema sin depender de memoria del cajero.

Impacto operacional:

Permite cuadre de caja diario y reduce discusiones con el restaurante.

### 7.2 PDF de liquidacion

Estado actual:

Implementado como comprobante oficial de cierre.

Cobertura actual:

El modulo ya genera PDF para negocio e inversionistas a partir de un cierre confirmado, con:

- Local.
- Periodo.
- Fecha de cierre.
- Ventas totales.
- Negocio.
- Administracion.
- Utilidad.
- Maquinas.
- Paquetes vendidos.
- Inversionistas.
- Pagos sugeridos.
- Observaciones.
- Firma/responsable.

Regla operativa actual:

- El PDF oficial solo se habilita cuando existe cierre confirmado para ese periodo exacto.
- Un calculo preliminar no se presenta como comprobante final.

Impacto operacional:

Da confianza al restaurante y al inversionista.

### 7.3 Cierre unico por periodo

Estado actual:

Implementado en aplicacion.

Regla activa:

- Un cierre confirmado por `local_id + fecha_inicio + fecha_fin`.
- El sistema rechaza cierres que se crucen con un cierre oficial existente.
- Nunca se sobrescribe un cierre confirmado desde el flujo normal.

Siguiente mejora sugerida:

- Si se necesita corregir, crear anulacion o version corregida con auditoria explicita.

Impacto operacional:

Evita duplicidad contable.

### 7.4 ESP32 offline

Escenario critico:

Viernes 8pm, restaurante lleno, router cae 20 minutos.

Opciones:

| Opcion | Complejidad | Riesgo | Comentario |
|---|---:|---:|---|
| Panel emergencia: admin activa rele | Baja | Bajo | Ya existe, pero requiere operador disponible |
| Modo gracia limitado | Media | Bajo/Medio | Recomendado para continuidad operativa |
| Validacion offline completa de QR | Alta | Medio/Alto | Requiere sincronizacion y evita menos fraude |

Propuesta:

Implementar modo gracia:

- Si el ESP32 pierde conexion pero tuvo backend online recientemente, permitir N activaciones locales.
- Limitar por tiempo y cantidad.
- Registrar evento local pendiente.
- Sincronizar cuando vuelva internet.
- Alertar al admin si heartbeat se pierde mas de 5 minutos.

Impacto operacional:

Evita parar ventas por fallas cortas de red.

### 7.5 Notificaciones

Hueco:

El sistema tiene logs, pero la operacion no debe depender de que alguien entre a revisar.

Propuesta:

Agregar notificaciones por WhatsApp/email para:

- ESP32 sin heartbeat.
- Maquina en mantenimiento.
- Falla reportada.
- Cierre listo para revisar.
- Cierre confirmado.
- Pago/cuota vencida.
- Venta anomala o exceso de QRs generados.

Impacto operacional:

Permite escalar de 1 local a 5+ locales sin vigilancia manual constante.

### 7.6 Inventario de modulos fisicos

Hueco:

El modulo ESP32 no esta modelado como activo independiente.

Propuesta:

Crear entidad `hardware_module`:

- id interno.
- serial.
- mac_address.
- firmware_version.
- machine_id actual.
- fecha_instalacion.
- tecnico_instalador.
- estado.
- ultima_conexion.
- notas.

Impacto operacional:

Permite reemplazos, trazabilidad tecnica y soporte de campo.

### 7.7 Onboarding de nuevo local

Checklist objetivo:

1. Crear local.
2. Crear usuarios del local.
3. Crear paquetes del local.
4. Registrar maquinas.
5. Configurar porcentajes.
6. Configurar propietarios/inversionistas.
7. Preparar modulo fisico.
8. Asociar modulo a maquina.
9. Hacer prueba QR.
10. Hacer prueba de falla.
11. Hacer cierre de prueba.
12. Activar operacion.

Hueco:

El paso de configurar ESP32 todavia depende de editar/subir firmware con `MACHINE_ID`, WiFi y backend.

Propuesta futura:

Captive portal para configurar:

- WiFi SSID.
- WiFi password.
- Backend URL.
- Machine ID.
- Token del dispositivo.

Impacto operacional:

Reduce dependencia de Andres para cada instalacion.

---

## 8. Riesgos y controles

### Riesgo: QRs extra generados por empleado

Escenario:

Un empleado genera QRs adicionales y se los queda.

Controles propuestos:

- Limite de QRs por usuario/sesion.
- Alerta si se generan mas de N QRs en X minutos.
- Reporte diario por vendedor.
- Diferenciar QR generado, vendido, usado, anulado.
- Auditoria de cada venta.

### Riesgo: devolucion fraudulenta

Escenario:

Alguien devuelve turnos sin justificacion real.

Controles propuestos:

- Motivo obligatorio.
- Usuario aprobador.
- Relacion con uso/falla.
- Limite de devoluciones por QR.
- Reporte de devoluciones por usuario.

### Riesgo: porcentaje cambiado despues del periodo

Escenario:

Se cambia porcentaje de una maquina y una liquidacion vieja queda distinta.

Controles propuestos:

- Guardar snapshot de porcentajes en el cierre.
- Historial de cambios de porcentaje.
- Vigencia desde/hasta para acuerdos comerciales.

### Riesgo: hardware reemplazado sin trazabilidad

Escenario:

Se cambia un ESP32 y luego no se sabe cual modulo pertenece a cual maquina.

Controles propuestos:

- Inventario de modulos.
- Vinculacion modulo-maquina.
- Version firmware.
- Historial de instalacion/reemplazo.

---

## 9. Roadmap recomendado

### Fase 1 - Ahora: operar limpio en El Mekatiadero

Objetivo:

Cerrar el piloto con calidad operacional.

Prioridades:

1. Completar/verificar `socios.html` para que el socio vea estado real.
2. Revisar filtros por local en blueprints pendientes.
3. Documentar checklist de cierre diario y cierre mensual.
4. Actualizar manuales operativos para reflejar cierre unico y PDF oficial.
5. Definir flujo formal de anulacion o version corregida de cierres.
6. Verificar en operacion real el uso de `cortesia` y `ajuste`.
7. Consolidar reporte mensual para restaurante y socios.

Criterio de exito:

El Mekatiadero puede operar una semana completa con ventas, fallas, devoluciones y cierre financiero sin cuadre manual externo.

### Fase 2 - Proximo mes: abrir segundo local

Objetivo:

Hacer que agregar un local sea repetible.

Prioridades:

1. Checklist de onboarding de local.
2. Modo gracia ESP32 offline.
3. Alerta de heartbeat perdido.
4. Notificaciones de fallas/mantenimiento.
5. Reporte mensual del restaurante.
6. Inventario basico de modulos fisicos.
7. Guia de instalacion de modulo en maquina.

Criterio de exito:

Se puede instalar una maquina en un segundo local con pasos documentados y reportes separados.

### Fase 3 - Cuando entren inversionistas externos

Objetivo:

Dar confianza financiera y trazabilidad.

Prioridades:

1. Login independiente de socios validado.
2. Dashboard inversionista completo.
3. Pagos con comprobante.
4. Estado de cuenta por socio.
5. Historial de liquidaciones por socio.
6. Alertas de pagos/cuotas.
7. Contrato/acuerdo por inversion.

Criterio de exito:

Un inversionista puede entender cuanto invirtio, cuanto genero, cuanto se le pago y cuanto se le debe sin pedir explicaciones por fuera del sistema.

### Fase 4 - Operacion multi-local madura

Objetivo:

Inversiones Arcade opera varios locales sin perder control.

Prioridades:

1. Dashboard ejecutivo global.
2. Dashboard por local.
3. Dashboard tecnico de hardware.
4. Tickets de mantenimiento.
5. Analisis de rentabilidad por maquina/local.
6. Alertas anomalas de ventas, fallas y devoluciones.
7. Historial de acuerdos comerciales.

Criterio de exito:

La gerencia puede decidir donde poner o quitar maquinas con datos reales.

### Fase 5 - Venta/licenciamiento del sistema

Objetivo:

Convertir la plataforma en producto vendible a otros operadores.

Prioridades:

1. Arquitectura multi-tenant.
2. Captive portal para ESP32.
3. Tokens por dispositivo.
4. API publica documentada.
5. Panel self-service de onboarding.
6. Marca configurable.
7. Planes comerciales/licencias.

Criterio de exito:

Otro operador puede usar el sistema sin mezclar datos con Inversiones Arcade.

---

## 10. Decisiones tecnicas sugeridas

### 10.1 Fuente canonica de ingresos

Recomendacion:

Para caja y ventas, la fuente canonica debe ser la venta registrada (`qrhistory` / evento comercial). Para uso y rentabilidad por maquina, la fuente debe cruzarse con `turnusage`.

Esto permite dos vistas:

- Ingreso vendido: dinero recibido.
- Uso jugado: actividad real de maquinas.

Ambas son importantes y no deben mezclarse sin explicacion.

### 10.2 Fuente canonica de saldo

Recomendacion:

Elegir una sola fuente canonica entre `userturns.turns_remaining` y `qrcode.remainingTurns`. La otra debe ser derivada, sincronizada o eliminada en una futura migracion.

Mientras existan ambas:

- Registrar pruebas de consistencia.
- Evitar actualizaciones parciales.
- Corregir diferencias con auditoria.

### 10.3 Cierres inmutables

Recomendacion:

Un cierre confirmado no se edita. Se anula o se corrige con otro cierre referenciado.

Debe guardar snapshot de:

- Ventas.
- Maquinas.
- Porcentajes.
- Inversionistas.
- Totales.
- Usuario que confirma.
- Local.
- Fecha/hora.

### 10.4 Eventos transaccionales

Recomendacion:

Cada accion critica debe generar evento:

- Venta.
- Edicion de venta.
- Devolucion.
- Falla.
- Mantenimiento.
- Turno manual.
- Reinicio.
- Cambio de porcentaje.
- Cierre.
- Pago.
- Cambio de propietario/inversionista.

---

## 11. Tablero de prioridades

| Prioridad | Tema | Por que importa |
|---|---|---|
| Alta | Metodo de pago | Cuadre de caja diario |
| Alta | Cierre unico | Evita errores contables |
| Alta | PDF liquidacion | Confianza con restaurante/socios |
| Alta | Filtros por local | Escalabilidad multi-local |
| Alta | Panel socio real | Confianza inversionista |
| Media | Alertas heartbeat/fallas | Operacion sin vigilancia manual |
| Media | Modo gracia offline | Continuidad en restaurante lleno |
| Media | Inventario hardware | Soporte y reemplazos |
| Media | Tickets mantenimiento | Control operativo |
| Baja ahora | Multi-tenant | Importante, pero no antes de operar bien |
| Baja ahora | Captive portal | Muy util, pero despues de segundo local |

---

## 12. Indicadores clave

### Operacion

- Ventas por dia.
- Ventas por local.
- Ventas por metodo de pago.
- Turnos usados.
- Turnos restantes vendidos.
- QRs generados vs vendidos vs usados.

### Maquinas

- Ingreso por maquina.
- Turnos por maquina.
- Fallas por maquina.
- Fallas por estacion.
- Tiempo en mantenimiento.
- Ultimo heartbeat.
- Reinicios remotos.

### Finanzas

- Ingresos totales.
- Monto restaurante.
- Monto administracion.
- Utilidad inversionistas.
- Cierres confirmados.
- Pagos pendientes.
- ROI por socio.

### Expansion

- Rentabilidad por local.
- Rentabilidad por maquina.
- Fallas por tipo de maquina.
- Tiempo promedio de instalacion.
- Costo de mantenimiento por maquina.

---

## 13. Criterios para no perder foco

No construir todavia:

- Multi-tenant completo antes de operar bien dos locales.
- Marketplace de inversionistas antes de cerrar pagos y liquidaciones.
- Automatizacion compleja de firmware antes de tener inventario basico.
- Reportes avanzados si aun no hay metodo de pago y cierre confiable.

Construir primero:

- Caja diaria.
- Liquidacion confiable.
- Socio con datos reales.
- Alertas operativas.
- Onboarding repetible.
- Mantenimiento trazable.

---

## 14. Proxima sesion sugerida

Para avanzar de plan a ejecucion, la siguiente sesion deberia elegir una de estas rutas:

1. Disenar e implementar metodo de pago en ventas.
2. Auditar liquidaciones y cierre unico.
3. Revisar `socios.html` contra endpoints reales.
4. Hacer checklist tecnico-operativo para abrir segundo local.
5. Disenar modelo de inventario de modulos ESP32.

Recomendacion:

Empezar por metodo de pago + reporte de caja diaria. Es el hueco mas inmediato para operar limpio en El Mekatiadero.
