# Pauta de mantenimiento por equipo

Formato `pauta_equipos`, adaptador `AdaptadorPauta`. Solo XLSX; CSV no representa el contrato multitabla y no se anuncia como extensión compatible. No se modifica el generador ni el esquema SQLite.

## Tablas

A: cada fila contiene Instalación/Planta, Equipo/TAG, Tarea y Frecuencia. B: hoja Pautas con Tipo de equipo, Tarea, Frecuencia y hoja Equipos con TAG, Tipo de equipo e Instalación. El producto se hace por tipo normalizado, no entre todos los tipos. Los TAG se identifican por instalación; repetir equipo × tarea es error. Las filas con errores se excluyen del borrador y bloquean su confirmación.

Opcionales: HH totales, Cuadrilla entera, Habilidad, Requiere desconexión (SI/NO), Duración (h/d), Última ejecución, Prioridad 0–100, Tensión en kV, Notas. La duración se redondea hacia arriba a días laborables; las HH no se multiplican por dotación. Un número sin unidad en Duración representa días. Fecha de puesta en servicio en Equipos inicia el primer ciclo cuando no hay última ejecución.

Personal opcional: Nombre y Habilidades separadas por punto y coma o coma. No se infiere ubicación exclusiva de un trabajador: el modelo nativo permite compartir recursos por habilidad entre instalaciones. Sin Personal se proponen personas ficticias por instalación/habilidad: máximo entre cuadrilla requerida y techo de HH anuales/capacidad anual. Es un mínimo de capacidad anual, no garantía de factibilidad dentro de cada ventana. El solver debe diagnosticarlo.

## Frecuencias y anclas

Diaria, diario hábil, semanal, mensual, trimestral, semestral y anual usan D, DH, W, M, Q, S y A. Cada N días/semanas/meses/años usa ND/NW/NM/NA con interval=N. Bienal=NA/2; 5 años=NA/5. No se aplica interval a M ni A.

Última ejecución avanza un ciclo antes de programar. El día original del mes se conserva: 31/01 → 28/02 → 31/03. En DH el generador descarta fechas no laborables. Sin fechas se propone el inicio del horizonte; `ancla` permite modificarlo. Los vencimientos anteriores al horizonte se filtran manteniendo la fase. No se importa avance real a partir de última ejecución.

## Perfiles, supuestos y trazabilidad

Perfil.hoja y fila_encabezado seleccionan la tabla. Mapeo: `formato` A/B, `columnas` (campo interno → letra Excel), `hoja_equipos`, `fila_equipos`, `columnas_equipos`, y equivalentes `hoja_personal`, `fila_personal`, `columnas_personal`. Campos internos: location, equipment, type, name, frequency, work_hours, crew_size, required_skill, requires_outage, duration, last, commissioned, priority, notes, voltage_kv, person, skills. La detección automática examina las primeras 50 filas y no depende del orden ni de acentos/mayúsculas.

SUPUESTOS editables: fecha_inicio/fecha_fin (2027 por defecto), calendario, moneda, hh, dotacion, habilidad, desconexion, prioridad, tolerancia_dias, ancla, duracion (días), recursos. `duracion=null` deriva techo(HH/cuadrilla/jornada). Las ventanas son blandas. Los costos cero significan desconocidos. No representan autorización de desconexión. `faltantes` declara los valores inferidos; también se conservan en descripción y metadatos de importación. El flujo común exige aceptación expresa.

Cada maestra conserva hoja/fila/celdas y, en B, el origen de su equipo; notas, tipo y SHA-256 de la fuente quedan en el borrador persistido. Confirmar utiliza los servicios nativos y el importador interno validado. Las notas de actividades reciben trazabilidad por el servicio común.

## Límites

Lectura secuencial; máximo 30 MB comprimidos, 150 MB descomprimidos, 100 hojas, 200 columnas y 200.000 filas de datos por tabla. Celdas combinadas en tablas son error; no se rellenan implícitamente. Fórmulas se leen únicamente de su caché, nunca se ejecutan; sin caché se informa hoja/fila/columna. Fechas y números reutilizan la conversión del importador nativo (coma decimal y fechas chilenas).

Se cuentan vencimientos antes de crear ocurrencias nativas, con caché por frecuencia/ancla. La previsualización advierte cantidad prevista y bloquea el primer exceso de 100.000 ocurrencias o maestras. Ante exceso informa un límite inferior y conserva solo un prefijo diagnóstico; no se puede confirmar ese prefijo. La conversión a plantilla interna puede ser más lenta que la previsualización.

El lector común y el registro de servicio tienen conflictos de integración documentados en `agentes/I3/CONFLICTOS.md`; no se modifican desde I3. El objetivo ≤3 s para 20.000 filas debe evaluarse con las mediciones del INFORME, sin asumir cumplimiento por diseño.

## Reproducción

Desde app: `python -B ejemplos/generar_formato_pauta.py`. Genera dos plantillas con comentarios/listas e instrucciones, más tres ejemplos ficticios:

| Archivo | Equipos | Maestras | Ocurrencias 2027 |
|---|---:|---:|---:|
| pauta_1se_40.xlsx | 40 | 80 | 2.600 |
| pauta_5se_800.xlsx | 800 en 5 SS/EE | 1.600 | 52.000 |
| pauta_B_12x300.xlsx | 3.600 (300 por tipo) | 7.200 | 46.800 |

A usa semanal + mensual: 53 + 12 vencimientos desde 01/01/2027. B usa mensual + anual: 12 + 1, con 24 filas de pautas. Las fechas y datos son deterministas; el ZIP puede tener marcas de creación diferentes. Pruebas: `python -B -m pytest tests/importadores/test_pauta_equipos.py -q -s -p no:cacheprovider --basetemp <carpeta nueva dentro del proyecto>`.
