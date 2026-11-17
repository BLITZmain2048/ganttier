# Gantt nativo en Excel (P5-07)

Fecha: 05-10-2026. `excel_io/gantt_xlsx.py`, integración localizada en `api/files.py`
y diálogo independiente `gui/gantt_export.py`. No modifica el repositorio, el
generador, los informes ni el panel de solve. Amplía P5-07 con resumen y personas.

## Contrato

`GET /api/projects/{pid}/gantt.xlsx?mode=nivel&group_by=wbs:2&scale=week&from=2026-10-01&to=2026-12-31&critical=true`

Opcionales: mode=nivel, group_by=wbs, scale=week, rango del programa y critical=false.
Modos: `nivel|trabajador`; escalas: `day|week|month` (también mes para personas).
Agrupación: `wbs`, `wbs:1`…`wbs:99`, `sitio`, `instalacion`, `equipo`, `habilidad`;
combinaciones como `sitio/wbs:2` o `equipo/wbs`. Sitio e instalación usan `location`.
La jerarquía sigue `parent_id`, no puntos del código. Sin nodo: «Sin WBS».
Un nivel superior a la profundidad disponible conserva el último nodo.

Fechas de consulta ISO AAAA-MM-DD; GUI y Excel DD-MM-AAAA. Parámetros inválidos,
rangos invertidos o excesivos: 422 en español. Proyecto inexistente: 404.
Los temporales se eliminan después de la descarga. Interfaz Python:
`export_gantt(conn, pid, path, Options(...))`, conexión con filas sqlite3.Row.

## Significado de los datos

- **Resumen por nivel:** envolvente de `planned_start/finish`, con respaldo
  `early_start/finish`. Son fechas del programa vigente, no fechas ejecutadas
  inventadas. Agrupa el programa completo antes de recortar la vista; conserva
  extremos completos y muestra ◀/▶ al continuar fuera del rango. Una barra de
  grupo no implica ocupación continua. Actividades sin fechas se cuentan en Leyenda.
- Avance: media ponderada por HH; sin HH, por duración (hito: peso 1). Usa avance
  registrado actual, sin reconstruir un corte histórico. Los avances nulos no se
  convierten en cero; si todos faltan, la celda queda vacía. ◆ cuenta hitos del período,
  incluso en días no laborables. Rojo opcional: grupo con alguna actividad crítica.
- **Por trabajador:** una fila por ID tipo `trabajador`; homónimos separados.
  Carga explícita de asignaciones, sin inventar personas para demandas por habilidad.
  Equipos/cuadrillas no se convierten en personas. La leyenda declara las limitaciones.
- Con roles indivisibles aplicados se usa `role_schedules.nominal_assignments`;
  se rechazan asignaciones obsoletas. Las fechas equivalentes de bloques se conservan
  como están en el programa; no se reinterpretan como una agenda horaria.
- HH explícitas uniformes sobre días laborables de la actividad; sin HH, unidades
  por horas diarias del calendario. Recortar no redistribuye las HH originales.
- **Capacidad:** mínimo de horas del calendario personal y capacidad diaria,
  multiplicado por unidades. Respeta excepciones parciales, turnos y feriados
  efectivos; recurso inactivo: cero. Verde: carga positiva inferior a 80 %;
  naranja: desde 80 %; rojo: sobre capacidad (tolerancia 0,000001 HH), también
  cuando hay carga y capacidad cero. Reglas nativas por rango, sin imágenes.
- Totales SUM por persona y por período; **Totales por período** reúne todas las
  hojas. La capacidad libre ajena no borra una celda roja individual. Un total
  agregado no constituye una auditoría de sobrecarga individual.
- **Calendario:** día, horas y motivo de feriado/excepción/descanso. En resumen
  diario las barras se interrumpen en días sin horas del calendario del proyecto;
  semana/mes se muestra si contiene algún día laborable. Encabezados diarios grises:
  fin de semana o no laborable. Un turno puede trabajar el sábado. La carga personal
  se calcula con los calendarios correspondientes, no con el color del encabezado.
- **Leyenda:** parámetros, límites y definiciones. Línea naranja en el período que
  contiene el corte. Si el corte está fuera del rango no se dibuja una línea ficticia.

## Límites y escritura

Máximo **500 filas de resumen**. Si hay más grupos, se ordenan por nombre y se
reúnen en bloques contiguos de tamaño `ceil(n/500)`; etiqueta con primero, último
y cantidad. Conserva extremos, conteos, avance ponderado e hitos; Leyenda declara
el agrupamiento. Nunca se crea una matriz de 100.000 actividades por fechas.

Personas nunca fusionadas: parejas de hojas carga/capacidad de hasta 500 filas,
más totales globales. Máximo **520 períodos**, rango hasta **20 años**, fechas
entre 1900 y 9998. Si se excede, hay que acotar o elegir otra escala; no se cambia
la escala silenciosamente. Semanas lunes-domingo, meses naturales; períodos
extremos parciales según el rango solicitado.

Libro `write_only`, estilos compartidos y XML temporal junto al destino. Transacción
de lectura consistente, sin finalizar transacciones preexistentes del llamante. Cursor SQL
para actividades, agregación antes de escribir y diferencias vectoriales por
persona/calendario para cargas. La ruta de roles usa el contrato nominal existente,
que materializa datos del proyecto. Escritura atómica: preserva destino anterior
ante fallos. Totales sin caché hasta abrir/recalcular con Excel o LibreOffice.
El libro es una instantánea; editarlo no reprograma ni reimporta Ganttier.

## Comparación con API y áreas reservadas

Pruebas contra `/activities` y `/load` para día/semana/mes. Capacidad ordinaria
también contrastada. Diferencia explícita: `services.load_profile` usa disponibilidad
booleana y puede ignorar excepciones parciales; este exportador conserva sus horas.
Ese servicio también agrupa por nombre y puede fusionar homónimos o presentar
demanda por habilidad. La exportación nominal conserva IDs. Una futura mejora
de `/load` podría exponer ID/tipo y horas efectivas; no se modificó services.py.

## Pruebas

Desde `Ganttier/backend`, fijando temporales dentro de la carpeta compartida:

```powershell
..\.venv\Scripts\python -m pytest tests/excel_io/test_gantt_xlsx.py tests/gui/test_gantt_export.py -q
..\.venv\Scripts\python -m pytest tests/excel_io/test_gantt_xlsx.py -m slow -o addopts= -q
```

El caso slow tiene 100.000 actividades/asignaciones, comprueba ambos modos,
agregación, conteos y 3.200.000 HH. Límite: 60 s para ambas exportaciones, excluida
la construcción del fixture. Mediciones, regresión por directorio con timeout,
hashes y revisión visual: `trabajos/ganttier_ejemplos_20261005/gantt_excel/INFORME.md`.
