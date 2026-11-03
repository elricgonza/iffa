"""Autorización por permisos, alcance de datos por rol y auditoría."""
import json
from functools import wraps

from flask import abort, g, request
from flask_login import current_user, login_required

from .models import Alumno, AuditLog, Profesor, db

ROLES_AMPLIOS = {"Administrador", "Consulta", "Transcriptor"}


def permission_required(permiso):
    """Exige sesión iniciada y el permiso indicado (según el rol del usuario)."""
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if not current_user.has_perm(permiso):
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def scope_kind(user=None):
    """'all' | 'profesor' | 'estudiante' | 'none'.

    Un usuario Profesor/Estudiante solo ve sus propios datos. Si no tiene un
    registro vinculado (usr_id_login) NO ve nada: nunca cae a la vista global.
    """
    user = user or current_user
    roles = {r.rol for r in user.roles}
    if roles & ROLES_AMPLIOS:
        return "all"
    if "Profesor" in roles:
        return "profesor" if mi_profesor() else "none"
    if "Estudiante" in roles:
        return "estudiante" if mi_alumno() else "none"
    return "none"


def mi_profesor():
    if not hasattr(g, "_profesor"):
        g._profesor = Profesor.query.filter_by(usr_id_login=current_user.id).first()
    return g._profesor


def mi_alumno():
    if not hasattr(g, "_alumno"):
        g._alumno = Alumno.query.filter_by(usr_id_login=current_user.id).first()
    return g._alumno


def log_accion(accion, modulo, entidad_id=None, detalle=None, status=200, usuario=None):
    """Agrega un registro a audit_log (el commit lo hace quien llama)."""
    auth = current_user and current_user.is_authenticated
    db.session.add(AuditLog(
        usuario=usuario or (current_user.usuario if auth else "anónimo"),
        id_usuario=current_user.id if auth else None,
        accion=accion, modulo=modulo, entidad_id=entidad_id,
        detalle=json.dumps(detalle, ensure_ascii=False, default=str) if detalle else None,
        ip=request.remote_addr, endpoint=(request.endpoint or "")[:150],
        metodo=request.method, status=status,
    ))


def validar_password(nueva, usuario=None, actual=None):
    """Política de contraseñas (misma para el cambio propio y la administración de usuarios).
    Devuelve la lista de problemas (vacía si es válida)."""
    errores = []
    if len(nueva) < 8:
        errores.append("Debe tener al menos 8 caracteres")
    if len(nueva) > 128:
        errores.append("No debe superar 128 caracteres")
    if not (any(c.isalpha() for c in nueva) and any(c.isdigit() for c in nueva)):
        errores.append("Debe combinar letras y números")
    if usuario and usuario.lower() in nueva.lower():
        errores.append("No debe contener su nombre de usuario")
    if actual is not None and nueva == actual:
        errores.append("Debe ser distinta de la contraseña actual")
    return errores
