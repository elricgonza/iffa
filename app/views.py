"""Autenticación, panel principal, perfil y asignación masiva de profesores."""
from datetime import date
from time import time
from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, redirect, render_template, request, url_for)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import String, cast, or_
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.security import check_password_hash, generate_password_hash

from . import config_notas
from .crud import RESOURCES, _render_table
from .models import (Alumno, Asignado, Curso, Gestion, Grado, Inscrito, Materia,
                     Nota, Pago, Profesor, Usuario, db)
from .security import log_accion, mi_alumno, scope_kind, validar_password
from .services import cursos_vigentes_ids

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


@auth_bp.route("/perfil")
@login_required
def perfil():
    return render_template("perfil.html")


@auth_bp.route("/cambiar-password", methods=["GET", "POST"])
@login_required  # disponible para TODOS los usuarios autenticados (no requiere permisos)
def cambiar_password():
    errores = []
    clave_limite = f"pw{current_user.id}"  # límite de intentos por usuario
    if request.method == "POST":
        actual, nueva, conf = (request.form.get(k, "") for k in ("actual", "nueva", "confirmar"))
        if _bloqueado(clave_limite):
            errores.append("Demasiados intentos fallidos. Espere unos minutos e intente de nuevo.")
        elif not current_user.check_password(actual):
            _intentos.setdefault(clave_limite, []).append(time())
            log_accion("ERROR", "auth", current_user.id, {"cambio_password": "contraseña actual incorrecta"}, status=401)
            db.session.commit()
            errores.append("La contraseña actual no es correcta.")
        else:
            errores = validar_password(nueva, current_user.usuario, actual)
            if nueva != conf:
                errores.append("La confirmación no coincide con la nueva contraseña.")
            if not errores:
                _intentos.pop(clave_limite, None)
                current_user.set_password(nueva)
                log_accion("UPDATE", "auth", current_user.id, {"cambio_password": "ok"})
                db.session.commit()
                flash("Contraseña actualizada correctamente.", "ok")
                return redirect(url_for("auth.cambiar_password"))
    return render_template("cambiar_password.html", errores=errores), (400 if errores else 200)


# -------------------------------------------------------------------- panel
@main_bp.route("/")
@login_required
def inicio():
    tiles = []
    for key, etiqueta in (("alumno", "Alumnos"), ("inscrito", "Inscripciones"), ("asignado", "Asignaciones"),
                          ("nota", "Calificaciones"), ("pago", "Pagos")):
        r = RESOURCES[key]
        if current_user.has_perm(r.perm("ver")):
            tiles.append((etiqueta, r.build_query({}, current_user).count(), url_for("crud.index", key=key), key))
    pendientes = None
    if current_user.has_perm("pago_ver"):
        pendientes = RESOURCES["pago"].build_query({}, current_user).filter(Pago.pagado.is_(False)).count()
    return render_template("dashboard.html", tiles=tiles, pendientes=pendientes, scope=scope_kind())


# -------------------------------------------- asignación masiva de profesor
def _opciones_form():
    return dict(
        cursos=[(c.id, str(c)) for c in Curso.query.filter(Curso.id.in_(cursos_vigentes_ids()))
                .order_by(Curso.paralelo).all()],  # solo cursos de la gestión vigente
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
    elif id_curso not in cursos_vigentes_ids():
        errors["id_curso"] = "Solo se puede asignar profesor en cursos de la gestión vigente"
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


# -------------------------------------------------------- Boletín de Calificaciones
def _roles_boletin_ok():
    return (current_user.has_role("Administrador")
            or current_user.has_role("Transcriptor")
            or current_user.has_role("Estudiante"))


def _opts_gestion_boletin():
    return [(g.id, f"{g.gestion} — {g.plan}")
            for g in Gestion.query.order_by(Gestion.gestion.desc(), Gestion.id.desc())]


def _opts_alumno_boletin(q=None):
    query = Alumno.query.filter(Alumno.activo.is_(True))
    if q:
        toks = [t.strip() for t in str(q).lower().split() if t.strip()][:6]
        for tok in toks:
            pat = f"%{tok}%"
            query = query.filter(
                or_(
                    db.func.lower(Alumno.nombre).like(pat),
                    db.func.lower(Alumno.paterno).like(pat),
                    db.func.lower(Alumno.materno).like(pat),
                    cast(Alumno.ci, String).like(pat),
                )
            )
    return [(a.id, f"{a.nombre_completo} (C.I. {a.ci or '—'})")
            for a in query.order_by(Alumno.paterno, Alumno.materno, Alumno.nombre).limit(40)]


def _cargar_boletin(id_alumno, id_gestion):
    """Devuelve (inscrito, filas_notas) o (None, mensaje_error)."""
    inscrito = (Inscrito.query
                .options(
                    joinedload(Inscrito.alumno),
                    joinedload(Inscrito.instrumento),
                    joinedload(Inscrito.curso).joinedload(Curso.gestion),
                    joinedload(Inscrito.curso).joinedload(Curso.grado),
                    selectinload(Inscrito.asignados)
                    .joinedload(Asignado.materia),
                    selectinload(Inscrito.asignados)
                    .joinedload(Asignado.nota),
                )
                .join(Curso, Inscrito.id_curso == Curso.id)
                .filter(Inscrito.id_alumno == id_alumno,
                        Curso.id_gestion == id_gestion,
                        Inscrito.inscrito.is_(True))
                .order_by(Inscrito.id.desc())
                .first())
    if not inscrito:
        return None, "No se encontró inscripción activa del estudiante en la gestión seleccionada."
    filas = []
    for i, asg in enumerate(sorted(inscrito.asignados,
                                   key=lambda a: (a.materia.sigla or "", a.materia.materia or "")), 1):
        n = asg.nota
        def _v(x):
            return x if x is not None else ""
        total = ""
        if n and n.nota_final is not None:
            try:
                tf = float(n.nota_final)
                total = int(tf) if tf == int(tf) else round(tf, 1)
            except (TypeError, ValueError):
                total = n.nota_final
        literal = ""
        if n is not None:
            if n.aprobado is True:
                literal = "APROBADO"
            elif n.aprobado is False:
                literal = "REPROBADO"
            elif any(x is not None for x in (n.nota1, n.nota2, n.nota3, n.nota4)):
                literal = "REPROBADO" if (float(n.nota_final or 0) == 0) else ""
        filas.append({
            "n": i,
            "codigo": asg.materia.sigla or "",
            "asignatura": asg.materia.materia or "",
            "p1": _v(n.nota1 if n else None),
            "p2": _v(n.nota2 if n else None),
            "p3": _v(n.nota3 if n else None),
            "p4": _v(n.nota4 if n else None),
            "total": total,
            "t2": _v(n.segunda_t if n else None),
            "literal": literal,
        })
    return inscrito, filas


@main_bp.route("/reportes/boletin", methods=["GET", "POST"])
@login_required
def boletin():
    if not _roles_boletin_ok():
        abort(403)
    if not current_user.has_perm("nota_ver"):
        abort(403)

    es_estudiante = current_user.has_role("Estudiante") and not (
        current_user.has_role("Administrador") or current_user.has_role("Transcriptor"))
    alumno_propio = mi_alumno() if es_estudiante else None
    errores = {}
    values = {"id_gestion": request.values.get("id_gestion", type=int),
              "id_alumno": request.values.get("id_alumno", type=int)}

    # Gestión activa por defecto
    if not values["id_gestion"]:
        ga = Gestion.query.filter(Gestion.activo.is_(True)).order_by(
            Gestion.gestion.desc(), Gestion.id.desc()).first()
        if ga:
            values["id_gestion"] = ga.id

    if es_estudiante:
        if not alumno_propio:
            abort(403)
        values["id_alumno"] = alumno_propio.id

    if request.method == "POST" or (es_estudiante and values["id_gestion"] and values["id_alumno"]):
        if not values["id_alumno"]:
            errores["id_alumno"] = "Seleccione un estudiante"
        if not values["id_gestion"]:
            errores["id_gestion"] = "Seleccione una gestión"
        if not errores:
            # Alcance: estudiante solo el suyo; admin/transcriptor cualquiera
            if es_estudiante and values["id_alumno"] != alumno_propio.id:
                abort(403)
            inscrito, resultado = _cargar_boletin(values["id_alumno"], values["id_gestion"])
            if inscrito is None:
                errores["general"] = resultado
            else:
                # Solo lectura: no se registra en audit_log (acciones permitidas:
                # CREATE, UPDATE, DELETE, LOGIN, LOGOUT, BULK, ERROR).
                return render_template(
                    "reportes/boletin_calificaciones.html",
                    inscrito=inscrito,
                    filas=resultado,
                    alumno=inscrito.alumno,
                    curso=inscrito.curso,
                    gestion=inscrito.curso.gestion,
                    grado=inscrito.curso.grado,
                    instrumento=inscrito.instrumento,
                )

    return render_template(
        "reportes/boletin_form.html",
        errores=errores,
        values=values,
        es_estudiante=es_estudiante,
        alumno_propio=alumno_propio,
        gestiones=_opts_gestion_boletin(),
        alumnos=_opts_alumno_boletin(request.args.get("q")) if not es_estudiante else [],
    )


@main_bp.route("/reportes/boletin/alumnos")
@login_required
def boletin_alumnos():
    """Opciones de alumnos para el selector (HTMX search)."""
    if not _roles_boletin_ok() or current_user.has_role("Estudiante"):
        abort(403)
    return render_template("crud/_options.html",
                           opciones=_opts_alumno_boletin(request.args.get("q")),
                           vacio="— Busque por nombre o C.I. —", actual="")

