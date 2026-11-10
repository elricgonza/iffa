"""Reportes: Boletín de Calificaciones (HTML imprimible y PDF)."""
import io
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from flask import Blueprint, Response, abort, current_app, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import branding
from .models import Alumno, Asignado, Curso, Gestion, Grado, Inscrito, Materia, Profesor, db
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


# =========================================================================================
#  CENTRALIZADOR DE CALIFICACIONES (por curso): pantalla/PDF y Excel con el formato oficial
# =========================================================================================
FILAS_MINIMAS = 10  # el formulario oficial tiene al menos 10 filas numeradas


def datos_centralizador(curso, id_alumno=None, libro="", folio=""):
    """Reúne, desde la base de datos, todo lo que muestra el Centralizador de un curso.
    id_alumno: si se indica (vista de estudiante), la nómina muestra solo su fila."""
    cfg = current_app.config
    materias = Materia.query.filter_by(id_grado=curso.id_grado).order_by(Materia.sigla, Materia.id).all()
    ids_materia = {m.id for m in materias}
    inscritos = (Inscrito.query.join(Alumno, Inscrito.id_alumno == Alumno.id)
                 .filter(Inscrito.id_curso == curso.id, Inscrito.inscrito.is_(True))
                 .options(selectinload(Inscrito.alumno),
                          selectinload(Inscrito.asignados).selectinload(Asignado.nota),
                          selectinload(Inscrito.asignados).selectinload(Asignado.materia))
                 .order_by(Alumno.paterno, Alumno.materno, Alumno.nombre, Inscrito.id).all())
    filas = []
    for n, ins in enumerate(inscritos, 1):
        por_materia = {a.id_materia: a for a in ins.asignados}
        notas, resultados = [], []
        for m in materias:
            a = por_materia.get(m.id)
            nt = a.nota if a else None
            notas.append(nt.nota_aprob if (nt is not None and nt.nota_aprob is not None) else None)
            if a is not None:
                resultados.append(None if (nt is None or nt.aprobado is None) else nt.aprobado)
        if ins.abandono:
            estado = "ABANDONO"
        elif not resultados or any(r is None for r in resultados) and not any(r is False for r in resultados):
            estado = "EN PROCESO"
        elif any(r is False for r in resultados):
            estado = "REPROBADO"
        else:
            estado = "APROBADO"
        obs = []
        if ins.abandono:
            obs.append("ABANDONO")
        if ins.obs:
            obs.append(ins.obs.strip().upper())
        arr = [a for a in ins.asignados if a.id_materia not in ids_materia]
        if arr:
            obs.append("ARRASTRE: " + ", ".join(a.materia.sigla or a.materia.materia for a in arr))
        filas.append({"n": n, "id_alumno": ins.id_alumno, "nombre": ins.alumno.nombre_completo.upper(),
                      "ci": ins.alumno.ci, "notas": notas, "estado": estado, "obs": " · ".join(obs)})
    estadisticas = [("ESTUDIANTES INSCRITOS", len(filas)),
                    ("ESTUDIANTES APROBADOS", sum(f["estado"] == "APROBADO" for f in filas)),
                    ("ESTUDIANTES REPROBADOS", sum(f["estado"] == "REPROBADO" for f in filas)),
                    ("ABANDONOS", sum(f["estado"] == "ABANDONO" for f in filas))]
    en_proceso = sum(f["estado"] == "EN PROCESO" for f in filas)
    if en_proceso:
        estadisticas.append(("EN PROCESO (SIN CALIFICACIÓN COMPLETA)", en_proceso))
    promedios = []
    for j in range(len(materias)):
        vals = [f["notas"][j] for f in filas if f["notas"][j] is not None]
        promedios.append(round(sum(vals) / len(vals), 1) if vals else None)
    # docentes que firman por asignatura (los asignados en este curso)
    pares = (db.session.query(Asignado.id_materia, Asignado.id_profesor)
             .filter(Asignado.id_curso == curso.id, Asignado.id_profesor.isnot(None)).distinct().all())
    nombres = {p.id: p.nombre_completo for p in Profesor.query.filter(Profesor.id.in_({pid for _, pid in pares} or {0}))}
    docentes = {}
    for mid, pid in pares:
        docentes.setdefault(mid, set()).add(nombres.get(pid, ""))
    bloques = [{"materia": m.materia, "docente": " / ".join(sorted(d for d in docentes.get(m.id, ()) if d)).upper()}
               for m in materias]
    if id_alumno is not None and not cfg["CENTRALIZADOR_ESTUDIANTE_VE_TODOS"]:
        filas_vista = [f for f in filas if f["id_alumno"] == id_alumno]
    else:
        filas_vista = filas
    hoy = date.today()
    return {
        "gestion": curso.gestion.gestion, "nivel": (curso.grado.nivel or "").upper(), "carrera": cfg["BOLETIN_CARRERA"],
        "instrumento": curso.grado.instrumento.instrumento.upper(), "regimen": cfg["CENTRALIZADOR_REGIMEN"],
        "curso": f'{_nombre_curso(curso).upper()} "{curso.paralelo.upper()}"',
        "institucion": f"{branding.INSTITUCION.upper()} “{branding.NOMBRE.upper()}”", "rm": branding.RM_CENTRALIZADOR,
        "ciudad": branding.CIUDAD.upper().replace("–", "-"), "turno": cfg["CENTRALIZADOR_TURNO"],
        "caracter": cfg["CENTRALIZADOR_CARACTER"], "codigo_registro": cfg["CENTRALIZADOR_CODIGO_REGISTRO"],
        "libro": (libro or "").strip()[:20], "folio": (folio or "").strip()[:20],
        "materias": [{"codigo": m.sigla or "—", "nombre": m.materia.upper()} for m in materias],
        "filas": filas_vista, "total_filas": len(filas), "estadisticas": estadisticas, "promedios": promedios,
        "bloques": bloques, "nota_minima": int(cfg["NOTA_APROBACION"]),
        "solo_propia": id_alumno is not None and not cfg["CENTRALIZADOR_ESTUDIANTE_VE_TODOS"],
        "fecha_reporte": f"{hoy.day} de {MESES[hoy.month - 1]} de {hoy.year}",
        "filas_en_blanco": max(FILAS_MINIMAS - len(filas_vista), 0) if not (id_alumno is not None and not cfg["CENTRALIZADOR_ESTUDIANTE_VE_TODOS"]) else 0,
    }


def centralizador_xlsx(d):
    """Libro Excel con el formato oficial: hoja ANVERSO (nómina y calificaciones) y REVERSO (firmas y estadísticas)."""
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as L

    VERDE_CLARO, VERDE = PatternFill("solid", fgColor="D6E3BC"), PatternFill("solid", fgColor="9BBB59")
    thin, med, dot = Side(style="thin"), Side(style="medium"), Side(style="dotted")
    todo = Border(left=thin, right=thin, top=thin, bottom=thin)
    centro = Alignment(horizontal="center", vertical="center", wrap_text=True)
    izq = Alignment(horizontal="left", vertical="center", wrap_text=True)
    vertical = Alignment(horizontal="center", vertical="center", textRotation=90, wrap_text=True)

    def poner(ws, ref, valor, font=None, align=None, fill=None, border=None, fmt=None, formula=False):
        c = ws[ref]
        c.value = valor
        if isinstance(valor, str) and not formula:
            c.data_type = "s"  # texto literal: nunca se interpreta como fórmula (p. ej. «=…» en un nombre)
        if font: c.font = font
        if align: c.alignment = align
        if fill: c.fill = fill
        if border: c.border = border
        if fmt: c.number_format = fmt
        return c

    def unir(ws, rango, borde=None):
        ws.merge_cells(rango)
        if borde:
            for fila in ws[rango]:
                for c in fila:
                    c.border = borde

    def negrita(sz=11, **kw):
        return Font(name="Calibri", size=sz, bold=True, **kw)

    def pagina(ws):
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = 5  # Oficio (Legal), como el formato original
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins.left = ws.page_margins.right = 0.4
        ws.page_margins.top = ws.page_margins.bottom = 0.5
        ws.print_options.horizontalCentered = True
        ws.oddFooter.center.text = f"{branding.SIGLA} · Documento generado el {d['fecha_reporte']}"
        ws.oddFooter.center.size = 8

    def logo(ws, archivo, ancla, alto=None):
        import os
        img = XLImage(os.path.join(current_app.static_folder, "img", archivo))
        if alto:
            img.width, img.height = round(img.width * alto / img.height), alto
        ws.add_image(img, ancla)

    nm = len(d["materias"])
    c0, c_est = 5, 5 + nm            # primera columna de asignaturas / columna ESTADO
    c_obs, c_fin = c_est + 1, c_est + 2
    # ------------------------------------------------------------------ ANVERSO
    ws = wb_ws = Workbook().active
    wb = ws.parent
    ws.title = "ANVERSO"
    ws.sheet_view.showGridLines = False
    for col, w in {1: 4.3, 2: 25.4, 3: 31, 4: 14}.items():
        ws.column_dimensions[L(col)].width = w
    for j in range(nm):
        ws.column_dimensions[L(c0 + j)].width = 10.5
    ws.column_dimensions[L(c_est)].width = 14
    ws.column_dimensions[L(c_obs)].width = 8
    ws.column_dimensions[L(c_fin)].width = 28
    for r, h in {1: 15.75, 2: 15.75, 3: 15, 4: 16.5, 5: 16.5, 6: 24, 7: 22, 8: 30}.items():
        ws.row_dimensions[r].height = h
    logo(ws, "ministerio-educacion.png", "A1")
    logo(ws, "logo-sm.png", f"{L(c_fin)}1", alto=58)
    # código de registro, título y libro/folio
    unir(ws, f"{L(max(c_est - 2, 4))}1:{L(c_est - 1)}1")
    poner(ws, f"{L(max(c_est - 2, 4))}1", "Código de Registro: ", negrita(), Alignment(horizontal="right"))
    poner(ws, f"{L(c_est)}1", d["codigo_registro"], negrita())
    ancho = 14 + 10.5 * nm + 14  # ancho disponible para el título (en caracteres)
    unir(ws, f"D3:{L(c_est)}5")
    poner(ws, "D3", "CENTRALIZADOR DE CALIFICACIONES", negrita(20 if ancho >= 95 else 14 if ancho >= 60 else 12), centro)
    for fila, texto in ((4, f"LIBRO N° {d['libro']}"), (5, f"FOLIO N° {d['folio']}")):
        unir(ws, f"{L(c_obs)}{fila}:{L(c_fin)}{fila}")
        poner(ws, f"{L(c_obs)}{fila}", texto, negrita(14), izq)
        for col in (c_obs, c_fin):
            ws[f"{L(col)}{fila}"].border = Border(left=med if col == c_obs else None, right=med if col == c_fin else None,
                                                  top=med if fila == 4 else None, bottom=med if fila == 5 else None)
    # lugar, turno, institución, carácter
    unir(ws, "A7:B7")
    poner(ws, "A7", d["ciudad"], negrita(10), Alignment(horizontal="center", vertical="center"))
    poner(ws, f"{L(c_est - 1)}7", "TURNO:", negrita(), Alignment(horizontal="right", vertical="center"))
    poner(ws, f"{L(c_est)}7", d["turno"], negrita(), Alignment(horizontal="left", vertical="center"))
    unir(ws, "A8:B8", Border(bottom=thin))
    poner(ws, "A8", "INSTITUCIÓN: ", Font(name="Calibri", size=11), izq, border=Border(bottom=thin))
    unir(ws, f"C8:{L(c_est - 2)}8", Border(bottom=thin))
    poner(ws, "C8", f"{d['institucion']}   R.M. {d['rm']}", negrita(), izq, border=Border(bottom=thin))
    poner(ws, f"{L(c_est - 1)}8", "CARÁCTER:", negrita(), Alignment(horizontal="right", vertical="center"), border=Border(bottom=thin))
    poner(ws, f"{L(c_est)}8", d["caracter"], negrita(), izq, border=Border(bottom=thin))
    # datos del curso (bloque izquierdo, filas 9-14)
    datos = [("GESTIÓN: ", d["gestion"]), ("NIVEL: ", d["nivel"]), ("CARRERA:  ", d["carrera"]),
             ("INSTRUMENTO DE ESPECIALIDAD: ", d["instrumento"]), ("RÉGIMEN: ", d["regimen"]), ("CURSO: ", d["curso"])]
    for i, (et, val) in enumerate(datos):
        r = 9 + i
        ws.row_dimensions[r].height = 21
        unir(ws, f"A{r}:B{r}", todo)
        poner(ws, f"A{r}", et, negrita(), izq, border=todo)
        poner(ws, f"C{r}", val, negrita(), izq, border=todo)
    ws.row_dimensions[15].height = 21
    poner(ws, "A15", "N°", negrita(), centro, VERDE_CLARO, todo)
    unir(ws, "B15:C15", todo)
    poner(ws, "B15", "NÓMINA ESTUDIANTES", negrita(), centro, VERDE_CLARO, todo)
    # encabezado de la matriz (códigos y nombres de asignaturas en vertical)
    unir(ws, "D9:D15", todo)
    poner(ws, "D9", "CÉDULA DE IDENTIDAD ", negrita(12), centro, VERDE_CLARO, todo)
    for j, m in enumerate(d["materias"]):
        col = L(c0 + j)
        poner(ws, f"{col}9", m["codigo"], negrita(12), centro, VERDE, todo)
        unir(ws, f"{col}10:{col}15", todo)
        poner(ws, f"{col}10", m["nombre"], negrita(11), vertical, VERDE_CLARO, todo)
    unir(ws, f"{L(c_est)}9:{L(c_est)}15", todo)
    poner(ws, f"{L(c_est)}9", "ESTADO", negrita(12), vertical, VERDE_CLARO, todo)
    unir(ws, f"{L(c_obs)}9:{L(c_fin)}15", todo)
    poner(ws, f"{L(c_obs)}9", "OBSERVACIONES ", negrita(12), vertical, VERDE_CLARO, todo)
    # nómina
    COLOR_ESTADO = {"APROBADO": "047857", "REPROBADO": "B91C1C", "ABANDONO": "92400E", "EN PROCESO": "64748B"}
    r = 16
    for f in d["filas"] + [None] * d["filas_en_blanco"]:
        ws.row_dimensions[r].height = 21
        n = f["n"] if f else r - 15
        poner(ws, f"A{r}", n, negrita(10), centro, border=todo)
        unir(ws, f"B{r}:C{r}", todo)
        poner(ws, f"B{r}", f["nombre"] if f else None, Font(name="Calibri", size=12), Alignment(horizontal="left", vertical="center"), border=todo)
        poner(ws, f"D{r}", f["ci"] if f else None, Font(name="Calibri", size=12), centro, border=todo)
        for j in range(nm):
            v = f["notas"][j] if f else None
            roja = v is not None and v < d["nota_minima"]
            poner(ws, f"{L(c0 + j)}{r}", v, Font(name="Calibri", size=12, bold=roja, color="B91C1C" if roja else None), centro, border=todo)
        est = f["estado"] if f else None
        poner(ws, f"{L(c_est)}{r}", est, Font(name="Calibri", size=12, bold=bool(est), color=COLOR_ESTADO.get(est)), centro, border=todo)
        unir(ws, f"{L(c_obs)}{r}:{L(c_fin)}{r}", todo)
        poner(ws, f"{L(c_obs)}{r}", f["obs"] if f else None, Font(name="Calibri", size=11), centro, border=todo)
        r += 1
    # promedio por asignatura (mejora)
    poner(ws, f"B{r}", "PROMEDIO POR ASIGNATURA", negrita(10), Alignment(horizontal="right", vertical="center"))
    ws.merge_cells(f"B{r}:D{r}")
    for j in range(nm):
        poner(ws, f"{L(c0 + j)}{r}", d["promedios"][j], negrita(10, color="193B68"), centro, border=todo, fmt="0.0")
    if d["solo_propia"]:
        r += 1
        ws.merge_cells(f"A{r}:{L(c_fin)}{r}")
        poner(ws, f"A{r}", f"Vista del estudiante: solo se muestran sus propios datos (curso con {d['total_filas']} inscritos).",
              Font(name="Calibri", size=9, italic=True, color="64748B"), Alignment(horizontal="left"))
    # firmas
    r += 4
    mitad = c0 + max(nm - 3, 0)
    for rango, texto in ((f"B{r}:C{r}", "RECTOR (A)"), (f"{L(mitad)}{r}:{L(c_est)}{r}", "DIRECTOR(A) ACADÉMICO(A)")):
        unir(ws, rango, Border(top=dot))
        poner(ws, rango.split(":")[0], texto, negrita(9), Alignment(horizontal="center", vertical="top", wrap_text=True), border=Border(top=dot))
    ws.print_title_rows = "9:15"
    pagina(ws)

    # ------------------------------------------------------------------ REVERSO
    rv = wb.create_sheet("REVERSO")
    rv.sheet_view.showGridLines = False
    for col, w in {1: 4, 2: 38.6, 3: 4, 8: 4, 13: 4}.items():
        rv.column_dimensions[L(col)].width = w
    for col in (4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 17):
        rv.column_dimensions[L(col)].width = 10
    logo(rv, "ministerio-educacion.png", "A1")
    logo(rv, "logo-sm.png", "N1", alto=58)
    unir(rv, "C4:M5")
    poner(rv, "C4", "CENTRALIZADOR DE CALIFICACIONES", negrita(20), centro)
    poner(rv, "N4", f"{d['curso']} · GESTIÓN {d['gestion']}", negrita(10), Alignment(horizontal="left", vertical="center"))
    columnas = [(2, 2), (4, 7), (9, 12), (14, 17)]
    fila = 13
    for k, b in enumerate(d["bloques"]):
        a, z = columnas[k % 4]
        if k and k % 4 == 0:
            fila += 8
        rv.row_dimensions[fila].height = 23.25
        rv.row_dimensions[fila + 1].height = 30
        r1, r2 = f"{L(a)}{fila}", f"{L(a)}{fila + 1}"
        if z > a:
            unir(rv, f"{r1}:{L(z)}{fila}", Border(top=dot))
            unir(rv, f"{r2}:{L(z)}{fila + 1}")
        poner(rv, r1, b["docente"] or "—", Font(name="Calibri", size=11), Alignment(horizontal="center", vertical="center", wrap_text=True), border=Border(top=dot))
        poner(rv, r2, b["materia"].upper(), negrita(11), Alignment(horizontal="center", vertical="top", wrap_text=True))
    fila += 5
    unir(rv, f"G{fila}:H{fila}")
    poner(rv, f"G{fila}", "ESTADÍSTICAS", negrita(12), centro)
    fila += 2
    unir(rv, f"D{fila}:I{fila}", todo)
    poner(rv, f"D{fila}", "DETALLE", negrita(12), centro, PatternFill("solid", fgColor="D8E4BC"), todo)
    poner(rv, f"J{fila}", "CANTIDAD", negrita(12), centro, PatternFill("solid", fgColor="D8E4BC"), todo)
    poner(rv, f"K{fila}", "%", negrita(12), centro, PatternFill("solid", fgColor="D8E4BC"), todo)
    primero = fila + 1
    for i, (det, cant) in enumerate(d["estadisticas"]):
        rr = primero + i
        unir(rv, f"D{rr}:I{rr}", todo)
        poner(rv, f"D{rr}", det, negrita(12), izq, border=todo)
        poner(rv, f"J{rr}", cant, negrita(12), centro, border=todo)
        poner(rv, f"K{rr}", f"=IF($J${primero}=0,0,J{rr}/$J${primero})", negrita(12), centro, border=todo, fmt="0%", formula=True)
    pagina(rv)
    ws.parent.active = 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------ rutas del Centralizador
def _autorizar_central():
    """Permiso «reporte_centralizador»; administrador/transcriptor (alcance total) o estudiante vinculado."""
    if not current_user.has_perm("reporte_centralizador") or scope_kind() not in ("all", "estudiante"):
        abort(403)


def _cursos_selector(id_gestion):
    q = (Curso.query.join(Gestion, Curso.id_gestion == Gestion.id).join(Grado, Curso.id_grado == Grado.id)
         .options(selectinload(Curso.gestion), selectinload(Curso.grado)))
    if id_gestion == "activa":
        q = q.filter(Gestion.activo.is_(True))
    elif str(id_gestion).isdigit():
        q = q.filter(Curso.id_gestion == int(id_gestion))
    if scope_kind() == "estudiante":  # el estudiante solo ve los cursos en los que está inscrito
        q = q.filter(Curso.id.in_(select(Inscrito.id_curso).where(Inscrito.id_alumno == mi_alumno().id,
                                                                  Inscrito.inscrito.is_(True))))
    return [(c.id, str(c)) for c in q.order_by(Gestion.gestion.desc(), Grado.grado, Curso.paralelo)]


@reportes_bp.route("/centralizador")
@login_required
def centralizador():
    """Pantalla del Centralizador de Calificaciones: elegir gestión y curso."""
    _autorizar_central()
    cursos = _cursos_selector("activa")
    return render_template("reportes/centralizador_seleccion.html", gestiones=_opts_gestion_filtro(),
                           cursos=cursos, pdf_ok=pdf_disponible(), es_estudiante=scope_kind() == "estudiante")


@reportes_bp.route("/centralizador/cursos")
@login_required
def centralizador_cursos():
    """Opciones de curso según la gestión elegida (HTMX)."""
    _autorizar_central()
    ops = _cursos_selector(request.args.get("id_gestion", "activa"))
    vacio = "— Seleccione curso —" if ops else "— Sin cursos en esta gestión —"
    return render_template("crud/_options.html", opciones=ops, vacio=vacio,
                           actual=ops[0][0] if len(ops) == 1 else "")


@reportes_bp.route("/centralizador/generar")
@login_required
def centralizador_generar():
    _autorizar_central()
    curso = db.session.get(Curso, request.args.get("id_curso", type=int) or 0)
    if curso is None:
        abort(404)
    id_alumno = None
    if scope_kind() == "estudiante":
        id_alumno = mi_alumno().id
        if not Inscrito.query.filter_by(id_alumno=id_alumno, id_curso=curso.id, inscrito=True).first():
            abort(404)
    formato = request.args.get("formato", "html")
    if formato not in ("html", "pdf", "xlsx"):
        abort(400)
    datos = datos_centralizador(curso, id_alumno, request.args.get("libro", ""), request.args.get("folio", ""))
    log_accion("CREATE", "reporte_centralizador", curso.id,
               {"curso": str(curso), "formato": formato, "estudiante": id_alumno is not None})
    db.session.commit()
    nombre = re.sub(r"[^A-Za-z0-9_-]+", "", f"centralizador_{curso.id}_{datos['gestion']}")
    if formato == "xlsx":
        return Response(centralizador_xlsx(datos),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{nombre}.xlsx"', "Cache-Control": "no-store"})
    html = render_template("reportes/centralizador.html", d=datos, pdf=formato == "pdf", pdf_disponible=pdf_disponible(),
                           args=request.args, titulo_reporte=f"Centralizador de Calificaciones · {datos['curso']}")
    if formato == "html":
        return html
    if not pdf_disponible():
        return Response("La generación de PDF no está disponible en este servidor (instale weasyprint). "
                        "Puede usar el Excel o «Imprimir».", status=501, mimetype="text/plain")
    from weasyprint import HTML
    return Response(HTML(string=html).write_pdf(), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{nombre}.pdf"', "Cache-Control": "no-store"})
