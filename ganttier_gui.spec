# -*- mode: python ; coding: utf-8 -*-
"""Construir con .venv\Scripts\python.exe -m PyInstaller ganttier_gui.spec."""
import importlib.util
import os
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

# La resolución PE de PyInstaller consulta PATH. Qt 6.11 usa la ICU de Windows:
# un icuuc.dll homónimo de Poppler/Conda no tiene sus mismos símbolos y hace
# fallar QtCore al arrancar el exe. Respetar primero el directorio del sistema,
# como hace el cargador de Windows, antes de herramientas ajenas al proyecto.
if sys.platform == "win32":
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    os.environ["PATH"] = str(system32) + os.pathsep + os.environ.get("PATH", "")

root = Path(SPECPATH).resolve()
sys.path.insert(0, str(root / "backend"))
os.environ["QT_API"] = "pyside6"
build_dir = Path(os.environ.get("GANTTIER_BUILD_ASSETS", str(root / "build" / "gui_assets")))
build_dir.mkdir(parents=True, exist_ok=True)
icon = root / "ganttier.ico"
if not icon.exists():
    from PIL import Image, ImageDraw
    # Usa la misma paleta del tema sin iniciar QApplication.
    from ganttier.gui.theme import tokens
    t = tokens()
    icon = build_dir / "ganttier.ico"
    art = Image.new("RGBA", (256, 256))
    painter = ImageDraw.Draw(art)
    for rect, color in [((16, 32, 152, 76), t.accent2),
                        ((80, 108, 232, 152), t.datadate),
                        ((48, 184, 152, 228), t.today)]:
        painter.rounded_rectangle(rect, radius=12, fill=color)
    art.save(icon, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

datas = [(str(root / "backend" / "ganttier" / "schema.sql"), "ganttier")]
# Íconos vectoriales (Lucide, ISC) de la GUI.
for svg in sorted((root / "backend" / "ganttier" / "gui" / "icons").glob("*")):
    datas.append((str(svg), "ganttier/gui/icons"))
for folder in ("plantillas", "ejemplos"):
    for book in sorted((root / folder).glob("*.xlsx")):
        datas.append((str(book), folder))
datas += collect_data_files("holidays")
hiddenimports = collect_submodules("ganttier") + collect_submodules("holidays")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += ["PySide6.QtSvg", "PySide6.QtPdf", "matplotlib.backends.backend_qtagg", "matplotlib.backends.backend_agg", "openpyxl", "reportlab", "docx"]
binaries = []
# Los hooks analizan los imports estáticos. collect_all copiaba además todos los
# .py como datos y recogía herramientas ajenas a los formatos usados por Ganttier.
for package in ("openpyxl", "reportlab", "docx"):
    datas += collect_data_files(package, excludes=["**/tests/**", "**/test/**"])
if importlib.util.find_spec("ortools") is not None:
    # Única API usada: CP-SAT. Conservar sus DLL compartidas y pandas, requerido
    # por cp_model en OR-Tools 9.15 aunque Ganttier no importe pandas directamente.
    hiddenimports += ["ortools.sat.python.cp_model", "ortools.util.python.sorted_interval_list"]
    binaries += collect_dynamic_libs("ortools")
for package in ("pypdf", "pdfplumber"):
    if importlib.util.find_spec(package) is not None:
        hiddenimports += collect_submodules(package)

excludes = [
    "PyQt5", "PyQt6", "PySide2", "tkinter", "pytest", "_pytest", "pytestqt",
    "numpy.tests", "pandas.tests", "matplotlib.tests", "openpyxl.tests",
    "scipy", "IPython", "jupyter", "notebook",
    "ortools.algorithms", "ortools.constraint_solver", "ortools.graph",
    "ortools.linear_solver", "ortools.math_opt", "ortools.pdlp", "ortools.scheduling",
]
# No hay imports ni usos de estas familias Qt en backend/ganttier. QtPdf se
# conserva explícitamente, así como QtSvg/Widgets/PrintSupport usados por gráficos.
excludes += ["PySide6." + name for name in (
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebView",
    "QtMultimedia", "QtMultimediaWidgets", "QtQml", "QtQuick", "QtQuickWidgets",
    "QtQuickControls2", "QtDesigner", "QtHelp", "QtTest", "QtPdfWidgets",
)]

a = Analysis(
    [str(root / "backend" / "ganttier" / "gui" / "launcher.py")],
    pathex=[str(root / "backend")], binaries=binaries, datas=datas,
    hiddenimports=sorted(set(hiddenimports)), hookspath=[], runtime_hooks=[],
    hooksconfig={"matplotlib": {"backends": ["QtAgg", "Agg"]}},
    excludes=excludes, noarchive=False,
)
# Se producen PNG con Agg y lienzos QtAgg: conservar TTF, iconos y estilos;
# no se usan TeX ni fuentes AFM de los backends PostScript/PDF de matplotlib.
a.datas = [entry for entry in a.datas if not any(part in entry[0].replace("\\", "/") for part in (
    "matplotlib/mpl-data/sample_data/", "matplotlib/mpl-data/fonts/afm/",
    "matplotlib/mpl-data/fonts/pdfcorefonts/",
))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Ganttier", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, icon=str(icon))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Ganttier")
