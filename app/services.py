"""Reglas de negocio: pagos, asignaciones por inscripción y requisitos."""
from flask import current_app

from .models import Asignado, Inscrito, Materia, Nota, Pago, db

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


def crear_asignado(ins, materia):
    """Crea la asignación (reutilizando el profesor ya asignado a esa materia
    en el mismo curso, si existe) y su registro de nota vacío."""
    prof = (db.session.query(Asignado.id_profesor)
            .filter(Asignado.id_curso == ins.id_curso, Asignado.id_materia == materia.id,
                    Asignado.id_profesor.isnot(None)).first())
    a = Asignado(id_inscrito=ins.id, id_curso=ins.id_curso, id_materia=materia.id,
                 id_profesor=prof[0] if prof else None)
    db.session.add(a)
    db.session.flush()
    db.session.add(Nota(id_asignado=a.id))
    return a


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
