"""Contrato v1 de lectores externos. Independiente de HTTP, Qt y SQLite."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Protocol
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field


class Perfil(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,79}$')
    nombre: str = Field(min_length=1, max_length=160)
    formato: str = Field(min_length=1, max_length=80)
    version: int = Field(default=1, ge=1)
    esquema: int = Field(default=1, ge=1, le=1)
    hoja: str = ''
    fila_encabezado: int = Field(default=1, ge=1, le=1000)
    mapeo: dict[str, Any] = Field(default_factory=dict)
    supuestos: dict[str, Any] = Field(default_factory=dict)


@dataclass
class Lectura:
    """Filas normalizadas por un lector; cada ítem conserva su origen físico."""
    perfil: Perfil
    items: list[dict] = field(default_factory=list)
    sitios: list[dict] = field(default_factory=list)
    wbs: list[dict] = field(default_factory=list)
    advertencias: list[dict] = field(default_factory=list)
    excluidos: list[dict] = field(default_factory=list)
    vista_previa: list[dict] = field(default_factory=list)
    sugeridos: dict = field(default_factory=dict)
    totales: dict = field(default_factory=dict)


@dataclass
class Borrador:
    perfil: Perfil
    fuente: dict
    maestras: list[dict]
    sitios: list[dict]
    wbs: list[dict]
    advertencias: list[dict]
    faltantes: list[dict]
    supuestos: dict
    resumen: dict
    excluidos: list[dict] = field(default_factory=list)
    vista_previa: list[dict] = field(default_factory=list)
    esquema: int = 1
    modo: str = 'mantenimiento'
    modelo: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        result = asdict(self)
        result['perfil'] = self.perfil.model_dump(mode='json')
        return result

    @property
    def tiene_errores(self):
        return any(a.get('nivel') == 'error' for a in self.advertencias)


class Lector(Protocol):
    """Fase B puede registrar otro lector sin modificar API ni asistente."""
    formato: str
    def detectar(self, path: Path) -> list[dict]: ...
    def leer(self, path: Path, perfil: Perfil) -> Lectura: ...
    def construir(self, lectura: Lectura, supuestos: dict) -> list[dict]: ...


class Adaptador(Protocol):
    """Interfaz alternativa completa para formatos de proyecto o inventario (fase B).

    ``convertir`` devuelve domain.ImportBundle validado. Solo se registra código
    instalado por el programa; un perfil JSON nunca importa ni ejecuta módulos.
    """
    formato: str
    extensiones: set[str]
    def detectar(self, path: Path) -> list[dict]: ...
    def previsualizar(self, path: Path, perfil: Perfil, supuestos: dict) -> Borrador: ...
    def convertir(self, borrador: Borrador, nombre: str | None): ...
