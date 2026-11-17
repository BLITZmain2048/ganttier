"""Extracción local acotada. QtPdf se aísla para no crear Qt en el hilo HTTP."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys


def extraer_paginas(path):
    path=Path(path)
    if path.stat().st_size>30*1024*1024: raise ValueError('El PDF supera 30 MB.')
    try:
        import pdfplumber
    except ImportError:
        pdfplumber=None
    if pdfplumber:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages)>100: raise ValueError('El PDF supera 100 páginas.')
            return [p.extract_text() or '' for p in pdf.pages], 'pdfplumber'
    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader=None
    if PdfReader:
        pdf=PdfReader(path)
        if len(pdf.pages)>100: raise ValueError('El PDF supera 100 páginas.')
        return [p.extract_text() or '' for p in pdf.pages], 'pypdf'
    # PySide6 ya forma parte de Ganttier. PDFium extrae filas de texto; el lector
    # exige una estructura comprobable y revisión, nunca afirma OCR de imágenes.
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
    try:
        if getattr(sys,'frozen',False):
            from tempfile import TemporaryDirectory
            with TemporaryDirectory(prefix='ganttier-pdf-') as folder:
                salida=Path(folder)/'paginas.json'
                result=subprocess.run([sys.executable,'--ganttier-extraer-pdf',str(path.resolve()),str(salida)],
                    capture_output=True,env=env,timeout=45,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode: raise ValueError('No se pudo extraer texto del PDF con QtPdf.')
                return json.loads(salida.read_text(encoding='utf-8')),'QtPdf/PDFium (respaldo local)'
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),str(path.resolve())],
            capture_output=True,text=True,encoding='utf-8',env=env,timeout=45,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode: raise ValueError('No se pudo extraer texto del PDF con las bibliotecas disponibles.')
        return json.loads(result.stdout),'QtPdf/PDFium (respaldo local)'
    except (OSError,subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        raise ValueError('Extracción PDF no disponible; use la plantilla Excel de inventario.') from exc


if __name__=='__main__':
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtPdf import QPdfDocument
    app=QGuiApplication([]); doc=QPdfDocument()
    error=doc.load(sys.argv[1])
    if error!=QPdfDocument.Error.None_ or not 0<doc.pageCount()<=100: sys.exit(2)
    print(json.dumps([doc.getAllText(i).text() for i in range(doc.pageCount())],ensure_ascii=False))
