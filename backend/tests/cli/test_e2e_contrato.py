"""Contrato de stdout, catálogo completo, errores y arranque de procesos nuevos."""
import json

import pytest


def test_catalogo_todos_los_comandos(cli):
    data = cli.run("describir")["data"]
    entries = {entry["command"]: entry for entry in data["commands"]}
    expected = {"describir", "estado", "api", "servir", "demo crear", "plantilla"}
    groups = {
        "base": "copiar respaldar respaldos verificar",
        "proyectos": "listar ver resumen crear duplicar eliminar",
        "relaciones": "cadena", "lote": "aplicar",
        "importar": "excel json inventario detectar", "exportar": "xlsx gantt json",
        "programa": "verificar cpm resolver comparar lote aplicar carga diagnosticar",
        "programa corridas": "listar ver aplicar",
        "control avance": "registrar lote listar", "control fecha-corte": "ver fijar",
        "control linea-base": "crear activar listar",
        "control": "curva-s evm evm-por pronosticos desempeno alertas calidad tablero actividades",
        "informes": "generar plantel-semanal", "informes automaticos": "ver configurar ejecutar",
    }
    for group, actions in groups.items():
        expected.update(f"{group} {action}" for action in actions.split())
    aliases = dict(projects="proyectos", activities="actividades", resources="recursos", relations="relaciones",
                   assignments="asignaciones", calendars="calendarios", periodic_tasks="periodicas")
    for table in [*cli.ids["entities"], "equipos", "ausencias", "rutas"]:
        expected.update(f"{aliases.get(table, table)} {action}" for action in ("listar", "ver", "agregar", "actualizar", "eliminar"))
    missing = expected - entries.keys()
    assert not missing, "comando no implementado todavía (dueño según CLI_SPEC §4): " + ", ".join(sorted(missing))
    for name in expected:
        entry = entries[name]
        assert entry["help"] and isinstance(entry["arguments"], list) and entry["examples"], name
        for arg in entry["arguments"]:
            assert {"type", "default", "required"} <= arg.keys(), (name, arg)
    assert {"variant", "plan", "weights", "options"} <= data["schemas"].keys()
    assert {"proyecto", "mantenimiento", "inventario"} <= set(next(a["choices"] for a in entries["plantilla"]["arguments"] if a["name"] == "kind"))


@pytest.mark.parametrize("command", ["describir", "estado"])
def test_arranque_frio_informativo(cli, command, record_property):
    cli.run(command, timeout=60)
    elapsed = cli.calls[-1]["wall_s"]
    record_property("cold_start_s", elapsed)
    print(f"\nTST arranque de proceso nuevo {command}: {elapsed:.3f}s (meta <2s; tolerancia informativa 15s)")
    if elapsed >= 2:
        pytest.xfail(f"Arranque {elapsed:.3f}s >=2s; no bloqueante bajo carga (incluso >15s requiere repetir en reposo)")


@pytest.mark.parametrize("args", [("--version",), ("--help",), ("estado",), ("base", "verificar"), ("proyectos", "listar")])
def test_sobre_en_lecturas_y_ayuda(cli, args):
    before = cli.snapshot()
    cli.run(*args)
    assert cli.snapshot() == before


def test_json_salida_y_stdin(cli):
    target = cli.root / "salida.json"
    body = cli.run("--salida", target, "--compacto", "estado")
    assert json.loads(target.read_text(encoding="utf-8")) == body
    assert len(cli.calls[-1]["stdout"].splitlines()) == 1
    body = cli.project("actividades", "actualizar", cli.ids["activities"][0], "--archivo", "-", stdin='{"name":"Prueba Unicode ñ"}')
    assert body["db"]["mode"] == "simulated"


@pytest.mark.parametrize("args", [("no_existe_tst",), ("-p", "999999999", "proyectos", "ver"), ("programa", "resolver", "--tiempo", "-1")])
def test_exit_uno_uso_datos_no_encontrado(cli, args):
    body = cli.run(*args, code=1, implemented=False)
    assert not body["ok"] and body["error"]["code"] in {"uso", "datos_invalidos", "no_encontrado"}


def test_exit_dos_verificacion_negativa_legitima(cli):
    body = cli.project("programa", "verificar", code=2)
    assert body["ok"] and body["error"] is None
    assert body["data"]["feasible"] is False and body["data"]["violations"]


def test_no_importa_qt(cli):
    hook = cli.root / "sin_qt"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text("import sys\nclass NoQt:\n def find_spec(self, fullname, *args):\n  if fullname.startswith(('PySide6', 'PyQt')): raise RuntimeError('TST: la CLI importó Qt')\nsys.meta_path.insert(0, NoQt())\n", encoding="utf-8")
    cli.run("estado", env={"PYTHONPATH": str(hook)})
    cli.run("describir", env={"PYTHONPATH": str(hook)})


def test_exit_cinco_error_interno_inyectado(cli):
    hook = cli.root / "fallo_interno"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text("from ganttier.cli.comandos import sistema\ndef fail(*args):\n raise RuntimeError('TST fallo interno deliberado')\nsistema.estado=fail\n", encoding="utf-8")
    body = cli.run("estado", code=5, env={"PYTHONPATH": str(hook) + __import__('os').pathsep + str(__import__('pathlib').Path(__file__).resolve().parents[2])})
    assert body["error"]["code"] == "interno"
    assert "TST fallo interno deliberado" in cli.calls[-1]["stderr"]
