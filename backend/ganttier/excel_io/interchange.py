"""Escapes explícitos para conservar ausencia, nulos y listas al exportar.

Se aplican sólo a libros con la hoja de versión _Ganttier. Los valores editados
siguen pasando por las validaciones del importador; no se guarda una copia oculta.
"""
import json

MARKER = 'Ganttier intercambio 1'
ABSENT = r'\A'
NULL = r'\N'


def encode(record, field):
    if field not in record:
        return ABSENT
    value = record[field]
    if value is None:
        return NULL
    if isinstance(value, list):
        return r'\J' + json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and (value.startswith('\\') or not value.strip()):
        return '\\' + value
    return value


def decode(value):
    if value == ABSENT:
        return False, None
    if value == NULL:
        return True, None
    if isinstance(value, str) and value.startswith(r'\J'):
        result = json.loads(value[2:])
        if not isinstance(result, list) or not all(isinstance(v, str) for v in result):
            raise ValueError('Lista de habilidades inválida; se requieren textos.')
        return True, result
    if isinstance(value, str) and value.startswith('\\'):
        return True, value[1:]
    return True, value
