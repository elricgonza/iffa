"""Reportes: Boletín de Calificaciones (HTML imprimible y PDF)."""
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from flask import Blueprint, Response, abort, current_app, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import branding
from .models import Alumno, Asignado, Curso, Gestion, Inscrito, db
from .resources import _opts_gestion_filtro, like
from .security import log_accion, mi_alumno, scope_kind

reportes_bp = Blueprint("reportes", __name__, url_prefix="/reportes")

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]


# ----------------------------------------------------------------- datos del boletín
def _entero(valor):
    return int(Decimal(str(valor)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _nombre_curso(curso):
    return curso.curso or curso.grado.grado


def datos_boletin(ins):
    """Reúne, desde la base de datos, todo lo que muestra el boletín de una inscripción."""
    cfg = current_app.config
    asigs = (Asignado.query.filter_by(id_inscrito=ins.id)
             .options(selectinload(Asignado.materia), selectinload(Asignado.nota),
                      selectinload(Asignado.curso).selectinload(Curso.grado)).all())
    # primero las materias del curso de la inscripción; luego arrastres / otras, por código
    asigs.sort(key=lambda a: (a.id_curso != ins.id_curso, a.materia.sigla or "~", a.materia.id))
    filas = []
    for n, a in enumerate(asigs, 1):
        nt = a.nota
        estado = None if (nt is None or nt.aprobado is None) else nt.aprobado
        filas.append({
            "n": n, "codigo": a.materia.sigla or "—", "asignatura": a.materia.materia,
            "p": [getattr(nt, f"nota{i}") if nt else None for i in range(1, 5)],
            "total": _entero(nt.nota_final) if (nt and nt.nota_final is not None) else None,
            "segunda_t": nt.segunda_t if nt else None, "aprobado": estado,
            "literal": "APROBADO" if estado else ("REPROBADO" if estado is False else "PENDIENTE"),
            "extra": f"{_nombre_curso(a.curso)} {a.curso.paralelo}" if a.id_curso != ins.id_curso else None,
        })
    totales = [f["total"] for f in filas if f["total"] is not None]
    aprobadas = sum(1 for f in filas if f["aprobado"] is True)
    reprobadas = sum(1 for f in filas if f["aprobado"] is False)
    pendientes = sum(1 for f in filas if f["aprobado"] is None)
    if not filas:
        situacion = "SIN MATERIAS ASIGNADAS"
    elif pendientes:
        situacion = "EN PROCESO"
    elif reprobadas:
        situacion = f"REPROBADO EN {reprobadas} MATERIA(S)"
    else:
        situacion = "APROBADO"
    hoy = date.today()
    curso = ins.curso
    return {
        "alumno": ins.alumno, "ci": ins.alumno.ci, "instrumento": ins.instrumento.instrumento,
        "gestion": curso.gestion.gestion, "curso": _nombre_curso(curso), "paralelo": curso.paralelo,
        "regimen": cfg["BOLETIN_REGIMEN"], "carrera": cfg["BOLETIN_CARRERA"],
        "filas": filas, "aprobadas": aprobadas, "reprobadas": reprobadas, "pendientes": pendientes,
        "promedio": round(sum(totales) / len(totales), 1) if totales else None,
        "situacion": situacion, "nota_minima": int(cfg["NOTA_APROBACION"]),
        "fecha_reporte": f"{hoy.day} de {MESES[hoy.month - 1]} de {hoy.year}",
        "ciudad": branding.CIUDAD.split("–")[0].strip(),
    }


def pdf_disponible():
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:  # no instalado o sin libpango
        return False


# ------------------------------------------------------------------------- permisos
def _autorizar():
    """Permiso «nota_boletin»; solo con alcance total (admin/transcriptor) o estudiante vinculado."""
    if not current_user.has_perm("nota_boletin") or scope_kind() not in ("all", "estudiante"):
        abort(403)


def _inscripcion_visible(id_inscrito):
    q = Inscrito.query.filter(Inscrito.id == id_inscrito)
    if scope_kind() == "estudiante":  # un estudiante solo ve su propio boletín
        q = q.filter(Inscrito.id_alumno == mi_alumno().id)
    ins = q.first()
    if ins is None:
        abort(404)
    return ins


def _etiqueta(i):
    return f"{i.alumno.nombre_completo} · C.I. {i.alumno.ci or '—'} — {i.curso}"


# --------------------------------------------------------------------------- rutas
@reportes_bp.route("/boletin/seleccion")
@login_required
def boletin_seleccion():
    """Ventana (modal) para elegir de quién se genera el boletín."""
    _autorizar()
    if scope_kind() == "estudiante":
        inscs = (Inscrito.query.join(Curso, Inscrito.id_curso == Curso.id).join(Gestion, Curso.id_gestion == Gestion.id)
                 .filter(Inscrito.id_alumno == mi_alumno().id, Inscrito.inscrito.is_(True))
                 .options(selectinload(Inscrito.curso).selectinload(Curso.grado), selectinload(Inscrito.curso).selectinload(Curso.gestion))
                 .order_by(Gestion.gestion.desc(), Curso.id.desc()).all())
        items = [(i.id, f"Gestión {i.curso.gestion.gestion} · {_nombre_curso(i.curso)} {i.curso.paralelo}") for i in inscs]
        return render_template("reportes/_boletin_seleccion.html", modo="estudiante", items=items, gestiones=[],
                               pdf_ok=pdf_disponible())
    return render_template("reportes/_boletin_seleccion.html", modo="buscar", items=[], gestiones=_opts_gestion_filtro(),
                           pdf_ok=pdf_disponible())


@reportes_bp.route("/boletin/buscar")
@login_required
def boletin_buscar():
    """Búsqueda en vivo de inscripciones (alumno por nombre, apellido o C.I.) para administrador/transcriptor."""
    _autorizar()
    if scope_kind() != "all":
        abort(403)
    texto = (request.args.get("q") or "").strip()
    if len(texto) < 2:
        return render_template("crud/_search_results.html", items=[], msg="Escriba al menos 2 caracteres")
    q = (Inscrito.query.join(Alumno, Inscrito.id_alumno == Alumno.id).join(Curso, Inscrito.id_curso == Curso.id)
         .filter(Inscrito.inscrito.is_(True), like(Alumno.nombre, Alumno.paterno, Alumno.materno, Alumno.ci)(texto)))
    g = request.args.get("id_gestion", "activa")
    if g == "activa":
        q = q.filter(Curso.id_gestion.in_(select(Gestion.id).where(Gestion.activo.is_(True))))
    elif g.isdigit():
        q = q.filter(Curso.id_gestion == int(g))
    q = (q.options(selectinload(Inscrito.alumno), selectinload(Inscrito.curso).selectinload(Curso.gestion),
                   selectinload(Inscrito.curso).selectinload(Curso.grado))
         .order_by(Alumno.paterno, Alumno.materno, Alumno.nombre, Curso.id_gestion.desc()).limit(15))
    return render_template("crud/_search_results.html", items=[(i.id, _etiqueta(i)) for i in q], msg=None)


@reportes_bp.route("/boletin/<int:id_inscrito>")
@login_required
def boletin(id_inscrito):
    _autorizar()
    ins = _inscripcion_visible(id_inscrito)
    quiere_pdf = request.args.get("pdf") == "1"
    datos = datos_boletin(ins)
    log_accion("CREATE", "reporte_boletin", ins.id,
               {"alumno": ins.alumno.nombre_completo, "gestion": datos["gestion"], "pdf": quiere_pdf})
    db.session.commit()
    html = render_template("reportes/boletin.html", d=datos, pdf=quiere_pdf, pdf_disponible=pdf_disponible(),
                           id_inscrito=ins.id, titulo_reporte=f"Boletín de Calificaciones · {datos['gestion']}",
                           meta_reporte=f"Gestión {datos['gestion']}")
    if not quiere_pdf:
        return html
    if not pdf_disponible():
        return Response("La generación de PDF no está disponible en este servidor (instale weasyprint). "
                        "Puede usar «Imprimir» y guardar como PDF.", status=501, mimetype="text/plain")
    from weasyprint import HTML
    contenido = HTML(string=html).write_pdf()
    nombre = re.sub(r"[^A-Za-z0-9_-]+", "", f"boletin_{ins.alumno.ci or ins.alumno.id}_{datos['gestion']}")
    return Response(contenido, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{nombre}.pdf"', "Cache-Control": "no-store"})
