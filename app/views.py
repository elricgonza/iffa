"""Autenticación, panel principal, perfil y asignación masiva de profesores."""
from datetime import date
from time import time
from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, redirect, render_template, request, url_for)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from . import config_notas
from .crud import RESOURCES, _render_table
from .models import Asignado, Curso, Materia, Pago, Profesor, Usuario, db
from .security import log_accion, scope_kind

auth_bp = Blueprint("auth", __name__)
main_bp = Blueprint("main", __name__)
acad_bp = Blueprint("acad", __name__, url_prefix="/asignado")
cfg_bp = Blueprint("cfgnotas", __name__, url_prefix="/config")

# --------------------------------------------------------------------- login
_intentos: dict = {}  # ip -> [marcas de tiempo] (limitador simple por proceso)
_VENTANA, _MAX_INTENTOS = 300, 5
_HASH_FALSO = generate_password_hash("no-existe")  # iguala tiempos si el usuario no existe


def _bloqueado(ip):
    ahora = time()
    _intentos[ip] = [t for t in _intentos.get(ip, []) if ahora - t < _VENTANA]
    return len(_intentos[ip]) >= _MAX_INTENTOS


def _destino_seguro(destino):
    if not destino:
        return None
    p = urlparse(destino)
    if p.scheme or p.netloc or not destino.startswith("/") or destino.startswith("//") or "\\" in destino:
        return None
    return destino


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.inicio"))
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _bloqueado(ip):
            flash("Demasiados intentos fallidos. Espere unos minutos e intente de nuevo.", "error")
            return render_template("login.html"), 429
        nombre = (request.form.get("usuario") or "").strip()
        clave = request.form.get("password") or ""
        user = Usuario.query.filter(db.func.lower(Usuario.usuario) == nombre.lower()).first()
        ok = check_password_hash(user.password if user else _HASH_FALSO, clave)
        if user and ok and user.activo:
            _intentos.pop(ip, None)
            login_user(user)
            user.last_login = date.today()
            log_accion("LOGIN", "auth", user.id, {"ok": True})
            db.session.commit()
            return redirect(_destino_seguro(request.args.get("next")) or url_for("main.inicio"))
        _intentos.setdefault(ip, []).append(time())
        log_accion("LOGIN", "auth", None, {"ok": False, "usuario": nombre[:80]}, status=401,
                   usuario=nombre[:80] or "anónimo")
        db.session.commit()
        flash("Usuario o contraseña incorrectos, o cuenta inactiva.", "error")
        return render_template("login.html"), 401
    return render_template("login.html")


@auth_bp.post("/logout")
@login_required
def logout():
    log_accion("LOGOUT", "auth", current_user.id)
    db.session.commit()
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/perfil", methods=["GET", "POST"])
@login_required
def perfil():
    if request.method == "POST":
        actual, nueva, conf = (request.form.get(k, "") for k in ("actual", "nueva", "confirmar"))
        if not current_user.check_password(actual):
            flash("La contraseña actual no es correcta.", "error")
        elif len(nueva) < 8:
            flash("La nueva contraseña debe tener al menos 8 caracteres.", "error")
        elif nueva != conf:
            flash("La confirmación no coincide.", "error")
        else:
            current_user.set_password(nueva)
            log_accion("UPDATE", "auth", current_user.id, {"cambio": "password"})
            db.session.commit()
            flash("Contraseña actualizada.", "ok")
            return redirect(url_for("auth.perfil"))
    return render_template("perfil.html")


# -------------------------------------------------------------------- panel
@main_bp.route("/")
@login_required
def inicio():
    tiles = []
    for key, etiqueta in (("alumno", "Alumnos"), ("inscrito", "Inscripciones"), ("asignado", "Asignaciones"),
                          ("nota", "Calificaciones"), ("pago", "Pagos")):
        r = RESOURCES[key]
        if current_user.has_perm(r.perm("ver")):
            tiles.append((etiqueta, r.build_query({}, current_user).count(), url_for("crud.index", key=key)))
    pendientes = None
    if current_user.has_perm("pago_ver"):
        pendientes = RESOURCES["pago"].build_query({}, current_user).filter(Pago.pagado.is_(False)).count()
    return render_template("dashboard.html", tiles=tiles, pendientes=pendientes, scope=scope_kind())


# -------------------------------------------- asignación masiva de profesor
def _opciones_form():
    return dict(
        cursos=[(c.id, str(c)) for c in Curso.query.order_by(Curso.id_gestion.desc(), Curso.paralelo).all()],
        profesores=[(p.id, p.nombre_completo) for p in
                    Profesor.query.filter_by(activo=True).order_by(Profesor.paterno, Profesor.nombre)],
    )


def _materias_de_curso(id_curso):
    q = (db.session.query(Materia).join(Asignado, Asignado.id_materia == Materia.id)
         .filter(Asignado.id_curso == id_curso).distinct().order_by(Materia.materia))
    return [(m.id, m.materia) for m in q]


def _necesita_editar():
    if not current_user.has_perm("asignado_editar"):
        abort(403)


@acad_bp.route("/masivo")
@login_required
def masivo():
    _necesita_editar()
    return render_template("crud/_masivo_asignado.html", errors={}, values={}, materias=[], **_opciones_form())


@acad_bp.route("/opciones-materia")
@login_required
def opciones_materia():
    _necesita_editar()
    return render_template("crud/_options.html", opciones=_materias_de_curso(request.args.get("id_curso", type=int)),
                           vacio="— Seleccione materia —", actual="")


@acad_bp.post("/masivo")
@login_required
def masivo_guardar():
    _necesita_editar()
    f = request.form
    id_curso, id_mat, id_prof = (f.get(k, type=int) for k in ("id_curso", "id_materia", "id_profesor"))
    errors = {}
    if not id_curso or not db.session.get(Curso, id_curso):
        errors["id_curso"] = "Seleccione un curso"
    if not id_prof or not Profesor.query.filter_by(id=id_prof, activo=True).first():
        errors["id_profesor"] = "Seleccione un profesor activo"
    if not errors and id_mat not in {m for m, _ in _materias_de_curso(id_curso)}:
        errors["id_materia"] = "Seleccione una materia del curso"
    if errors:
        return render_template("crud/_masivo_asignado.html", errors=errors, values=f,
                               materias=_materias_de_curso(id_curso) if id_curso else [], **_opciones_form())
    n = (Asignado.query.filter_by(id_curso=id_curso, id_materia=id_mat)
         .update({"id_profesor": id_prof}, synchronize_session=False))
    log_accion("BULK", "asignado", None, {"id_curso": id_curso, "id_materia": id_mat,
                                          "id_profesor": id_prof, "registros": n})
    db.session.commit()
    return _render_table(RESOURCES["asignado"], msg=f"Profesor asignado en {n} registro(s)", close=True)


# ------------------------------------- fechas de transcripción de notas (solo Administrador)
@cfg_bp.route("/notas", methods=["GET", "POST"])
@login_required
def notas():
    if not current_user.has_role("Administrador"):
        abort(403)
    actual = config_notas.cargar()
    if request.method == "POST":
        nuevo, errores = config_notas.desde_formulario(request.form)
        if errores:
            return render_template("config_notas.html", cfg=nuevo, errores=errores,
                                   periodos=config_notas.PERIODOS, estados=_estados(nuevo)), 200
        cambios = {k: {"antes": actual[k], "despues": v} for k, v in nuevo.items() if actual[k] != v}
        config_notas.guardar(nuevo)
        log_accion("UPDATE", "config_notas", None, cambios or {"sin_cambios": True})
        db.session.commit()
        flash("Fechas de transcripción de notas guardadas.", "ok")
        return redirect(url_for("cfgnotas.notas"))
    return render_template("config_notas.html", cfg=actual, errores={},
                           periodos=config_notas.PERIODOS, estados=_estados(actual))


def _estados(cfg):
    return {k: config_notas.estado(k, cfg) for k, _ in config_notas.PERIODOS}
