"""Carga del Gantt sin sumar la latencia de tres lecturas independientes."""
from concurrent.futures import ThreadPoolExecutor

from .model import ActivityArrays


def cargar(client, pid):
    """Se llama en el trabajador existente; no crea ni publica objetos de Qt.

    Los dos hilos auxiliares finalizan antes de devolver o propagar un error.
    ApiClient/httpx admite llamadas concurrentes sobre la misma instancia.
    """
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gantt-lectura") as pool:
        wbs = pool.submit(client.wbs, pid)
        relations = pool.submit(client.relations, pid)
        arrays = ActivityArrays(client.columnar(pid))
        return arrays, wbs.result(), relations.result()
