import click
from flask import Flask, Response, render_template, request, url_for
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .models import Gestion, Permiso, Rol, Usuario, db

login_manager = LoginManager()
csrf = CSRFProtect()

GRUPOS_MENU = [
    ("Académico", ["gestion", "instrumento", "grado", "materia", "curso"]),
    ("Personas", ["alumno", "profesor"]),
    ("Procesos", ["inscrito", "asignado", "nota", "pago"]),
    ("Administración", ["usuario", "rol", "permiso", "auditoria"]),
]
ACADEMICOS = ["gestion", "instrumento", "grado", "materia", "curso"]


def create_app():
    from config import Config

    app = Flask(__name__)
    app.config.from_object(Config)
    import os
    if os.environ.get("TRUST_PROXY") == "1":  # detrás de Nginx
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Inicie sesión para continuar."

    @login_manager.user_loader
    def _load(uid):
        return db.session.get(Usuario, int(uid))

    @login_manager.unauthorized_handler
    def _no_auth():
        if request.headers.get("HX-Request"):  # sesión vencida durante una acción HTMX
            r = Response(status=401)
            r.headers["HX-Redirect"] = url_for("auth.login")
            return r
        from flask import redirect
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    from . import resources  # noqa: F401  (registra los recursos)
    from .crud import RESOURCES, bp as crud_bp
    from .views import acad_bp, auth_bp, cfg_bp, main_bp
    from . import config_notas

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(acad_bp)
    app.register_blueprint(cfg_bp)
    app.jinja_env.globals["estado_notas"] = config_notas.resumen
    app.register_blueprint(crud_bp)

    @app.context_processor
    def _ctx():
        grupos = []
        activas = []
        if current_user.is_authenticated:
            activas = Gestion.query.filter(Gestion.activo.is_(True)).order_by(Gestion.gestion.desc(), Gestion.id.desc()).all()
            for titulo, claves in GRUPOS_MENU:
                items = [(RESOURCES[k].title, url_for("crud.index", key=k), k) for k in claves
                         if current_user.has_perm(RESOURCES[k].perm("ver"))]
                if titulo == "Administración" and current_user.has_role("Administrador"):
                    items.insert(0, ("Fechas Transcripción Notas", url_for("cfgnotas.notas"), "cfg_notas"))
                if items:
                    grupos.append((titulo, items))
        # gestion_activa: la gestión vigente (si por datos antiguos hubiera varias, la más reciente)
        return {"menu": grupos, "gestion_activa": activas[0] if activas else None, "gestiones_activas": len(activas)}

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if current_user.is_authenticated:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def _error(code, msg):
        if request.headers.get("HX-Request"):
            return Response(msg, status=code)
        return render_template("error.html", code=code, msg=msg), code

    @app.errorhandler(CSRFError)
    def _csrf(e):
        return _error(400, "La sesión expiró o el formulario no es válido. Recargue la página.")

    @app.errorhandler(403)
    def _403(e):
        return _error(403, "No tiene permiso para acceder a este recurso.")

    @app.errorhandler(404)
    def _404(e):
        return _error(404, "El recurso solicitado no existe.")

    @app.errorhandler(500)
    def _500(e):
        db.session.rollback()
        return _error(500, "Ocurrió un error interno. Intente nuevamente.")

    register_cli(app)
    return app


# --------------------------------------------------------------------- CLI
def permisos_por_rol():
    from .crud import RESOURCES
    todos = [p for r in RESOURCES.values() for p in r.permisos_aplicables()]

    def ver(*claves):
        return [f"{k}_ver" for k in claves]

    return {
        "Administrador": todos,
        "Consulta": ver(*ACADEMICOS, "profesor", "alumno", "inscrito", "asignado", "nota", "pago"),
        "Transcriptor": ver(*ACADEMICOS, "profesor") + [
            "alumno_ver", "alumno_crear", "alumno_editar",
            "inscrito_ver", "inscrito_crear", "inscrito_editar",
            "asignado_ver", "asignado_crear", "asignado_editar", "nota_ver", "nota_editar"],
        "Profesor": ver(*ACADEMICOS) + ["alumno_ver", "inscrito_ver", "asignado_ver", "nota_ver", "nota_editar"],
        "Estudiante": ["alumno_ver", "inscrito_ver", "asignado_ver", "nota_ver", "pago_ver"],
    }


DESCRIPCION_ROL = {
    "Administrador": "Acceso total al sistema",
    "Profesor": "Califica y consulta únicamente sus materias asignadas",
    "Estudiante": "Consulta sus propias inscripciones, notas y pagos",
    "Consulta": "Solo lectura de la información académica y de pagos",
    "Transcriptor": "Registra alumnos, inscripciones, asignaciones y notas",
}


def register_cli(app):
    @app.cli.command("seed")
    def seed():
        """Crea roles base y permisos (idempotente). No pisa permisos ya personalizados."""
        from .crud import RESOURCES
        for r in RESOURCES.values():
            for p in r.permisos_aplicables():
                if not Permiso.query.filter_by(permiso=p).first():
                    accion = p.rsplit("_", 1)[1]
                    db.session.add(Permiso(permiso=p, descripcion=f"{accion.capitalize()} · {r.title}"))
        db.session.flush()
        mapa = permisos_por_rol()
        for nombre, perms in mapa.items():
            rol = Rol.query.filter_by(rol=nombre).first()
            nuevo = rol is None
            if nuevo:
                rol = Rol(rol=nombre, descripcion=DESCRIPCION_ROL[nombre])
                db.session.add(rol)
            if nuevo or nombre == "Administrador":  # el admin siempre conserva todos
                rol.permisos = Permiso.query.filter(Permiso.permiso.in_(perms)).all()
        db.session.commit()
        click.echo("Roles y permisos listos.")

    @app.cli.command("create-user")
    @click.argument("usuario")
    @click.option("--email", prompt=True)
    @click.option("--rol", "roles", multiple=True, default=["Administrador"], show_default=True,
                  help="Puede repetirse: --rol Profesor --rol Consulta")
    @click.password_option()
    def create_user(usuario, email, roles, password):
        """Crea un usuario (por ejemplo el primer administrador)."""
        if len(password) < 8:
            raise click.ClickException("La contraseña debe tener al menos 8 caracteres.")
        if Usuario.query.filter(db.func.lower(Usuario.usuario) == usuario.lower()).first():
            raise click.ClickException("Ese usuario ya existe.")
        objs = [Rol.query.filter_by(rol=r).first() for r in roles]
        if not all(objs):
            raise click.ClickException("Rol inexistente; ejecute primero: flask seed")
        u = Usuario(usuario=usuario, email=email, activo=True, roles=objs)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"Usuario {usuario} creado con roles: {', '.join(roles)}")
