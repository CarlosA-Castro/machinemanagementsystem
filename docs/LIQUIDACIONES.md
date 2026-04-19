# Módulo de Liquidaciones — Inversiones Arcade
### Documentación Ejecutiva del Sistema de Gestión de Máquinas

**Versión:** 1.0  
**Fecha:** Abril 2026  
**Audiencia:** Dirección ejecutiva y administrativa

---

## 1. ¿Qué es una Liquidación?

Una **liquidación** es el proceso de cierre financiero de un período determinado. Al ejecutarla, el sistema toma todas las ventas reales registradas durante ese período y calcula con exactitud cuánto dinero le corresponde a cada parte involucrada:

| Parte | Descripción | % por defecto |
|---|---|---|
| **Negocio (Restaurante/Local)** | El establecimiento que aloja la máquina | 35% |
| **Administración (Inversiones Arcade)** | La empresa que administra y opera las máquinas | 25% |
| **Utilidad (Inversionistas)** | Los propietarios del capital invertido en las máquinas | 40% |

> **Importante:** Estos porcentajes son configurables individualmente por máquina. Cada acuerdo comercial con un restaurante puede tener condiciones distintas, y el sistema las respeta de forma independiente.

---

## 2. Fuente de los Datos

El sistema obtiene la información de ventas directamente desde la base de datos operativa, sin intervención manual. Solo se contabilizan transacciones que cumplen todos estos criterios:

- **Ventas reales:** Solo registros marcados como `es_venta_real = TRUE`. Se excluyen escaneos de prueba o administrativos.
- **Paquetes comerciales:** Se excluye el paquete especial interno (ID 1), que no representa una venta al público.
- **Período seleccionado:** Solo ventas cuya fecha cae dentro del rango elegido por el usuario.
- **Local seleccionado:** Si el administrador gestiona múltiples locales, los datos se filtran por la ubicación activa.

> **Garantía de integridad:** El cálculo es completamente automático y trazable. No hay cifras ingresadas a mano — todo parte de los registros de escaneo de los códigos QR vendidos en las máquinas.

---

## 3. Cómo Usar el Módulo

El flujo de trabajo es simple y tiene dos etapas claramente separadas:

### Etapa 1: Calcular (sin guardar nada)

1. Navegar al módulo **Liquidaciones** en el sistema.
2. Seleccionar el rango de fechas: **Desde** y **Hasta**.
3. Hacer clic en **"Calcular"**.
4. El sistema muestra los resultados en 4 pestañas para revisión.

> **Esta operación es completamente segura.** "Calcular" solo lee datos — no guarda, no modifica, no cierra nada. Se puede calcular tantas veces como se desee sin ningún efecto permanente.

### Etapa 2: Confirmar Cierre (acción definitiva)

5. Revisar los resultados en todas las pestañas.
6. En la pestaña **"Inversiones Arcade"**, hacer clic en **"Confirmar Cierre"**.
7. El sistema registra oficialmente el cierre del período.

> **Atención:** Solo el botón "Confirmar Cierre" crea un registro permanente. Un cierre no se produce automáticamente al calcular. Esta separación permite revisar los números antes de comprometerse con el cierre oficial.

---

## 4. Las Cuatro Pestañas del Módulo

### Pestaña 1: Negocio

Esta pestaña presenta el **resumen financiero global del período**. Es la vista de alto nivel para entender de un vistazo cómo se distribuyeron los ingresos.

#### Panel de indicadores principales

| Indicador | Descripción |
|---|---|
| **Ingresos Totales** | Suma de todos los paquetes vendidos en el período (unidades × precio por paquete) |
| **Negocio** | Porción que corresponde al restaurante (Ingresos × % Negocio de cada máquina) |
| **Administración** | Porción que retiene Inversiones Arcade (Ingresos × % Admin de cada máquina) |
| **Utilidad** | Lo que queda para los inversionistas (Ingresos − Negocio − Administración) |
| **% vs período anterior** | Comparación automática con el período inmediatamente anterior de igual duración |

#### Gráfico: Últimas Liquidaciones

Gráfico de barras apiladas que muestra la distribución Negocio / Administración / Utilidad para cada período cerrado oficialmente. Este gráfico se alimenta solo de cierres confirmados, por lo que requiere al menos un cierre previo para mostrar historial.

#### Estadísticas de uso

- **Paquete más vendido:** El tipo de paquete con mayor cantidad de unidades vendidas en el período.
- **Top 3 Máquinas:** Las tres máquinas con más turnos jugados durante el período.

---

#### Tabla de Paquetes

Desglosa la información por tipo de paquete comercializado:

| Columna | Descripción |
|---|---|
| **Paquete** | Nombre del paquete (ej: "5 turnos", "10 turnos") |
| **Unidades vendidas** | Cantidad de paquetes de ese tipo vendidos en el período |
| **Precio unitario** | Precio de venta de cada paquete |
| **Ingresos totales** | Unidades × Precio |
| **Turnos equivalentes** | Total de turnos que representan las ventas (unidades × turnos por paquete) |
| **Turnos usados** | Turnos efectivamente jugados (*ver nota abajo*) |
| **Turnos restantes** | Saldo actual en base de datos para los QR vendidos en este período |
| **Valor restantes** | Turnos restantes × (precio / turnos por paquete) — valor monetario pendiente de uso |

> **Nota sobre "Turnos usados":** Actualmente este campo muestra 0. Los dispositivos ESP32 instalados en las máquinas no descuentan el saldo del código QR al momento de jugar. Para reflejar el uso real, se requeriría una actualización del firmware del hardware. Los demás campos no se ven afectados por esta limitación.

---

#### Detalle de Ventas (expandible)

Al hacer clic en **"Ver detalle"**, se despliega una tabla con el registro individual de cada venta del período, mostrando:

- Fecha y hora de la venta
- Vendedor (usuario del sistema que procesó la venta)
- Nombre del paquete
- Máquina utilizada
- Precio
- % Negocio aplicado y monto correspondiente
- Propietario/inversionista de esa máquina

---

### Pestaña 2: Inversionistas

Muestra **una tarjeta por cada inversionista**, con el detalle de todas las máquinas que posee y su participación económica en el período.

Por cada máquina del inversionista se presenta:

| Columna | Descripción |
|---|---|
| **Máquina** | Nombre o identificador de la máquina |
| **Paquetes jugados** | Cantidad de paquetes vendidos/usados en esa máquina |
| **Turnos jugados** | Total de turnos del período en esa máquina |
| **Ingresos de la máquina** | Total generado por esa máquina en el período |
| **Participación** | Monto que le corresponde al inversionista por esa máquina |
| **% vs anterior** | Comparación de su participación frente al período anterior |

**Fórmula de participación:**

```
Participación = Ingresos máquina × (% Utilidad / 100) × (% Propiedad del inversionista / 100)
```

> **Ejemplo:** Si una máquina generó $500.000, la utilidad es 40% y el inversionista posee 60% de esa máquina:
> $500.000 × 0,40 × 0,60 = **$120.000**

---

### Pestaña 3: Inversiones Arcade

Esta es la vista de **gestión operativa** para el equipo de Inversiones Arcade. Presenta el detalle completo máquina por máquina:

| Columna | Descripción |
|---|---|
| **Máquina** | Identificador de la máquina |
| **Paquetes / Turnos** | Actividad del período |
| **Ingresos** | Total generado por la máquina |
| **% Negocio / % Admin / % Utilidad** | Distribución configurada para esa máquina |
| **Inversionista** | Propietario de la máquina |
| **% Propiedad** | Porcentaje que posee el inversionista |
| **A pagar inversionista** | Monto concreto a liquidar al inversionista |

#### Gastos Informativos

Esta sección permite registrar gastos del período (peluches para máquinas claw, reparaciones, insumos, etc.). 

> **Aclaración importante:** Los gastos ingresados aquí son **estrictamente informativos**. No reducen la utilidad calculada ni afectan ningún pago. Su propósito es mantener un registro contable de referencia para la administración interna.

#### Confirmar Cierre

El botón **"Confirmar Cierre"** es la acción más importante del módulo. Al ejecutarlo:

1. El sistema guarda un registro oficial del cierre con todos los totales.
2. El período queda bloqueado como cerrado.
3. El cierre aparece en el historial y en los gráficos comparativos futuros.

> **Este es el único mecanismo para registrar un período como cerrado.** Sin este paso, el período simplemente no existirá en el historial, aunque se haya calculado múltiples veces.

---

### Pestaña 4: Histórico

Proporciona una **visión longitudinal** del desempeño financiero de la operación a lo largo del tiempo.

Contiene:

- **Gráfico de líneas:** Evolución de los ingresos totales en cada período cerrado.
- **Gráfico de dona:** Distribución promedio de Negocio / Administración / Utilidad a través de todos los cierres históricos.
- **Tabla de cierres:** Lista de todos los períodos cerrados oficialmente, con fechas de inicio, fechas de fin y montos totales.

> Este módulo solo muestra datos de períodos confirmados con "Confirmar Cierre". Un período calculado pero no confirmado no aparece aquí.

---

## 5. Configuración de Porcentajes por Máquina

El ícono de engranaje **(⚙)** en la interfaz abre un panel de configuración que permite ajustar la distribución de ingresos de cada máquina de forma independiente.

| Campo | Editable | Descripción |
|---|---|---|
| **% Negocio** | Sí | Porcentaje que retiene el restaurante donde está la máquina |
| **% Administración** | Sí | Porcentaje que cobra Inversiones Arcade por la operación |
| **% Utilidad** | No (calculado) | Resultado automático: 100% − % Negocio − % Admin |

Los cambios se guardan de forma permanente y se aplican en todos los cálculos de liquidación siguientes.

> **Caso de uso:** Si un restaurante de alto tráfico negocia un mayor porcentaje por tener la máquina, se puede configurar 45% Negocio, 20% Admin, y la utilidad del inversionista quedará en 35% — todo sin afectar otras máquinas.

---

## 6. Filtro por Local

Si Inversiones Arcade opera en múltiples ubicaciones (restaurantes / locales), el módulo muestra un **selector de local** en la parte superior.

- Cada local tiene su propia liquidación independiente.
- Los datos de ventas se filtran por ubicación, sin mezcla entre locales.
- Los administradores con acceso a un solo local ven automáticamente sus datos sin selector.

---

## 7. Fórmulas del Sistema

Para referencia y auditoría, estas son las fórmulas exactas que aplica el sistema:

```
Ingresos Totales    = Σ (unidades_vendidas_por_paquete × precio_paquete)

Negocio             = Ingresos Totales × %Negocio_ponderado
Administración      = Ingresos Totales × %Admin_ponderado
Utilidad            = Ingresos Totales − Negocio − Administración

Participación de inversionista (por máquina):
  = ingresos_máquina × (100 − %Negocio − %Admin) / 100 × %Propiedad / 100
```

> El porcentaje ponderado considera el porcentaje configurado por cada máquina, aplicado proporcionalmente según el volumen de ingresos de cada una.

---

## 8. Reglas de Negocio Clave

1. **"Calcular" no destruye ni guarda nada.** Es una operación de solo lectura. Se puede ejecutar en cualquier momento sin consecuencias.

2. **Solo "Confirmar Cierre" crea un registro permanente.** Este paso es intencional y deliberado — existe para dar al administrador la oportunidad de revisar los números antes de comprometerse.

3. **Los gastos no afectan la utilidad.** Son registros contables informativos, no descuentos sobre los ingresos distribuidos.

4. **Cada local se liquida de forma independiente.** No hay cruce de datos entre ubicaciones.

5. **Los porcentajes son configurables por máquina.** Cada acuerdo comercial puede ser diferente.

6. **La comparación con el período anterior es automática.** El sistema calcula la variación porcentual tomando el período de igual duración inmediatamente anterior al seleccionado.

---

## 9. Tablas de Base de Datos Involucradas

Para referencia técnica y auditoría:

| Tabla | Función |
|---|---|
| `qrhistory` | Registros de ventas — fuente primaria de verdad |
| `qrcode` | Códigos QR con saldo de turnos restantes |
| `turnpackage` | Catálogo de paquetes (nombre, precio, turnos, local) |
| `turnusage` | Registro de uso de turnos por máquina |
| `machine` | Catálogo de máquinas |
| `maquinaporcentajerestaurante` | % Negocio y % Admin configurados por máquina |
| `maquinapropietario` | % de propiedad por máquina por inversionista |
| `propietarios` | Registro de inversionistas |
| `gastos_liquidacion` | Gastos informativos del período |
| `cierre_liquidacion` | Períodos cerrados oficialmente (registro permanente) |

---

## 10. Preguntas Frecuentes

**¿Qué pasa si calculo varias veces el mismo período sin confirmar?**  
Nada. El cálculo es de solo lectura. Solo el último "Confirmar Cierre" crea el registro oficial. Si se confirma el mismo período dos veces, se generarán dos registros de cierre, lo cual puede distorsionar el historial — por eso se recomienda confirmar una sola vez por período.

---

**¿Por qué "Turnos usados" aparece en 0?**  
Los dispositivos físicos (ESP32) instalados en las máquinas no envían al sistema la información de cuándo se juega un turno. La columna existe en el sistema y está preparada para recibirla, pero requiere una actualización del firmware del hardware para comenzar a funcionar. Esto no afecta el cálculo financiero, ya que los ingresos se basan en los paquetes vendidos, no en los jugados.

---

**¿Los gastos que registro en "Gastos Informativos" se descuentan de la utilidad del inversionista?**  
No. Los gastos son estrictamente informativos y no afectan ningún cálculo. La utilidad del inversionista se basa únicamente en los ingresos por ventas y los porcentajes configurados. Los gastos sirven como registro contable interno de Inversiones Arcade.

---

**¿Puedo configurar porcentajes distintos para cada máquina?**  
Sí. Cada máquina tiene su propia configuración de % Negocio y % Admin. Una máquina en un restaurante de mayor volumen puede tener un acuerdo distinto al de otra máquina en un local diferente, y el sistema los calcula de forma completamente independiente.

---

**¿Qué significa "% vs período anterior" y cómo se calcula?**  
Es una comparación automática con el período inmediatamente anterior de igual duración. Si se selecciona del 1 al 31 de marzo, el sistema tomará automáticamente del 1 al 28 de febrero como período de comparación y mostrará la variación porcentual de ingresos entre ambos. No requiere ninguna configuración adicional.

---

**¿Qué ocurre si una máquina tiene múltiples propietarios?**  
El sistema soporta propiedad compartida. Cada propietario tiene un porcentaje de participación sobre la utilidad de esa máquina, y el sistema calcula la participación de cada uno de forma proporcional. La suma de los porcentajes de propiedad de todos los socios de una máquina debe ser 100%.

---

**¿Cómo sé que el histórico es confiable?**  
El histórico muestra exclusivamente los períodos que fueron cerrados oficialmente con "Confirmar Cierre". Cada cierre guarda los montos exactos calculados en ese momento. Los datos no se recalculan retroactivamente — lo que se guardó al momento del cierre es lo que permanece, garantizando consistencia histórica.

---

**¿Puedo ver las ventas individuales de cada período?**  
Sí. En la pestaña "Negocio", la sección "Detalle de Ventas" permite expandir una tabla con cada transacción individual del período, incluyendo fecha, vendedor, paquete, máquina, precio y distribución calculada.

---

*Documento generado para uso interno de Inversiones Arcade. Para soporte técnico o modificaciones al módulo, contactar al equipo de desarrollo.*
