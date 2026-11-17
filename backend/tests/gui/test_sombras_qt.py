"""Regresión: un atributo Python con el nombre de un método de Qt lo «sobrescribe».

Ejemplo real: ``self.metric = QComboBox()`` en una página ocultó ``QPaintDevice.metric`` y la
aplicación se caía (falla de segmentación) al cambiar el tema. Esta prueba recorre todas las clases
Qt de la GUI y verifica que ningún ``self.<nombre> = ...`` coincida con un método de su clase base Qt.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

GUI = Path(__file__).resolve().parents[2] / "ganttier" / "gui"


def _qt_base(cls):
    """Primera clase de Qt (PySide6) en la jerarquía de ``cls``."""
    for base in inspect.getmro(cls):
        if base.__module__.startswith("PySide6"):
            return base
    return None


def _assigned_attributes(node: ast.ClassDef) -> dict[str, int]:
    names: dict[str, int] = {}
    for sub in ast.walk(node):
        targets = []
        if isinstance(sub, ast.Assign):
            targets = sub.targets
        elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)):
            targets = [sub.target]
        for t in targets:
            for el in (t.elts if isinstance(t, ast.Tuple) else [t]):
                if isinstance(el, ast.Attribute) and isinstance(el.value, ast.Name) and el.value.id == "self":
                    names.setdefault(el.attr, sub.lineno)
    return names


def test_ningun_atributo_oculta_metodos_de_qt():
    pytest.importorskip("PySide6")
    import ganttier.gui as gui_pkg
    bad = []
    for info in pkgutil.walk_packages(gui_pkg.__path__, "ganttier.gui."):
        module = importlib.import_module(info.name)
        source = Path(module.__file__)
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            cls = getattr(module, node.name, None)
            base = _qt_base(cls) if inspect.isclass(cls) else None
            if base is None:
                continue
            for name, line in _assigned_attributes(node).items():
                if not name.startswith("_") and callable(getattr(base, name, None)):
                    bad.append(f"{source.relative_to(GUI)}:{line}: {node.name}.{name} oculta {base.__name__}.{name}()")
    assert not bad, "Atributos que ocultan métodos de Qt (renómbralos):\n" + "\n".join(bad)
