# Correcciones A4 de respaldos y servidor

Fecha: 04-10-2026. Alcance: A4-01, A4-02, A4-03, A4-08 en `backups.py`; A4-07 y A4-15 en `gui/server.py`.

- **A4-01:** toda consulta, cancelación y aplicación exige que el destino del marcador coincida con la ruta canónica de la base. Se conserva el marcador histórico; si pertenece a otra base, se usa un nombre derivado de la ruta del nuevo destino. Dos bases pueden mantener restauraciones pendientes independientes. Se conservan firmas y claves consumidas por la API y el diálogo.
- **A4-02:** cada copia conserva tamaño y SHA-256 en su manifiesto. Al programar y aplicar se comprueban tamaño positivo, coincidencia con el manifiesto, tabla `projects`, versión reconocida en `meta.schema_version` o, como alternativa, `user_version`, integridad SQLite y referencias foráneas. Los manifiestos antiguos sin hash siguen admitidos si superan las demás comprobaciones. Los rechazos de validación producen `ValueError` con explicación en español. Las rutas SQLite se codifican como URI para admitir caracteres como `#`.
- **A4-03:** la retención protege las copias mencionadas en todos los marcadores pendientes. La restauración copia y verifica primero el origen en un temporal exclusivo junto al destino; recién después crea el respaldo previo y ejecuta retención. Verifica nuevamente el temporal antes del reemplazo y lo limpia también ante errores.
- **A4-08:** UUID en nombres finales y apertura exclusiva de temporales evitan compartir destinos entre respaldos del mismo segundo.
- **A4-07:** si vence el plazo de cierre, el socket y QLockFile permanecen bajo custodia hasta que el hilo termine. Un vigilante espera su salida y libera los recursos con exclusión mutua; iniciar de nuevo el mismo servidor mientras sigue activo se rechaza.
- **A4-15:** se crea primero la carpeta de la base. Los fallos de carpeta, permisos o bloqueo desconocido conservan su causa; únicamente la contención del bloqueo informa otra instancia abierta.

## Verificación

Antes de corregir: **7 reproducciones fallidas**, correspondientes a los seis defectos (A4-03 tiene dos reproducciones).

Después de corregir:

- **28 regresiones nuevas** en `backend/tests/core/test_correcciones_a4_respaldos.py`, incluidas en la suite core. Las pruebas del servidor usan `pytest.importorskip("PySide6")`.
- **12 pruebas aprobadas**: todo `test_a4_backups.py`, la reproducción de nombres concurrentes y las dos reproducciones de servidor solicitadas, sin modificar `_encargos/auditoria_tests/`.
- **130 pruebas aprobadas**: `tests/core` completo y `tests/gui/test_plataforma.py` / `test_ux3.py`. Se anuló el filtro predeterminado con `-o addopts=` para incluir también la prueba lenta de 100 000 actividades.

Total: **142 pruebas distintas aprobadas**, sin fallos ni omisiones. Se ejecutaron desde `backend` con `..\.venv\Scripts\python.exe -B`, `QT_QPA_PLATFORM=offscreen`, sin caché de pytest. Bases, archivos temporales, datos de pruebas y preferencias Qt se dirigieron a `trabajos/a4_respaldos_*`, dentro de la carpeta compartida y fuera de `backend/`. Queda una advertencia de deprecación del transporte de Starlette/TestClient, ajena a estas correcciones.
