# Ganttier

Correcciones integradas del 12-10-2026: interfaz adaptable a escala, informes guardados en la carpeta elegida, tabla de Avance paginada e indicadores incrementales, accesibilidad acotada del Gantt y diagnóstico de cierre con minidumps. [Alcance, límites y autoprueba del ejecutable](docs/decisiones/integracion_20261012.md). Las mediciones y regresiones se registran en `trabajos/correcciones_20261012/INT_integracion/INFORME.md`.

**Importador asistido de archivos de cliente:** abra **Importar / Exportar → Importar archivo de cliente…** o búsquelo con **Ctrl+K**. Detecta **Matriz OM-WBS**, **Formulario E-2-MP**, **Caso JSON de proyecto** e **Inventario de equipos** (Excel/PDF). Permite editar perfiles, supuestos y vínculos de cantidad a tareas/rutas. Incluye plantilla Excel de inventario descargable; el PDF requiere revisión y confirmación específicas antes de importar. Crea un proyecto nuevo y conserva fuente, SHA-256 y trazabilidad. [Uso, API, límites e interfaz para nuevos formatos](docs/decisiones/importador_asistido.md).

El caso JSON conserva roles, equipos, dependencias y ausencias; propone roles indivisibles y declara la fecha base y la equivalencia bloque/día como **SUPUESTOS**. La aceptación por API reproduce **A27 = bloque 36 y costo de personal 10.420**, con verificación independiente. El inventario Chile Solar reproduce módulos, strings e hileras y su muestreo del **15 % redondeado por bloque**, con `strings_por_SCB=24` confirmado. Evidencia y regresión: `trabajos/ganttier_importadores_20261006/faseB/INFORME.md` en el espacio compartido.

Mantenimiento admite **equipos de trabajo y reparto por cantidad**, turnos propios, rotación del resto y equidad por ciclo. **Lote_Continuo_En_Turno=NO** por defecto permite continuar el lote en el siguiente bloque del mismo equipo, sin consumir descansos; SI conserva continuidad estricta. El ejemplo de 28.000 paneles/mes con dos equipos 7x7 resulta factible en 2027 con la regla nueva. Incluye Excel de ida y vuelta, GUI, API, informes y verificador independiente. [Reglas y límites](docs/decisiones/equipos_reparto.md).

Mejoras del 05-10-2026: informes de períodos futuros, frecuencia **DH (diaria hábil)**, intersección de ventanas macro y **roles indivisibles desde Generar programa y el asistente**, con tabla de personas por actividad. Incluye diagnósticos de capacidad y supuestos de mantenimiento. [Decisiones y límites](docs/decisiones/mejoras_20261005.md).

Modo opt-in de [roles indivisibles por actividad y bloques equivalentes](docs/decisiones/roles_indivisibles.md), con objetivo lexicográfico hito/costo, auditoría independiente y asignación nominal por API y vista en la GUI.

Planificador y controlador de programas de trabajo tipo Primavera P6, **aplicación de escritorio** (Python + PySide6/Qt), local y sin licencias. Tiene dos modos separados:

| Modo | Uso típico | Núcleo |
|---|---|---|
| **Proyecto** | Construcción de S/E, líneas y obras con WBS y dependencias | CPM (FS/SS/FF/SF + desfases + 8 tipos de restricción), ruta crítica y programación RCPSP con recursos (CP-SAT o heurística SGS multiinicio) |
| **Mantenimiento** | Planes preventivos de subestaciones, líneas y equipos (hasta 100 000 actividades) | Generador de ocurrencias periódicas y nivelación vectorizada con asignación de trabajadores y agrupación de desconexiones |

**Generar programa**: no se necesita entregar fechas. Basta la **lista de tareas** (duración, HH, habilidad, dotación, dependencias, ventanas o mes macro opcionales; en mantenimiento, las tareas periódicas). Ganttier construye un programa, lo **verifica de forma independiente** (calendarios, precedencias, capacidad por habilidad y por trabajador, ventanas duras, fecha de corte) y sólo lo aplica si es factible. Si no existe solución factible, informa qué lo impide (p. ej. habilidad sin recursos o ventana dura imposible).

Novedades de esta versión: página **Análisis y alertas** (alertas sin duplicados, pronósticos EAC, EVM por WBS/instalación/habilidad, diagnóstico de calidad del programa y cumplimiento preventivo), **edición en lote** y **vistas guardadas** del Gantt, **cadenas de relaciones** desde una lista, **plan semanal por trabajador** en Excel, **calculadora de días laborables**, **búsqueda en todos los proyectos** (Ctrl+Shift+F), **ausencias personales** por intervalo, **respaldos automáticos** verificados con restauración, migraciones de esquema, instancia única y API local con credencial. Detalle en `docs/manual_usuario.md` y en el informe `Mejoras_Aplicadas_Ganttier.pdf`.

Funciones comunes: programación **macro** por mes, **optimización con objetivo elegible** (nivelar carga, minimizar plazo, respetar ventanas macro, minimizar costo/HH extra o una mezcla ponderada), **control** con líneas base, avance, curvas S, EVM y *Earned Schedule*, **desempeño por trabajador**, **informes** semanales, mensuales y anuales en PDF, Excel, Word y HTML (manuales o automáticos) y **plantillas Excel** de importación/exportación.

## Inicio rápido (Windows)

**Gantt a Excel:** desde Gantt → Más, Importar / Exportar o Ctrl+K →
«Exportar Gantt a Excel…». Elige resumen por nivel WBS/sitio/equipo/habilidad o
carga por trabajador, escala día/semana/mes y rango. Incluye barras y mapa de calor
nativos, capacidad, hitos, corte y leyenda. [Alcance y límites](docs/decisiones/gantt_excel.md).

1. Instala Python 3.10 o superior desde python.org (marca *Add python.exe to PATH*).
2. Ejecuta `instalar.bat` (una sola vez): crea `.venv` e instala las dependencias, incluidas PySide6 y OR-Tools (opcional).
3. Ejecuta `iniciar.bat`: abre la **ventana de Ganttier** (sin navegador ni consola). Si algo falla, `iniciar_consola.bat` muestra el diagnóstico.
4. En **Inicio** elige **Nuevo desde lista de tareas** (asistente: pega tus tareas desde Excel y genera el programa) o **Explorar demo**. Ctrl+K abre la paleta de comandos y F1 la lista de atajos.

### Ejecutable (.exe) sin Python

En un equipo Windows con la instalación anterior, ejecuta `construir_exe.bat`. Se genera `dist\Ganttier\Ganttier.exe`; distribuye **la carpeta `dist\Ganttier` completa** (incluye `_internal`). El ejecutable guarda sus datos en `%LOCALAPPDATA%\Ganttier` (base de datos, informes y registro `ganttier.log`).

## Estructura

```
backend/ganttier/gui/  interfaz de escritorio PySide6 (ventana, páginas, Gantt virtualizado, servidor embebido)
backend/ganttier/      API REST local FastAPI + motores (engine, maintenance, control, reports, excel_io, solve)
backend/tests/         pruebas pytest: por módulo, e2e, solver, modos (proyecto, periódico, control, importar/exportar) y gui
plantillas/            Plantilla_Proyecto.xlsx y Plantilla_Mantenimiento.xlsx
ejemplos/              libros de ejemplo (S/E 220 kV y plan de mantenimiento)
docs/                  manual_usuario.md, plantillas.md, decisiones técnicas por módulo
datos/                 base SQLite, informes y corridas (se crea al usar)
frontend/              interfaz web anterior (en desuso; no se inicia)
```

La GUI usa la misma API REST que las pruebas: al abrirse levanta un servidor local embebido en `127.0.0.1` (puerto libre desde 8765) y la documentación de la API queda en `http://127.0.0.1:<puerto>/docs` (las llamadas requieren la credencial de sesión). La variable `GANTTIER_API_URL` permite conectar la GUI a un servidor externo.

## Pruebas

```
cd backend
..\.venv\Scripts\python -m pytest tests\modos tests\solver -q      :: los 4 modos + buscador de programas factibles
..\.venv\Scripts\python -m pytest tests\gui -q                    :: GUI (pytest-qt)
..\.venv\Scripts\python -m pytest -q                              :: todo (las de 100 000 actividades: -m slow)
```

## Notas técnicas

- Base de datos: SQLite en modo WAL (`datos/ganttier.db`). Respaldo automático diario verificado en `datos/respaldos` (también antes de migrar o importar en reemplazo); ver *Proyectos → Respaldos…*. `GANTTIER_DB` y `GANTTIER_DATA_DIR` permiten otras rutas.
- Esquema versionado: `backend/ganttier/migrations.py` aplica pasos N→N+1 (las bases nuevas recorren la misma cadena). Una base de una versión futura se rechaza sin tocarla.
- API local: cada inicio genera una credencial de sesión (cabecera `X-Ganttier-Token`) y solo se aceptan los Host 127.0.0.1/localhost. Para un servidor externo use `GANTTIER_API_TOKEN` y, si corresponde, `GANTTIER_ALLOWED_HOSTS`.
- Feriados de Chile: paquete `holidays` (incluye los feriados móviles) con tabla de respaldo interna para 2024-2030.
- **Fecha de corte**: se interpreta al final de la jornada (como la «fecha de estado» de MS Project): el avance informado al corte incluye ese día y el trabajo remanente se programa desde el día hábil siguiente. Un corte igual al inicio del proyecto significa «aún no comienza».
- Búsqueda de programas: construcción SGS serial y paralela con reglas LFT/MTS y mejora hacia adelante/atrás; CP-SAT (OR-Tools) mejora el incumbente hasta ~3000 actividades cuando su modelo cabe en el presupuesto; sobre ese tamaño, heurística vectorizada con numpy. El resultado nunca empeora el programa vigente para el objetivo elegido y siempre pasa por el verificador independiente (`engine/verify.py`).
