"""Declaración de los recursos (tablas) administrables, sus formularios,
reglas de validación y alcance de datos según el rol."""
import unicodedata
from datetime import datetime

from flask import current_app
from flask_login import current_user
from sqlalchemy import and_, cast, false, func, inspect as sa_inspect, or_, select, String, true
from sqlalchemy.orm import selectinload

from . import config_notas
from .crud import Col, Field, Filter, Resource
from .models import (Alumno, Asignado, AuditLog, Curso, Gestion, Grado, Inscrito,
                     Instrumento, Materia, Nota, Pago, Permiso, Profesor, Rol, Usuario, db)
from .security import mi_alumno, mi_profesor, scope_kind
from .services import (METODOS_PAGO, crear_asignado, curso_para_materia, cursos_candidatos,
                       cursos_para_inscripcion, generar_pagos_y_asignados, nivel_tm, permitidas_tm,
                       nota_tiene_valores, recalcular_pagos_pendientes, requisitos_pendientes,
                       validar_asignacion_tm)

ROLES_BASE = ("Administrador", "Profesor", "Estudiante", "Consulta", "Transcriptor")


# ---------------------------------------------------------------- utilidades
def opts(model, order=(), where=None, eager=()):
    def f():
        q = model.query
        if where is not None:
            q = q.filter(where)
        if eager:
            q = q.options(*eager)
        if order:
            q = q.order_by(*order)
        return [(o.id, str(o)) for o in q.all()]
    return f


def static(values):
    return lambda: [(v, v) for v in values]


_CON_TILDE, _SIN_TILDE = "áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN"


def _norm(texto):
    t = unicodedata.normalize("NFD", texto)
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn").lower()


def like(*cols):
    """Búsqueda por palabras: cada palabra debe aparecer en alguna de las columnas
    (nombre, apellidos, C.I., ...), sin distinguir mayúsculas ni tildes.
    Ej.: «maria per» encuentra «Pérez Gómez María»."""
    def fn(s):
        conds = []
        for tok in _norm(s).split()[:6]:
            pat = "%" + tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conds.append(or_(*[
                func.lower(func.translate(c if isinstance(c.type, String) else cast(c, String),
                                          _CON_TILDE, _SIN_TILDE)).like(pat, escape="\\")
                for c in cols]))
        return and_(*conds) if conds else true()
    return fn


# ---- filtro «Gestión»: por defecto la gestión activa; se puede elegir otra o «todas»
_CURSOS_ACTIVOS = select(Curso.id).join(Gestion, Curso.id_gestion == Gestion.id).where(Gestion.activo.is_(True))


def _opts_gestion_filtro():
    ges = [(g.id, str(g)) for g in Gestion.query.order_by(Gestion.gestion.desc(), Gestion.id.desc())]
    return [("activa", "Gestión activa")] + ges + [("todas", "Todas las gestiones")]


def _filtro_gestion(q, v):
    """Para consultas que ya incluyen Curso (cursos, inscripciones)."""
    if v == "activa":
        return q.filter(Curso.id_gestion.in_(select(Gestion.id).where(Gestion.activo.is_(True))))
    return q.filter(Curso.id_gestion == int(v))


def _filtro_gestion_en(col):
    """Filtro por la gestión del curso referido por `col` (p. ej. Asignado.id_curso)."""
    def aplicar(q, v):
        if v == "activa":
            return q.filter(col.in_(_CURSOS_ACTIVOS))
        return q.filter(col.in_(select(Curso.id).where(Curso.id_gestion == int(v))))
    return aplicar


def _filtro_gestion_def():
    return Filter("id_gestion", "Gestión", _opts_gestion_filtro, _filtro_gestion, default="activa")


def usuarios_con_rol(rol):
    def f():
        return [(u.id, f"{u.usuario} ({u.email})") for u in
                Usuario.query.filter(Usuario.roles.any(Rol.rol == rol)).order_by(Usuario.usuario)]
    return f


def _vinculo_unico(modelo, data, obj, etiqueta):
    uid = data.get("usr_id_login")
    if not uid:
        return {}
    q = modelo.query.filter(modelo.usr_id_login == uid)
    if obj is not None:
        q = q.filter(modelo.id != obj.id)
    return {"usr_id_login": f"Ese usuario ya está vinculado a otro {etiqueta}"} if q.first() else {}


# ------------------------------------------------------------ catálogos simples
def _validar_gestion(d, o):
    """Fechas coherentes y a lo sumo UNA gestión activa (el índice único de la BD lo refuerza)."""
    if not current_user.has_role("Administrador"):
        return {"__all__": "Solo el Administrador puede gestionar las gestiones académicas."}
    err = {}
    if d["fin"] <= d["inicio"]:
        err["fin"] = "Debe ser posterior al inicio"
    if d.get("activo"):
        q = Gestion.query.filter(Gestion.activo.is_(True))
        if o is not None:
            q = q.filter(Gestion.id != o.id)
        otra = q.first()
        if otra:
            err["activo"] = (f"Ya existe una gestión activa ({otra}). Solo una gestión puede estar activa: "
                             "desactive primero la otra.")
    return err


Resource(
    "gestion", Gestion, "Gestiones académicas", "Gestión",
    fields=[
        Field("gestion", "Año", "number", required=True, min=1990, max=2100),
        Field("plan", "Plan / nombre", required=True),
        Field("inicio", "Inicio", "date", required=True),
        Field("fin", "Fin", "date", required=True),
        Field("activo", "Activa", "bool", default=False,
              help="Solo una gestión puede estar activa a la vez."),
    ],
    columns=[Col("Año", "gestion"), Col("Plan", "plan"), Col("Inicio", "inicio", "date", True),
             Col("Fin", "fin", "date", True), Col("Activa", "activo", "bool")],
    search=like(Gestion.plan, Gestion.gestion),
    order=lambda: [Gestion.gestion.desc()],
    validate=lambda d, o: _validar_gestion(d, o), solo_admin=True,
)

Resource(
    "instrumento", Instrumento, "Instrumentos", "Instrumento",
    fields=[Field("instrumento", "Instrumento", required=True), Field("area", "Área")],
    columns=[Col("Instrumento", "instrumento"), Col("Área", "area")],
    search=like(Instrumento.instrumento, Instrumento.area),
    order=lambda: [Instrumento.instrumento],
)

Resource(
    "grado", Grado, "Grados", "Grado",
    fields=[
        Field("id_instrumento", "Instrumento", "select", required=True, options=opts(Instrumento, [Instrumento.instrumento])),
        Field("nivel", "Nivel", required=True),
        Field("grado", "Grado", required=True),
    ],
    columns=[Col("Instrumento", "instrumento.instrumento"), Col("Nivel", "nivel"), Col("Grado", "grado")],
    query=lambda: Grado.query.join(Instrumento, Grado.id_instrumento == Instrumento.id),
    search=like(Grado.grado, Grado.nivel, Instrumento.instrumento),
    filters=[Filter("id_instrumento", "Instrumento", opts(Instrumento, [Instrumento.instrumento]),
                    lambda q, v: q.filter(Grado.id_instrumento == int(v)))],
    order=lambda: [Instrumento.instrumento, Grado.nivel, Grado.grado],
)

_opts_grado = opts(Grado, [Grado.grado], eager=[selectinload(Grado.instrumento)])
_opts_materia = opts(Materia, [Materia.materia], eager=[selectinload(Materia.grado)])
_opts_curso = opts(Curso, [Curso.id_gestion.desc(), Curso.paralelo],
                   eager=[selectinload(Curso.gestion), selectinload(Curso.grado)])


def _validar_materia(d, o):
    if o and o.id in d.get("requisitos", []):
        return {"requisitos": "Una materia no puede ser requisito de sí misma"}
    return {}


Resource(
    "materia", Materia, "Materias y requisitos", "Materia",
    fields=[
        Field("id_grado", "Grado", "select", required=True, options=_opts_grado),
        Field("materia", "Materia", required=True),
        Field("sigla", "Sigla"),
        Field("requisitos", "Materias requisito", "multiselect", target=Materia, options=_opts_materia,
              help="Materias que el alumno debe haber aprobado antes de cursarla."),
    ],
    columns=[Col("Materia", "materia"), Col("Sigla", "sigla", hide=True), Col("Grado", "grado.grado"),
             Col("Requisitos", lambda m: ", ".join(r.materia for r in m.requisitos), hide=True)],
    query=lambda: Materia.query.join(Grado, Materia.id_grado == Grado.id),
    search=like(Materia.materia, Materia.sigla),
    filters=[Filter("id_grado", "Grado", _opts_grado, lambda q, v: q.filter(Materia.id_grado == int(v)))],
    order=lambda: [Grado.grado, Materia.materia],
    validate=_validar_materia,
)

Resource(
    "curso", Curso, "Cursos", "Curso",
    fields=[
        Field("id_gestion", "Gestión", "select", required=True, options=opts(Gestion, [Gestion.gestion.desc()])),
        Field("id_grado", "Grado", "select", required=True, options=_opts_grado),
        Field("curso", "Nombre del curso", help="Opcional; si se omite se usa el nombre del grado."),
        Field("paralelo", "Paralelo", required=True),
        Field("aula", "Aula"),
        Field("capacidad", "Capacidad (cupos)", "number", min=1, max=32000),
    ],
    columns=[Col("Gestión", "gestion.gestion"), Col("Curso", lambda c: c.curso or c.grado.grado),
             Col("Grado", "grado.grado", hide=True), Col("Paralelo", "paralelo"),
             Col("Aula", "aula", hide=True), Col("Cupos", "capacidad", hide=True)],
    query=lambda: Curso.query.join(Gestion, Curso.id_gestion == Gestion.id).join(Grado, Curso.id_grado == Grado.id),
    search=like(Curso.curso, Curso.paralelo, Grado.grado),
    filters=[_filtro_gestion_def()],
    order=lambda: [Gestion.gestion.desc(), Grado.grado, Curso.paralelo],
)


# --------------------------------------------------------- profesores y alumnos
Resource(
    "profesor", Profesor, "Profesores", "Profesor",
    fields=[
        Field("nombre", "Nombres", required=True),
        Field("paterno", "Apellido paterno"), Field("materno", "Apellido materno"),
        Field("masculino", "Masculino", "bool", default=True),
        Field("ci", "C.I.", "number", min=1, max=2147483647),
        Field("formacion", "Formación"), Field("email", "Correo", "email"),
        Field("activo", "Activo", "bool", default=True),
        Field("usr_id_login", "Usuario de acceso", "select", options=usuarios_con_rol("Profesor"),
              help="Cuenta con rol Profesor que usará para ingresar. Sin vínculo, el profesor no verá ningún dato."),
    ],
    columns=[Col("Profesor", "nombre_completo"), Col("C.I.", "ci", hide=True),
             Col("Formación", "formacion", hide=True), Col("Activo", "activo", "bool"),
             Col("Usuario", lambda p: db.session.get(Usuario, p.usr_id_login) if p.usr_id_login else None, hide=True)],
    search=like(Profesor.nombre, Profesor.paterno, Profesor.materno, Profesor.ci),
    order=lambda: [Profesor.paterno, Profesor.materno, Profesor.nombre],
    validate=lambda d, o: _vinculo_unico(Profesor, d, o, "profesor"),
)

Resource(
    "alumno", Alumno, "Alumnos", "Alumno",
    fields=[
        Field("nombre", "Nombres", required=True),
        Field("paterno", "Apellido paterno"), Field("materno", "Apellido materno"),
        Field("nacimiento", "Fecha de nacimiento", "date"),
        Field("masculino", "Masculino", "bool", default=True),
        Field("ci", "C.I.", "number", min=1, max=2147483647),
        Field("direccion", "Dirección"), Field("email", "Correo", "email"),
        Field("activo", "Activo", "bool", default=True),
        Field("obs", "Observaciones"),
        Field("usr_id_login", "Usuario de acceso", "select", options=usuarios_con_rol("Estudiante"),
              help="Cuenta con rol Estudiante. Sin vínculo, el estudiante no verá ningún dato."),
    ],
    columns=[Col("Alumno", "nombre_completo"), Col("C.I.", "ci", hide=True),
             Col("Nacimiento", "nacimiento", "date", True), Col("Correo", "email", hide=True),
             Col("Activo", "activo", "bool")],
    search=like(Alumno.nombre, Alumno.paterno, Alumno.materno, Alumno.ci),
    order=lambda: [Alumno.paterno, Alumno.materno, Alumno.nombre],
    validate=lambda d, o: _vinculo_unico(Alumno, d, o, "alumno"),
    scope=lambda q: _scope_alumno(q),
)


def _scope_alumno(q):
    k = scope_kind()
    if k == "all":
        return q
    if k == "profesor":
        ids = (db.session.query(Inscrito.id_alumno)
               .join(Asignado, Asignado.id_inscrito == Inscrito.id)
               .filter(Asignado.id_profesor == mi_profesor().id))
        return q.filter(Alumno.id.in_(ids))
    if k == "estudiante":
        return q.filter(Alumno.id == mi_alumno().id)
    return q.filter(false())


# ---------------------------------------------------------------- inscripciones
def _scope_inscrito(q):
    k = scope_kind()
    if k == "all":
        return q
    if k == "profesor":
        ids = db.session.query(Asignado.id_inscrito).filter(Asignado.id_profesor == mi_profesor().id)
        return q.filter(Inscrito.id.in_(ids))
    if k == "estudiante":
        return q.filter(Inscrito.id_alumno == mi_alumno().id)
    return q.filter(false())


def _bloqueo_tm(curso):
    """Las inscripciones/asignaciones de 1ro. y 2do. Técnico Medio solo las gestiona el Administrador."""
    if nivel_tm(curso.grado) and not current_user.has_role("Administrador"):
        return {"__all__": "Las inscripciones y asignaciones de «1ro./2do. Técnico Medio» "
                           "solo puede gestionarlas el Administrador."}
    return {}


def _validar_inscrito(d, o):
    err = _bloqueo_tm(db.session.get(Curso, d["id_curso"]) if o is None else o.curso)
    if err:
        return err
    if o is None:
        if Inscrito.query.filter_by(id_alumno=d["id_alumno"], id_curso=d["id_curso"]).first():
            err["id_curso"] = "El alumno ya está inscrito en este curso"
        else:
            curso = db.session.get(Curso, d["id_curso"])
            if d.get("inscrito") and curso.capacidad:
                ocupados = Inscrito.query.filter_by(id_curso=curso.id, inscrito=True, abandono=False).count()
                if ocupados >= curso.capacidad:
                    err["id_curso"] = f"El curso no tiene cupos disponibles ({curso.capacidad})"
    if d["descuento"] > 0 and not d.get("motivo_descuento"):
        err["motivo_descuento"] = "Indique el motivo del descuento"
    return err


def _before_save_inscrito(obj, d):
    h = sa_inspect(obj).attrs.descuento.history
    obj._descuento_anterior = h.deleted[0] if h.deleted else None


def _after_save_inscrito(obj, d, nuevo):
    if not nuevo:
        recalcular_pagos_pendientes(obj, getattr(obj, "_descuento_anterior", None))
    generar_pagos_y_asignados(obj, d.get("cuota_base"))


def _before_delete_inscrito(ins):
    if any(p.pagado for p in ins.pagos):
        raise ValueError("No se puede eliminar: tiene pagos registrados.")
    if any(nota_tiene_valores(a.nota) for a in ins.asignados):
        raise ValueError("No se puede eliminar: tiene notas registradas.")
    for a in ins.asignados:
        if a.nota:
            db.session.delete(a.nota)
        db.session.delete(a)
    for p in ins.pagos:
        db.session.delete(p)


def cursos_de_instrumento(id_instrumento, params=None):
    """Cursos del instrumento indicado (vía su grado) en la(s) gestión(es) activa(s)."""
    try:
        iid = int(id_instrumento)
    except (TypeError, ValueError):
        return []
    q = (Curso.query.join(Gestion, Curso.id_gestion == Gestion.id).join(Grado, Curso.id_grado == Grado.id)
         .filter(Gestion.activo.is_(True), Grado.id_instrumento == iid)
         .options(selectinload(Curso.gestion), selectinload(Curso.grado))
         .order_by(Grado.grado, Curso.paralelo))
    return [(c.id, str(c)) for c in q]


def _etiqueta_alumno(a):
    return f"{a.nombre_completo} · C.I. {a.ci or '—'}"


def buscar_alumnos(texto):
    """Alumnos activos cuyo nombre, apellido(s) o C.I. contenga las palabras escritas."""
    q = (Alumno.query.filter(Alumno.activo.is_(True),
                             like(Alumno.nombre, Alumno.paterno, Alumno.materno, Alumno.ci)(texto))
         .order_by(Alumno.paterno, Alumno.materno, Alumno.nombre).limit(15))
    return [(a.id, _etiqueta_alumno(a)) for a in q]


_ins_base = lambda: (Inscrito.query.join(Alumno, Inscrito.id_alumno == Alumno.id)
                     .join(Curso, Inscrito.id_curso == Curso.id))
_eager_ins = [selectinload(Inscrito.alumno), selectinload(Inscrito.curso).selectinload(Curso.gestion),
              selectinload(Inscrito.curso).selectinload(Curso.grado)]


def _opts_inscrito():
    """Inscritos de la gestión académica activa (confirmados y que no abandonaron), por orden alfabético."""
    q = (Inscrito.query.options(*_eager_ins)
         .filter(Inscrito.inscrito.is_(True), Inscrito.abandono.is_(False),
                 Inscrito.id_curso.in_(_CURSOS_ACTIVOS)))
    return sorted(((i.id, str(i)) for i in q), key=lambda t: t[1].lower())

Resource(
    "inscrito", Inscrito, "Inscripciones", "Inscripción",
    fields=[
        Field("id_alumno", "Alumno", "search", required=True, create_only=True, target=Alumno,
              search=buscar_alumnos, label_fn=_etiqueta_alumno, check=lambda a: a.activo,
              help="Busque por nombre, apellido o C.I. (puede combinar palabras)."),
        Field("id_instrumento", "Instrumento", "select", required=True, create_only=True,
              options=opts(Instrumento, [Instrumento.instrumento])),
        Field("id_curso", "Curso (gestión activa)", "select", required=True, create_only=True,
              depends_on="id_instrumento", dep_options=cursos_de_instrumento,
              help="Solo cursos del instrumento elegido en la gestión activa."),
        Field("inscrito", "Inscrito (confirmado)", "bool", default=True),
        Field("reserva", "Reserva de cupo", "bool"),
        Field("abandono", "Abandono", "bool"),
        Field("otras_materias", "Permitir materias de otros grados", "bool"),
        Field("descuento", "Descuento (%)", "number", required=True, min=0, max=100, default=0,
              help="Se aplica al generar el pago; al cambiarlo se reajustan las cuotas pendientes."),
        Field("motivo_descuento", "Motivo del descuento"),
        Field("cuota_base", "Monto base (opcional)", "decimal", virtual=True, create_only=True, min=0,
              help="Si se deja vacío se usa la cuota base configurada (CUOTA_BASE)."),
        Field("obs", "Observaciones"),
    ],
    columns=[Col("Alumno", "alumno.nombre_completo"), Col("C.I.", "alumno.ci", hide=True), Col("Curso", "curso"),
             Col("Instrumento", "instrumento.instrumento", hide=True),
             Col("Desc. %", "descuento", hide=True), Col("Inscrito", "inscrito", "bool"),
             Col("Abandono", "abandono", "bool", True)],
    query=_ins_base, scope=_scope_inscrito,
    search=like(Alumno.nombre, Alumno.paterno, Alumno.materno, Alumno.ci),
    filters=[_filtro_gestion_def(),
             Filter("id_instrumento", "Instrumento", opts(Instrumento, [Instrumento.instrumento]),
                    lambda q, v: q.filter(Inscrito.id_instrumento == int(v))),
             Filter("id_curso", "Curso", _opts_curso, lambda q, v: q.filter(Inscrito.id_curso == int(v)))],
    order=lambda: [Curso.id_gestion.desc(), Alumno.paterno, Alumno.materno, Alumno.nombre],
    validate=_validar_inscrito, before_save=_before_save_inscrito,
    after_save=_after_save_inscrito, before_delete=_before_delete_inscrito,
)


# ------------------------------------------------------------------- asignados
def _scope_asignado(q):
    k = scope_kind()
    if k == "all":
        return q
    if k == "profesor":
        return q.filter(Asignado.id_profesor == mi_profesor().id)
    if k == "estudiante":
        return q.filter(Inscrito.id_alumno == mi_alumno().id)
    return q.filter(false())


def _entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _cursos_de_inscrito(id_inscrito, params=None):
    """Cursos entre los que se puede elegir al asignar una materia al inscrito.
    Técnico Medio: el propio y los de otros grados con materias pendientes (p. ej. arrastres de 1ro.);
    otros grados: solo el de la inscripción."""
    ins = db.session.get(Inscrito, _entero(id_inscrito) or 0)
    if ins is None:
        return []
    return [(c.id, str(c)) for c in cursos_para_inscripcion(ins)]


def _materias_de_inscrito(id_curso, params=None):
    """Materias asignables al inscrito en el curso elegido. Técnico Medio: solo las que cumplen las
    reglas (arrastres pendientes, requisitos aprobados) y son del grado de ese curso; otros grados: todas."""
    ins = db.session.get(Inscrito, _entero((params or {}).get("id_inscrito")) or 0)
    curso = db.session.get(Curso, _entero(id_curso) or 0)
    if ins is None or curso is None:
        return []
    if nivel_tm(ins.curso.grado):
        return [(m.id, f"{m}{' · arrastre' if arr else ''}")
                for m, arr in permitidas_tm(ins) if m.id_grado == curso.id_grado]
    return _opts_materia()


def _validar_asignado(d, o):
    if o is not None:
        return {}
    ins = db.session.get(Inscrito, d["id_inscrito"])
    mat = db.session.get(Materia, d["id_materia"])
    id_curso = d.get("id_curso")
    if Asignado.query.filter_by(id_inscrito=ins.id, id_materia=mat.id).first():
        return {"id_materia": "La materia ya está asignada a este inscrito"}
    if nivel_tm(ins.curso.grado):
        err = _bloqueo_tm(ins.curso)
        if err:
            return err
        msg = validar_asignacion_tm(ins, mat)
        if msg:
            return {"id_materia": msg}
        ids = {c.id for c in cursos_candidatos(ins, mat)}
        if id_curso and id_curso not in ids:
            return {"id_curso": "El curso no corresponde al grado de la materia"}
        if not id_curso and len(ids) > 1:
            return {"id_curso": "Existen varios cursos de ese grado: seleccione el curso"}
        return {}
    if id_curso and id_curso != ins.id_curso:
        return {"id_curso": "En este grado la materia se cursa en el curso de la inscripción"}
    if mat.id_grado != ins.curso.id_grado and not ins.otras_materias:
        return {"id_materia": "Es de otro grado: habilite «materias de otros grados» en la inscripción"}
    pend = requisitos_pendientes(ins.id_alumno, mat)
    if pend:
        return {"id_materia": "Requisitos pendientes: " + ", ".join(m.materia for m in pend)}
    return {}


def _before_save_asignado(obj, d):
    if obj.id_curso is None:  # sin curso elegido: el de la inscripción o el único curso del grado de la materia
        ins = db.session.get(Inscrito, obj.id_inscrito)
        mat = db.session.get(Materia, obj.id_materia)
        obj.id_curso = curso_para_materia(ins, mat) if nivel_tm(ins.curso.grado) else ins.id_curso


def _after_save_asignado(obj, d, nuevo):
    if nuevo and Nota.query.filter_by(id_asignado=obj.id).count() == 0:
        db.session.add(Nota(id_asignado=obj.id))


def _before_delete_asignado(a):
    if nota_tiene_valores(a.nota):
        raise ValueError("No se puede eliminar: ya tiene notas registradas.")
    if a.nota:
        db.session.delete(a.nota)


_asig_base = lambda: (Asignado.query
                      .join(Inscrito, Asignado.id_inscrito == Inscrito.id)
                      .join(Alumno, Inscrito.id_alumno == Alumno.id)
                      .join(Materia, Asignado.id_materia == Materia.id))


def _filtro_profesor(q, v):
    return q.filter(Asignado.id_profesor.is_(None)) if v == "0" else q.filter(Asignado.id_profesor == int(v))


_opts_profesor_filtro = lambda: [("0", "— Sin profesor —")] + opts(Profesor, [Profesor.paterno, Profesor.nombre])()

Resource(
    "asignado", Asignado, "Asignaciones (profesor · materia · alumno · curso)", "Asignación",
    fields=[
        Field("id_inscrito", "Inscrito", "select", required=True, create_only=True, options=_opts_inscrito),
        Field("id_curso", "Curso", "select", create_only=True, options=_opts_curso,
              depends_on="id_inscrito", dep_options=_cursos_de_inscrito, dep_strict=False, auto_unico=True,
              help="Se preselecciona si hay un único curso; en Técnico Medio, si existen varios cursos del grado "
                   "de la materia (p. ej. arrastres de 1ro.) elija el curso."),
        Field("id_materia", "Materia", "select", required=True, create_only=True, options=_opts_materia,
              depends_on="id_curso", dep_options=_materias_de_inscrito, dep_strict=False,
              dep_include=("id_inscrito",),
              help="1ro/2do Técnico Medio: solo las permitidas (arrastres pendientes, 2do con requisitos aprobados). "
                   "Otros grados: las del grado, u otras si la inscripción lo permite."),
        Field("id_profesor", "Profesor", "select",
              options=opts(Profesor, [Profesor.paterno, Profesor.nombre], where=Profesor.activo.is_(True))),
    ],
    columns=[Col("Alumno", "inscrito.alumno.nombre_completo"), Col("Curso", "curso"),
             Col("Materia", "materia.materia"),
             Col("Profesor", lambda a: a.profesor.nombre_completo if a.profesor else None)],
    query=_asig_base, scope=_scope_asignado,
    search=like(Alumno.nombre, Alumno.paterno, Alumno.materno, Materia.materia),
    filters=[Filter("id_gestion", "Gestión", _opts_gestion_filtro, _filtro_gestion_en(Asignado.id_curso),
                    default="activa"),
             Filter("id_curso", "Curso", _opts_curso, lambda q, v: q.filter(Asignado.id_curso == int(v))),
             Filter("id_profesor", "Profesor", _opts_profesor_filtro, _filtro_profesor)],
    order=lambda: [Asignado.id_curso.desc(), Materia.materia, Alumno.paterno, Alumno.nombre],
    validate=_validar_asignado, before_save=_before_save_asignado,
    after_save=_after_save_asignado, before_delete=_before_delete_asignado,
    toolbar="crud/_toolbar_asignado.html",
)


# ----------------------------------------------------------------------- notas
def _scope_nota(q):
    k = scope_kind()
    if k == "all":
        return q
    if k == "profesor":
        return q.filter(Asignado.id_profesor == mi_profesor().id)
    if k == "estudiante":
        return q.filter(Inscrito.id_alumno == mi_alumno().id)
    return q.filter(false())


def _puede_editar_nota(n, user):
    k = scope_kind()
    if k == "all":
        return True
    return k == "profesor" and n.asignado.id_profesor == mi_profesor().id


def _before_save_nota(n, d):
    n.calcular(current_app.config["NOTA_APROBACION"])


_nota_base = lambda: (Nota.query
                      .join(Asignado, Nota.id_asignado == Asignado.id)
                      .join(Inscrito, Asignado.id_inscrito == Inscrito.id)
                      .join(Alumno, Inscrito.id_alumno == Alumno.id)
                      .join(Materia, Asignado.id_materia == Materia.id))
def _bloqueo_nota(periodo):
    """Bloquea el campo fuera del rango de fechas definido en config_notas.json
    (el Administrador no está sujeto a la restricción)."""
    def fn(obj):
        if current_user.has_role("Administrador"):
            return None
        abierto, motivo = config_notas.estado(periodo)
        return None if abierto else motivo
    return fn


_n = lambda name: Field(name, name.replace("nota", "Nota "), "number", min=0, max=100,
                        locked=_bloqueo_nota(name))

Resource(
    "nota", Nota, "Calificaciones", "Calificación",
    fields=[_n("nota1"), _n("nota2"), _n("nota3"), _n("nota4"),
            Field("segunda_t", "2.º turno", "number", min=0, max=100, locked=_bloqueo_nota("2t"),
                  help="Nota de segundo turno; reemplaza al promedio si éste no alcanza para aprobar."),
            Field("obs", "Observaciones")],
    columns=[Col("Alumno", "asignado.inscrito.alumno.nombre_completo"),
             Col("Curso", "asignado.curso", hide=True), Col("Materia", "asignado.materia.materia"),
             Col("N1", "nota1", hide=True), Col("N2", "nota2", hide=True), Col("N3", "nota3", hide=True),
             Col("N4", "nota4", hide=True), Col("Final", "nota_final"), Col("2T", "segunda_t", hide=True),
             Col("Aprobado", "aprobado", "bool")],
    query=_nota_base, scope=_scope_nota, can_edit=_puede_editar_nota,
    allow_create=False, allow_delete=False,
    search=like(Alumno.nombre, Alumno.paterno, Alumno.materno, Materia.materia),
    filters=[Filter("id_gestion", "Gestión", _opts_gestion_filtro, _filtro_gestion_en(Asignado.id_curso),
                    default="activa"),
             Filter("id_curso", "Curso", _opts_curso, lambda q, v: q.filter(Asignado.id_curso == int(v))),
             Filter("id_materia", "Materia", _opts_materia, lambda q, v: q.filter(Asignado.id_materia == int(v)))],
    order=lambda: [Asignado.id_curso.desc(), Materia.materia, Alumno.paterno, Alumno.nombre],
    before_save=_before_save_nota, info="crud/_info_nota.html",
)


# ----------------------------------------------------------------------- pagos
def _scope_pago(q):
    k = scope_kind()
    if k == "all":
        return q
    if k == "estudiante":
        return q.filter(Inscrito.id_alumno == mi_alumno().id)
    return q.filter(false())


def _validar_pago(d, o):
    if d.get("pagado") and not d.get("metodo_pago"):
        return {"metodo_pago": "Indique el método de pago"}
    return {}


def _before_save_pago(p, d):
    if p.pagado and not p.fecha_pago:
        p.fecha_pago = datetime.now()
    if not p.pagado:
        p.fecha_pago = None


Resource(
    "pago", Pago, "Pagos", "Pago",
    fields=[
        Field("pagado", "Pagado", "bool"),
        Field("metodo_pago", "Método de pago", "select", options=static(METODOS_PAGO)),
        Field("fecha_pago", "Fecha y hora del pago", "datetime", help="Si se omite se usa la fecha actual."),
        Field("referencia_pago", "Referencia / comprobante"),
        Field("obs", "Observaciones"),
    ],
    columns=[Col("Alumno", "inscrito.alumno.nombre_completo"), Col("Curso", "inscrito.curso", hide=True),
             Col("Cuota", "cuota", "money"), Col("Pagado", "pagado", "bool"),
             Col("Método", "metodo_pago", hide=True), Col("Fecha", "fecha_pago", "datetime", True),
             Col("Referencia", "referencia_pago", hide=True)],
    query=lambda: (Pago.query.join(Inscrito, Pago.id_inscrito == Inscrito.id)
                   .join(Alumno, Inscrito.id_alumno == Alumno.id)
                   .join(Curso, Inscrito.id_curso == Curso.id)),
    scope=_scope_pago, allow_create=False, allow_delete=False,
    search=like(Alumno.nombre, Alumno.paterno, Alumno.materno, Pago.referencia_pago),
    filters=[Filter("estado", "Estado", lambda: [("pagado", "Pagados"), ("pendiente", "Pendientes")],
                    lambda q, v: q.filter(Pago.pagado.is_(v == "pagado"))),
             Filter("id_curso", "Curso", _opts_curso, lambda q, v: q.filter(Inscrito.id_curso == int(v)))],
    order=lambda: [Pago.pagado, Alumno.paterno, Alumno.nombre, Pago.id],
    validate=_validar_pago, before_save=_before_save_pago,
)


# --------------------------------------------- usuarios, roles, permisos, auditoría
def _validar_usuario(d, o):
    err = {}
    q = Usuario.query.filter(db.func.lower(Usuario.usuario) == d["usuario"].lower())
    if o:
        q = q.filter(Usuario.id != o.id)
    if q.first():
        err["usuario"] = "Ese nombre de usuario ya existe"
    if d.get("password") and len(d["password"]) < 8:
        err["password"] = "Mínimo 8 caracteres"
    if o is not None and o.id == current_user.id:
        if not d["activo"]:
            err["activo"] = "No puede desactivar su propia cuenta"
        admin = Rol.query.filter_by(rol="Administrador").first()
        if admin and o.has_role("Administrador") and admin.id not in d["roles"]:
            err["roles"] = "No puede quitarse a sí mismo el rol Administrador"
    return err


def _before_save_usuario(u, d):
    if d.get("password"):
        u.set_password(d["password"])


def _before_delete_usuario(u):
    Profesor.query.filter_by(usr_id_login=u.id).update({"usr_id_login": None})
    Alumno.query.filter_by(usr_id_login=u.id).update({"usr_id_login": None})


Resource(
    "usuario", Usuario, "Usuarios", "Usuario",
    fields=[
        Field("usuario", "Usuario", required=True),
        Field("email", "Correo", "email", required=True),
        Field("password", "Contraseña", "password", req_create=True,
              help="Mínimo 8 caracteres. En edición, déjela vacía para no cambiarla."),
        Field("activo", "Activo", "bool", default=True),
        Field("roles", "Roles", "multiselect", target=Rol, options=opts(Rol, [Rol.rol])),
    ],
    columns=[Col("Usuario", "usuario"), Col("Correo", "email", hide=True),
             Col("Roles", lambda u: ", ".join(r.rol for r in u.roles)),
             Col("Activo", "activo", "bool"), Col("Último acceso", "last_login", "date", True)],
    search=like(Usuario.usuario, Usuario.email),
    order=lambda: [Usuario.usuario],
    validate=_validar_usuario, before_save=_before_save_usuario, before_delete=_before_delete_usuario,
    can_delete=lambda u, user: u.id != user.id,
)


def _validar_rol(d, o):
    if o and o.rol in ROLES_BASE and d["rol"] != o.rol:
        return {"rol": "Los roles base del sistema no se pueden renombrar"}
    return {}


Resource(
    "rol", Rol, "Roles y permisos", "Rol",
    fields=[
        Field("rol", "Rol", required=True), Field("descripcion", "Descripción"),
        Field("permisos", "Permisos", "multiselect", target=Permiso, options=opts(Permiso, [Permiso.permiso])),
    ],
    columns=[Col("Rol", "rol"), Col("Descripción", "descripcion", hide=True),
             Col("Permisos", lambda r: len(r.permisos)), Col("Usuarios", lambda r: len(r.usuarios), hide=True)],
    order=lambda: [Rol.rol], validate=_validar_rol,
    can_delete=lambda r, user: r.rol not in ROLES_BASE,
)

Resource(
    "permiso", Permiso, "Catálogo de permisos", "Permiso",
    fields=[Field("descripcion", "Descripción")],
    columns=[Col("Permiso", "permiso"), Col("Descripción", "descripcion")],
    search=like(Permiso.permiso, Permiso.descripcion), order=lambda: [Permiso.permiso],
    allow_create=False, allow_delete=False,
)

Resource(
    "auditoria", AuditLog, "Registro de auditoría", "Registro",
    fields=[],
    columns=[Col("Fecha", "creado", "datetime"), Col("Usuario", "usuario"), Col("Acción", "accion"),
             Col("Módulo", "modulo"), Col("ID", "entidad_id", hide=True), Col("IP", "ip", hide=True),
             Col("Detalle", "detalle", hide=True)],
    search=like(AuditLog.usuario, AuditLog.modulo, AuditLog.detalle),
    filters=[Filter("accion", "Acción",
                    lambda: [(a, a) for a in ("CREATE", "UPDATE", "DELETE", "LOGIN", "LOGOUT", "BULK", "ERROR")],
                    lambda q, v: q.filter(AuditLog.accion == v))],
    order=lambda: [AuditLog.id.desc()],
    allow_create=False, allow_edit=False, allow_delete=False,
)
