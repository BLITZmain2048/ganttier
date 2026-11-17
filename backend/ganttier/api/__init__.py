"""API pública de Ganttier agrupada por recurso."""
from fastapi import APIRouter

from . import control, files, outages, planning, resources

router = APIRouter()
for module in (resources, planning, control, files, outages):
    router.include_router(module.router)

from .autoreports import router as autoreports_router
router.include_router(autoreports_router)

from .solve import router as solve_router
router.include_router(solve_router)

from .backups import router as backups_router
router.include_router(backups_router)

from .analysis import router as analysis_router
router.include_router(analysis_router)

from .insights import router as insights_router
router.include_router(insights_router)

from .tools import router as tools_router
router.include_router(tools_router)

from .extras import router as extras_router
router.include_router(extras_router)

from .importadores import router as importadores_router
router.include_router(importadores_router)

from .optimizacion import router as optimizacion_router
router.include_router(optimizacion_router)
router.include_router(__import__(__name__ + '.importacion_extra', fromlist=['install']).install(router))
