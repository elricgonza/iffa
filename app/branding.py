"""Identidad institucional (nombre y logo): la usan la interfaz y los reportes.

Archivos en app/static/img/:
  logo.png         original (color, fondo transparente)  -> reportes en papel / PDF
  logo-sm.png      color, recortado                       -> encabezados sobre fondo claro
  logo-blanco.png  silueta blanca                         -> sobre fondos oscuros (menú lateral)
  favicon.png      64x64
"""
import base64
import os

from flask import current_app

SIGLA = "IFALFA"
INSTITUCION = "Instituto de Formación Artística"
NOMBRE = "Luis Felipe Arce"
SISTEMA = "Sistema de Gestión Académica"
RESOLUCIONES = "R.M. 685/2016 · R.M. 845/1993"   # figuran en el encabezado de los reportes
CIUDAD = "La Paz – Bolivia"
LEMA = "Trabajando por la formación musical desde 1975"

_VARIANTES = {"original": "logo.png", "color": "logo-sm.png", "blanco": "logo-blanco.png", "favicon": "favicon.png"}


def logo_path(variante="color"):
    """Ruta absoluta del logo (para generadores de PDF/Excel que necesitan un archivo)."""
    return os.path.join(current_app.static_folder, "img", _VARIANTES[variante])


def logo_data_uri(variante="color"):
    """Logo incrustado como data URI: el HTML del reporte no depende de rutas ni de la red."""
    with open(logo_path(variante), "rb") as fh:
        return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")
