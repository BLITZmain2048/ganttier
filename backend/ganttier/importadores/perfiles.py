"""Perfiles incorporados y perfiles de usuario con revisiones atómicas."""
from pathlib import Path
import json
import threading
from uuid import uuid4
from .modelos import Perfil

INCORPORADOS = (
    Perfil(id='matriz-om-wbs', nombre='Matriz OM-WBS', formato='om_wbs'),
    Perfil(id='formulario-e2-mp', nombre='Formulario E-2-MP', formato='e2_mp'),
    Perfil(id='caso-json-proyecto', nombre='Caso JSON de proyecto', formato='caso_json'),
    Perfil(id='inventario-equipos', nombre='Inventario de equipos', formato='inventario_equipos'),
)
_LOCK = threading.RLock()


class AlmacenPerfiles:
    def __init__(self, directorio: Path):
        self.directorio = Path(directorio) / 'perfiles_importacion'

    def _path(self, ident: str):
        # La validación impide rutas, puntos y separadores introducidos por HTTP.
        Perfil(id=ident, nombre='Validación', formato='validacion')
        return self.directorio / (ident + '.json')

    def listar(self):
        profiles = [p.model_copy(deep=True) for p in INCORPORADOS]
        with _LOCK:
            for path in sorted(self.directorio.glob('*.json')):
                profiles.append(Perfil.model_validate_json(path.read_text(encoding='utf-8')))
        return profiles

    def obtener(self, ident):
        self._path(ident)
        for profile in self.listar():
            if profile.id == ident:
                return profile
        raise LookupError('No existe el perfil solicitado.')

    def guardar(self, perfil: Perfil):
        if perfil.id in {p.id for p in INCORPORADOS}:
            raise ValueError('Guarde una copia con otro identificador; los perfiles incorporados son de solo lectura.')
        path = self._path(perfil.id)
        with _LOCK:
            previous = Perfil.model_validate_json(path.read_text(encoding='utf-8')) if path.exists() else None
            if previous and previous.version != perfil.version:
                raise ValueError('El perfil cambió. Recárguelo antes de guardar.')
            saved = perfil.model_copy(deep=True)
            saved.version = previous.version + 1 if previous else 1
            self.directorio.mkdir(parents=True, exist_ok=True)
            if previous:
                history = self.directorio / 'historial' / saved.id
                history.mkdir(parents=True, exist_ok=True)
                (history / f'{previous.version}.json').write_text(previous.model_dump_json(indent=2), encoding='utf-8')
            temporary = self.directorio / (uuid4().hex + '.tmp')
            try:
                temporary.write_text(saved.model_dump_json(indent=2), encoding='utf-8')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return saved

    def borrar(self, ident):
        if ident in {p.id for p in INCORPORADOS}:
            raise ValueError('No se puede borrar un perfil incorporado.')
        with _LOCK:
            path = self._path(ident)
            if not path.exists():
                raise LookupError('No existe el perfil solicitado.')
            path.unlink()
