"""Fechas de habilitación de la transcripción de notas (archivo config_notas.json).

Formato del archivo (claves planas, una terna por período):
    habilitar_nota1: true|false
    transcripcion_nota1_inicio: "AAAA-MM-DD" | null
    transcripcion_nota1_final:  "AAAA-MM-DD" | null
    ... nota2, nota3, nota4 y 2t (segundo turno)

Regla: con habilitar_X = true la transcripción está permitida solo entre las fechas de
inicio y final (ambas inclusive); con false queda restringida. El rol Administrador
no está sujeto a estas restricciones.
"""
import json
import os
import tempfile
from datetime import date

from flask import current_app, g

PERIODOS = [("nota1", "Nota 1"), ("nota2", "Nota 2"), ("nota3", "Nota 3"),
            ("nota4", "Nota 4"), ("2t", "Segundo turno (2T)")]
CAMPO_A_PERIODO = {"nota1": "nota1", "nota2": "nota2", "nota3": "nota3", "nota4": "nota4", "segunda_t": "2t"}


def _por_defecto():
    cfg = {}
    for k, _ in PERIODOS:
        cfg[f"habilitar_{k}"] = False
        cfg[f"transcripcion_{k}_inicio"] = None
        cfg[f"transcripcion_{k}_final"] = None
    return cfg


def _fecha(v):
    if isinstance(v, str):
        try:
            return date.fromisoformat(v.strip())
        except ValueError:
            return None
    return None


def cargar():
    """Lee el archivo. Si no existe se crea con todo restringido; si está dañado
    se asume todo restringido (opción segura) y se registra el error."""
    if "_cfg_notas" in g:  # una sola lectura del archivo por petición
        return g._cfg_notas
    cfg = _leer()
    g._cfg_notas = cfg
    return cfg


def _leer():
    path = current_app.config["CONFIG_NOTAS_PATH"]
    cfg = _por_defecto()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError("el contenido debe ser un objeto JSON")
    except FileNotFoundError:
        try:
            guardar(cfg)
            return cfg
        except OSError:
            current_app.logger.exception("No se pudo crear %s", path)
        return cfg
    except (ValueError, OSError):
        current_app.logger.exception("config_notas.json ilegible: se restringe la transcripción")
        return cfg
    for k, _ in PERIODOS:
        cfg[f"habilitar_{k}"] = raw.get(f"habilitar_{k}") is True
        for lado in ("inicio", "final"):
            f = _fecha(raw.get(f"transcripcion_{k}_{lado}"))
            cfg[f"transcripcion_{k}_{lado}"] = f.isoformat() if f else None
    return cfg


def guardar(cfg):
    """Escritura atómica (archivo temporal + reemplazo)."""
    path = current_app.config["CONFIG_NOTAS_PATH"]
    carpeta = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=carpeta, prefix=".config_notas_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
        g.pop("_cfg_notas", None)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def estado(periodo, cfg=None, hoy=None):
    """(abierto, texto) de un período: 'nota1'..'nota4' o '2t'."""
    cfg = cfg or cargar()
    hoy = hoy or date.today()
    if not cfg[f"habilitar_{periodo}"]:
        return False, "Transcripción restringida"
    ini = _fecha(cfg[f"transcripcion_{periodo}_inicio"])
    fin = _fecha(cfg[f"transcripcion_{periodo}_final"])
    if ini is None or fin is None:
        return False, "Fechas de transcripción no definidas"
    if hoy < ini:
        return False, f"Se habilita el {ini:%d/%m/%Y}"
    if hoy > fin:
        return False, f"Plazo vencido el {fin:%d/%m/%Y}"
    return True, f"Habilitada hasta el {fin:%d/%m/%Y}"


def resumen():
    """[(etiqueta, abierto, texto)] de todos los períodos (para mostrar en pantalla)."""
    cfg = cargar()
    return [(label, *estado(k, cfg)) for k, label in PERIODOS]


def desde_formulario(form):
    """Valida el formulario del administrador -> (cfg, errores{periodo: mensaje})."""
    cfg, errores = _por_defecto(), {}
    for k, label in PERIODOS:
        on = form.get(f"habilitar_{k}") is not None
        cfg[f"habilitar_{k}"] = on
        fechas = {}
        for lado in ("inicio", "final"):
            raw = (form.get(f"transcripcion_{k}_{lado}") or "").strip()
            f = _fecha(raw) if raw else None
            if raw and f is None:
                errores[k] = f"{label}: fecha de {lado} no válida"
            fechas[lado] = f
            cfg[f"transcripcion_{k}_{lado}"] = f.isoformat() if f else None
        if k in errores:
            continue
        if on and (fechas["inicio"] is None or fechas["final"] is None):
            errores[k] = f"{label}: para habilitarla indique la fecha de inicio y la final"
        elif fechas["inicio"] and fechas["final"] and fechas["inicio"] > fechas["final"]:
            errores[k] = f"{label}: la fecha de inicio no puede ser posterior a la final"
    return cfg, errores
