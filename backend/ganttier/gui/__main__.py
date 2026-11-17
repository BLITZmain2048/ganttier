"""Punto de entrada: ``python -m ganttier.gui``."""
from . import arranque

arranque.mark("intérprete listo")
try:
    from . import precarga  # importa uvicorn/FastAPI/services en segundo plano mientras Qt se inicializa

    precarga.start()
except Exception:  # noqa: BLE001 - la precarga es sólo una optimización
    pass
from .app import main  # noqa: E402

arranque.mark("módulos de la interfaz importados")

if __name__ == "__main__":
    raise SystemExit(main())
