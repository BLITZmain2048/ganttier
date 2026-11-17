# Ganttier — aplicación dentro del proyecto canónico

La raíz del proyecto es `trabajos/ganttier/` y esta aplicación está en `02_trabajo/aplicacion/`. Consulta `../../04_notas/ORGANIZACION.md` para la organización general.

Los encargos nuevos del proyecto van a `../../02_trabajo/YYYY-MM-DD_slug/` respecto de esta carpeta y las entregas generales nuevas a `../../03_entregables/`. Los contratos CLI, pruebas y procesos de la aplicación conservan sus rutas internas cuando las exigen. En particular, `backend/`, `.venv/`, `datos/`, `docs/`, `_encargos/` y `trabajos/` siguen relativos a esta aplicación. Mantén el cwd y la configuración de datos que señale el contrato técnico.

Para una sesión limitada a esta aplicación, conserva sus temporales de pruebas en la ruta interna indicada por el contrato. La organización no modifica, migra ni sustituye la base de datos. No se ejecutan operaciones de usuario ni optimizaciones solo para comprobar una reubicación.

El acceso antiguo `Ganttier/` en la raíz compartida es compatibilidad. Para iniciar nuevos encargos mediante el puente, selecciona el proyecto `ganttier` y usa su raíz canónica.

## Contrato técnico conservado

# Ganttier: reglas para Codex

- Trabaja dentro de la carpeta autorizada; guarda entregables en `trabajos/`.
- Lee [referencia CLI](docs/cli.md) y [guía operativa](docs/codex_ganttier.md).
- Contrato vinculante: [CLI_SPEC](trabajos/cli_codex_20261019/CLI_SPEC.md), incluido §8.
- Invoca `../.venv/Scripts/python.exe -m ganttier.cli` desde `backend/` en Windows.
- En Linux usa el Python del entorno y el mismo módulo/directorio.
- Descubre comandos con `describir`; no inventes opciones ni rutas de API.
- Selecciona explícitamente `GANTTIER_DB`, `GANTTIER_DATA_DIR` y el proyecto.
- Para pruebas usa `trabajos/_temporales/`; nunca `datos/ganttier.db`.
- Stdout es un único JSON; conserva stderr y código de salida por separado.
- Mutaciones simuladas por defecto; no dejes `GANTTIER_CLI_ESCRITURA=1` persistente.
- Antes de optimizar: diagnosticar, comparar al menos tres variantes y revisar verificación.
- Aplica resultados solo dentro de la autorización del usuario, con respaldo y hash vigente.
- No uses `--sin-respaldo` ni `--forzar-bloqueo` como atajos de rutina.
- Tras aplicar, exige `programa verificar` con exit 0 y sin violaciones.
- Timeout/no encontrada no demuestra infactibilidad; factible no significa óptimo.
- Guarda plan, semillas, escenario, métricas y archivos laterales de la ganadora.
- Reanuda con el mismo escenario y JSONL; no cambies hashes o IDs para eludir controles.
- En trabajo compartido limita optimización a dos procesos y evita lotes simultáneos.
- No abras GUI, cierres procesos ajenos ni modifiques archivos de otros agentes.
- En la construcción CLI, respeta propietarios de §2 y entrega evidencia real según §6.
- Fallos y límites TST: [informe](trabajos/cli_codex_20261019/agentes/TST/INFORME.md).
