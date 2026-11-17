# O1 — Rendimiento de análisis con 100 000 actividades

Medido el 04-10-2026 con `.venv/Scripts/python.exe`, SQLite local y TestClient.
Demo `POST /api/demo/mantenimiento?n=100000`, corte original 01-01-2026.
Los tiempos HTTP incluyen lectura, cálculo, serialización y compresión; sin cProfile.

| GET /api/projects/{pid}/… | Antes (s) | Después (s) | Objetivo (s) |
| --- | ---: | ---: | ---: |
| alerts | 8,228 | 2,041 | 3,0 |
| control/quality | 4,187 | 1,403 | 2,0 |
| control/evm-by | 3,172 | 1,129 | 2,0 |
| maintenance/kpis | 2,942 | 0,069 | 1,5 |
| control/evm (control, sin cambios) | 0,751 | 0,767 | — |

cProfile previo de alertas: 3 construcciones de vistas, 327 170 decodificaciones
y 100 000 `replace`; las lecturas tipadas acumularon 5,68 s bajo el perfilador.
Después: una construcción de vistas y cero copias `replace` de actividades.

- Alertas comparte `control_inputs`, vistas y progreso con EVM, calidad y KPI.
- Calidad lee solo los campos consumidos, recibe porcentajes sin copiar fichas
  y omite nodos aislados al buscar ciclos; conserva las fechas reales originales.
- EVM por grupo lee solo las etiquetas necesarias; conserva cada llamada a `evm`
  y la conciliación independiente, sin cambiar sumas ni orden numérico.
- KPI filtra compromisos por período en SQL y une la tolerancia por proyecto;
  conserva `fsum`, orden, historial al corte y distinción entre fin real e inferido.
  En esta demo solo 277 ocurrencias pertenecen al período inicial.
- Se mantiene `UPDATE alerts SET state=state WHERE 0` antes de cualquier lectura,
  junto con reconocimiento, resolución, reactivación e idempotencia. Sin caché global.

Las cinco respuestas completas coinciden con la ruta anterior a igual fecha.
Verificación final: 321 pruebas pasadas (3 slow excluidas); prueba slow O1: 1 pasada.
Las referencias antiguas están congeladas en `test_rendimiento_analisis.py`:
ambos modos, 4 alcances de línea base, 3 bases EVM, 3 agrupaciones y varios cortes.
Prueba `slow` con demo nueva: alertas 1,704 s; calidad 1,472 s; KPI 0,069 s;
EVM por grupo 1,153 s. Los límites de aceptación automatizados tienen holgura 2x.
Comando de suites desde `backend`: `../.venv/Scripts/python.exe -B -m pytest tests/control tests/maintenance tests/modos --basetemp=../trabajos/rendimiento_O1/final-tmp -o cache_dir=../trabajos/rendimiento_O1/pytest-cache -q`.
Para rendimiento: mismo Python y opciones de temporales, `-m pytest tests/control/test_rendimiento_analisis.py -m slow -o addopts= -q -s`.
Archivos: `control/{alerts,schedule_quality,aggregation}.py`, `maintenance/kpis.py`,
`api/analysis.py`, la prueba indicada y esta decisión. Temporales en `trabajos/rendimiento_O1/`.
