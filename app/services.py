"""Reglas de negocio: pagos, asignaciones por inscripción y requisitos."""
import re
import unicodedata

from flask import current_app

from .models import Asignado, Curso, Gestion, Grado, Inscrito, Materia, Nota, Pago, db

METODOS_PAGO = ["Efectivo", "Transferencia", "Depósito", "QR", "Tarjeta"]


def nota_tiene_valores(nota):
    return nota is not None and any(
        v is not None for v in (nota.nota1, nota.nota2, nota.nota3, nota.nota4, nota.segunda_t))


def requisitos_pendientes(id_alumno, materia):
    """Materias requisito que el alumno aún no aprobó."""
    pendientes = []
    for req in materia.requisitos:
        aprobada = (db.session.query(Nota.id)
                    .join(Asignado, Nota.id_asignado == Asignado.id)
                    .join(Inscrito, Asignado.id_inscrito == Inscrito.id)
                    .filter(Inscrito.id_alumno == id_alumno,
                            Asignado.id_materia == req.id,
                            Nota.aprobado.is_(True))
                    .first())
        if not aprobada:
            pendientes.append(req)
    return pendientes


def crear_asignado(ins, materia, id_curso=None):
    """Crea la asignación (reutilizando el profesor ya asignado a esa materia
    en el mismo curso, si existe) y su registro de nota vacío.
    id_curso: curso donde se cursa la materia (por defecto, el de la inscripción)."""
    id_curso = id_curso or ins.id_curso
    prof = (db.session.query(Asignado.id_profesor)
            .filter(Asignado.id_curso == id_curso, Asignado.id_materia == materia.id,
                    Asignado.id_profesor.isnot(None)).first())
    a = Asignado(id_inscrito=ins.id, id_curso=id_curso, id_materia=materia.id,
                 id_profesor=prof[0] if prof else None)
    db.session.add(a)
    db.session.flush()
    db.session.add(Nota(id_asignado=a.id))
    return a


# ------------------------------------------------------------- Técnico Medio (1ro / 2do)
GRADO_1RO = "1ro. Técnico Medio"
GRADO_2DO = "2do. Técnico Medio"


def _clave(texto):
    """Normaliza un nombre de grado (sin tildes, mayúsculas ni puntuación)."""
    n = unicodedata.normalize("NFD", texto or "")
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()


_K1, _K2 = _clave(GRADO_1RO), _clave(GRADO_2DO)


def nivel_tm(grado):
    """'1ro' | '2do' si el grado es «1ro./2do. Técnico Medio»; si no, None."""
    k = _clave(grado.grado)
    return "1ro" if k == _K1 else "2do" if k == _K2 else None


def materias_aprobadas(id_alumno):
    """Ids de las materias que el alumno aprobó (en cualquier inscripción/gestión)."""
    q = (db.session.query(Asignado.id_materia)
         .join(Nota, Nota.id_asignado == Asignado.id)
         .join(Inscrito, Asignado.id_inscrito == Inscrito.id)
         .filter(Inscrito.id_alumno == id_alumno, Nota.aprobado.is_(True)).distinct())
    return {m for (m,) in q}


def contexto_tm(ins):
    """Datos para aplicar las reglas de Técnico Medio a una inscripción (o None si el
    curso no es de 1ro./2do. Técnico Medio). Los grados se toman del mismo instrumento."""
    nivel = nivel_tm(ins.curso.grado)
    if not nivel:
        return None
    grados = Grado.query.filter_by(id_instrumento=ins.curso.grado.id_instrumento).all()
    g1 = [g.id for g in grados if nivel_tm(g) == "1ro"]
    g2 = [g.id for g in grados if nivel_tm(g) == "2do"]
    mats = lambda ids: (Materia.query.filter(Materia.id_grado.in_(ids)).order_by(Materia.id).all() if ids else [])
    activos = {g for (g,) in db.session.query(Curso.id_grado)
               .join(Gestion, Curso.id_gestion == Gestion.id)
               .filter(Gestion.activo.is_(True), Curso.id_grado.in_(g1 + g2))}
    return {"nivel": nivel, "g1": g1, "g2": g2, "mats1": mats(g1), "mats2": mats(g2),
            "aprobadas": materias_aprobadas(ins.id_alumno), "activos": activos}


def materias_auto_tm(ctx):
    """Materias que se asignan automáticamente al inscribir.
    1ro: las de 1ro aún no aprobadas.
    2do: solo si aprobó todas las de 1ro -> las de 2do aún no aprobadas (si no, ninguna: se
    asignan manualmente los arrastres y las de 2do cuyos requisitos estén aprobados)."""
    ap = ctx["aprobadas"]
    if ctx["nivel"] == "1ro":
        return [m for m in ctx["mats1"] if m.id not in ap]
    if all(m.id in ap for m in ctx["mats1"]):
        return [m for m in ctx["mats2"] if m.id not in ap]
    return []


def validar_asignacion_tm(ins, mat, ctx=None):
    """Mensaje de error si la materia no puede asignarse a la inscripción; None si es válida."""
    ctx = ctx or contexto_tm(ins)
    if mat.id in ctx["aprobadas"]:
        return "El alumno ya aprobó esta materia"
    if mat.id_grado in ctx["g1"]:  # propia de 1ro, o arrastre si cursa 2do
        if ctx["nivel"] == "2do" and not any(g in ctx["activos"] for g in ctx["g1"]):
            return f"No existe un curso de «{GRADO_1RO}» habilitado en la gestión vigente (arrastre)"
        return None
    if mat.id_grado in ctx["g2"]:
        if ctx["nivel"] == "1ro" and not any(g in ctx["activos"] for g in ctx["g2"]):
            return f"No existe un curso de «{GRADO_2DO}» habilitado en la gestión vigente"
        pend = requisitos_pendientes(ins.id_alumno, mat)
        if pend:
            return "Requisitos sin aprobar: " + ", ".join(m.materia for m in pend)
        return None
    return f"Solo se pueden asignar materias de «{GRADO_1RO}» y «{GRADO_2DO}» del mismo instrumento"


def permitidas_tm(ins, ctx=None):
    """[(Materia, es_arrastre)] que se pueden asignar ahora a la inscripción."""
    ctx = ctx or contexto_tm(ins)
    ya = {m for (m,) in db.session.query(Asignado.id_materia).filter_by(id_inscrito=ins.id)}
    salida = []
    for m in ctx["mats1"] + ctx["mats2"]:
        if m.id in ya or validar_asignacion_tm(ins, m, ctx):
            continue
        salida.append((m, ctx["nivel"] == "2do" and m.id_grado in ctx["g1"]))
    return salida


def materias_permitidas_tm(ins):
    """[(id, etiqueta)] de las materias que se pueden asignar ahora a la inscripción."""
    return [(m.id, f"{m}{' · arrastre' if arr else ''}") for m, arr in permitidas_tm(ins)]


def cursos_activos_de_grado(id_grado):
    """Cursos del grado en la(s) gestión(es) activa(s), ordenados por paralelo."""
    return (Curso.query.join(Gestion, Curso.id_gestion == Gestion.id)
            .filter(Gestion.activo.is_(True), Curso.id_grado == id_grado)
            .order_by(Curso.paralelo, Curso.id).all())


def cursos_candidatos(ins, mat):
    """Cursos donde puede cursarse la materia: el de la inscripción si es de su grado;
    si es de otro grado, los cursos de ese grado en la gestión vigente."""
    if mat.id_grado == ins.curso.id_grado:
        return [ins.curso]
    return cursos_activos_de_grado(mat.id_grado)


def cursos_para_inscripcion(ins):
    """Cursos entre los que puede elegir el administrador al asignar materias a la inscripción.
    Técnico Medio: el propio (si aún tiene materias asignables) y los de los otros grados con
    materias pendientes (arrastres / 2do). Otros grados: solo el de la inscripción."""
    if not nivel_tm(ins.curso.grado):
        return [ins.curso]
    grados = {m.id_grado for m, _ in permitidas_tm(ins)}
    cursos = [ins.curso] if ins.curso.id_grado in grados else []
    for g in sorted(grados - {ins.curso.id_grado}):
        cursos += cursos_activos_de_grado(g)
    return cursos


def curso_para_materia(ins, mat):
    """Curso por defecto de la materia: el de la inscripción si es de su grado; si no, el único
    curso del grado en la gestión vigente (si hay varios, se prefiere el mismo paralelo)."""
    cursos = cursos_candidatos(ins, mat)
    if not cursos:
        return None
    return next((c.id for c in cursos if c.paralelo == ins.curso.paralelo), cursos[0].id)


def generar_pagos_y_asignados(ins, cuota_base=None):
    """Al inscribir: genera el/los registro(s) de pago aplicando el porcentaje de
    descuento, y asigna por defecto las materias del grado del curso."""
    if not ins.inscrito or ins.abandono:
        return
    cfg = current_app.config
    if Pago.query.filter_by(id_inscrito=ins.id).count() == 0:
        base = cuota_base if cuota_base is not None else cfg["CUOTA_BASE"]
        n = max(cfg["NUM_CUOTAS"], 1)
        total = round(base * (100 - ins.descuento) / 100, 2)
        cuota = round(total / n, 2)
        for i in range(n):
            monto = cuota if i < n - 1 else round(total - cuota * (n - 1), 2)
            db.session.add(Pago(id_inscrito=ins.id, cuota=monto, pagado=False,
                                obs=f"Cuota {i + 1}/{n}" if n > 1 else None))
    existentes = {m for (m,) in db.session.query(Asignado.id_materia).filter_by(id_inscrito=ins.id)}
    ctx = contexto_tm(ins)
    if ctx:  # 1ro/2do Técnico Medio: reglas según lo aprobado (solo en la primera generación)
        if not existentes:
            for m in materias_auto_tm(ctx):
                crear_asignado(ins, m)
        return
    for m in Materia.query.filter_by(id_grado=ins.curso.id_grado).order_by(Materia.id):
        if m.id not in existentes:
            crear_asignado(ins, m)


def recalcular_pagos_pendientes(ins, descuento_anterior):
    """Si cambia el descuento, ajusta proporcionalmente las cuotas no pagadas."""
    if descuento_anterior is None or descuento_anterior == ins.descuento or descuento_anterior >= 100:
        return
    factor = (100 - ins.descuento) / (100 - descuento_anterior)
    for p in Pago.query.filter_by(id_inscrito=ins.id, pagado=False):
        p.cuota = round(p.cuota * factor, 2)
