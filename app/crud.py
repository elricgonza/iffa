"""Motor CRUD genérico: cada recurso se declara una vez (resources.py) y obtiene
listado paginado con búsqueda/filtros, formulario modal, validación, permisos,
alcance de datos por rol y auditoría. Todo vía HTMX (sin recargar la página)."""
import json
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from flask import Blueprint, abort, current_app, make_response, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import MultiDict

from .models import db
from .security import log_accion

bp = Blueprint("crud", __name__)
RESOURCES: dict = {}


# ------------------------------------------------------------------ definiciones
@dataclass
class Field:
    name: str
    label: str
    type: str = "text"  # text textarea email number decimal date datetime bool select multiselect password
    required: bool = False
    req_create: bool = False  # obligatorio solo al crear (p. ej. contraseña)
    options: Optional[Callable] = None  # -> [(valor, etiqueta)]
    target: Any = None  # modelo destino (multiselect)
    attr: Optional[str] = None  # atributo del modelo si difiere del nombre
    virtual: bool = False  # no se guarda en el modelo (lo usan los hooks)
    create_only: bool = False
    help: str = ""
    min: Optional[float] = None
    max: Optional[float] = None
    default: Any = None
    maxlength: Optional[int] = None
    depends_on: Optional[str] = None  # select dependiente: nombre del campo padre
    dep_options: Optional[Callable] = None  # (valor_padre) -> [(valor, etiqueta)]
    dep_strict: bool = True  # False: dep_options solo sugiere; se valida contra options() y la regla da el motivo
    dep_include: tuple = ()  # otros campos cuyo valor necesita dep_options (se envían con la petición)
    auto_unico: bool = False  # si dep_options devuelve una sola opción, se preselecciona
    search: Optional[Callable] = None  # type="search": (texto) -> [(id, etiqueta)]
    label_fn: Optional[Callable] = None  # etiqueta de un registro (type="search")
    check: Optional[Callable] = None  # validación extra del registro elegido (type="search")
    locked: Optional[Callable] = None  # (obj) -> motivo (str) si el campo está bloqueado para el usuario, o None

    @property
    def a(self):
        return self.attr or self.name

    def opciones_dep(self, padre, params=None):
        """Opciones de un select dependiente: dep_options(valor_padre, valores_de_otros_campos)."""
        return self.dep_options(padre, params or {})


@dataclass
class Col:
    label: str
    getter: Any  # "ruta.punteada" o callable(obj)
    kind: str = "text"  # text bool date datetime money
    hide: bool = False  # oculta en pantallas pequeñas


@dataclass
class Filter:
    name: str
    label: str
    options: Callable
    apply: Callable  # (query, valor_str) -> query
    default: Optional[str] = None  # valor aplicado cuando el parámetro no viene en la URL


def _dotted(obj, path):
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part)
    return obj


class Resource:
    def __init__(self, key, model, title, singular, fields, columns, *, query=None,
                 search=None, filters=(), order=None, scope=None, can_edit=None,
                 can_delete=None, allow_create=True, allow_edit=True, allow_delete=True,
                 validate=None, before_save=None, after_save=None, before_delete=None,
                 toolbar=None, info=None, solo_admin=False, form_size="max-w-2xl"):
        self.key, self.model, self.title, self.singular = key, model, title, singular
        self.fields, self.columns, self.filters = fields, columns, list(filters)
        self.query, self.search, self.order, self.scope = query, search, order, scope
        self._can_edit, self._can_delete = can_edit, can_delete
        self.allow_create, self.allow_edit, self.allow_delete = allow_create, allow_edit, allow_delete
        self.validate, self.before_save, self.after_save = validate, before_save, after_save
        self.before_delete, self.toolbar, self.form_size = before_delete, toolbar, form_size
        self.info = info  # plantilla opcional con información bajo el título del listado
        self.solo_admin = solo_admin  # crear/editar/eliminar solo para el rol Administrador (además del permiso)
        RESOURCES[key] = self

    # ---- permisos
    def perm(self, accion):
        return f"{self.key}_{accion}"

    def permisos_aplicables(self):
        p = ["ver"]
        if self.allow_create:
            p.append("crear")
        if self.allow_edit:
            p.append("editar")
        if self.allow_delete:
            p.append("eliminar")
        return [self.perm(x) for x in p]

    def _admin_ok(self, user):
        return not self.solo_admin or user.has_role("Administrador")

    def puede_crear(self, user):
        return self.allow_create and user.has_perm(self.perm("crear")) and self._admin_ok(user)

    def puede_editar(self, obj, user):
        return (self.allow_edit and user.has_perm(self.perm("editar")) and self._admin_ok(user)
                and (self._can_edit(obj, user) if self._can_edit else True))

    def puede_eliminar(self, obj, user):
        return (self.allow_delete and user.has_perm(self.perm("eliminar")) and self._admin_ok(user)
                and (self._can_delete(obj, user) if self._can_delete else True))

    # ---- consultas
    def build_query(self, args, user, defaults=True):
        q = self.query() if self.query else self.model.query
        if self.scope:
            q = self.scope(q)
        for f in self.filters:
            v = args.get(f.name)
            if v is None and defaults:
                v = f.default
            if v and v != "todas":
                try:
                    q = f.apply(q, v)
                except ValueError:  # parámetro manipulado: se vuelve al valor por defecto
                    if f.default and v != f.default:
                        q = f.apply(q, f.default)
        s = (args.get("q") or "").strip()
        if s and self.search:
            q = q.filter(self.search(s))
        if self.order:
            q = q.order_by(*self.order())
        return q

    def paginate(self, args, user):
        page = max(int(args.get("page", 1) or 1), 1) if str(args.get("page", 1)).isdigit() else 1
        return self.build_query(args, user).paginate(
            page=page, per_page=current_app.config["PER_PAGE"], error_out=False)

    def get_visible(self, oid):
        """Obtiene el registro solo si está dentro del alcance del usuario."""
        obj = self.build_query({}, current_user, defaults=False).filter(self.model.id == oid).first()
        if obj is None:
            abort(404)
        return obj

    # ---- presentación
    def display(self, obj, col):
        v = col.getter(obj) if callable(col.getter) else _dotted(obj, col.getter)
        if v is None or v == "":
            return "—"
        if col.kind == "bool":
            return "Sí" if v else "No"
        if col.kind == "date":
            return v.strftime("%d/%m/%Y")
        if col.kind == "datetime":
            return v.strftime("%d/%m/%Y %H:%M")
        if col.kind == "money":
            return f"{float(v):,.2f}"
        return str(v)

    def dep_ops(self, f, values):
        """Opciones iniciales de un select dependiente según los valores actuales del formulario."""
        padre = values.get(f.depends_on)
        return f.opciones_dep(padre, values) if padre else []

    def bloqueo(self, f, obj):
        """Motivo por el que el campo está bloqueado para el usuario actual (o None)."""
        return f.locked(obj) if (f.locked and obj is not None) else None

    def search_label(self, f, raw):
        """Etiqueta del registro elegido en un campo type="search"."""
        if not raw or not str(raw).isdigit():
            return ""
        o = db.session.get(f.target, int(raw))
        if o is None:
            return ""
        return f.label_fn(o) if f.label_fn else str(o)

    def related_label(self, obj, f):
        """Texto del registro relacionado (id_curso -> obj.curso) para campos solo de alta."""
        rel = getattr(obj, f.a[3:], None) if f.a.startswith("id_") else None
        return str(rel) if rel is not None else ""

    def raw(self, obj, col):
        return col.getter(obj) if callable(col.getter) else _dotted(obj, col.getter)

    # ---- formularios
    def initial_values(self, obj):
        vals = {}
        for f in self.fields:
            if obj is None:
                v = f.default
            elif f.virtual:
                v = f.default
            else:
                v = getattr(obj, f.a)
            if f.type == "multiselect":
                vals[f.name] = [str(getattr(x, "id", x)) for x in (v or [])]
            elif f.type == "bool":
                vals[f.name] = bool(v)
            elif f.type == "datetime" and v:
                vals[f.name] = v.strftime("%Y-%m-%dT%H:%M")
            elif f.type == "date" and v:
                vals[f.name] = v.isoformat()
            elif f.type == "password":
                vals[f.name] = ""
            else:
                vals[f.name] = "" if v is None else str(v)
        return vals

    def form_values(self, form):
        vals = {}
        for f in self.fields:
            if f.type == "multiselect":
                vals[f.name] = form.getlist(f.name)
            elif f.type == "bool":
                vals[f.name] = form.get(f.name) is not None
            elif f.type == "password":
                vals[f.name] = ""
            else:
                vals[f.name] = form.get(f.name, "")
        return vals

    def _max_len(self, f):
        if f.maxlength:
            return f.maxlength
        try:
            col = sa_inspect(self.model).attrs[f.a].columns[0]
            return getattr(col.type, "length", None)
        except Exception:
            return None

    def parse(self, form, obj):
        data, errors = {}, {}
        for f in self.fields:
            if f.create_only and obj is not None:
                continue
            if f.locked and obj is not None and f.locked(obj):
                continue  # campo bloqueado: se conserva el valor actual, aunque se manipule el formulario
            needed = f.required or (f.req_create and obj is None)
            try:
                if f.type == "multiselect":
                    raw = form.getlist(f.name)
                    validos = {str(v) for v, _ in f.options()}
                    if any(x not in validos for x in raw):
                        raise ValueError("Selección no válida")
                    val = [int(x) for x in raw]
                    empty = not val
                elif f.type == "bool":
                    val, empty = form.get(f.name) is not None, False
                else:
                    raw = (form.get(f.name) or "").strip()
                    empty = raw == ""
                    val = None if empty else self._coerce(f, raw, form)
            except ValueError as e:
                errors[f.name] = str(e) or "Valor no válido"
                continue
            if needed and empty:
                errors[f.name] = "Campo obligatorio"
                continue
            if isinstance(val, str):
                ml = self._max_len(f)
                if ml and len(val) > ml:
                    errors[f.name] = f"Máximo {ml} caracteres"
                    continue
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                if f.min is not None and val < f.min:
                    errors[f.name] = f"El valor mínimo es {f.min:g}"
                    continue
                if f.max is not None and val > f.max:
                    errors[f.name] = f"El valor máximo es {f.max:g}"
                    continue
            data[f.name] = val
        return data, errors

    def _coerce(self, f, raw, form=None):
        t = f.type
        if t in ("text", "textarea", "password"):
            return raw
        if t == "email":
            if "@" not in raw or " " in raw:
                raise ValueError("Correo electrónico no válido")
            return raw
        if t == "number":
            try:
                return int(raw)
            except ValueError:
                raise ValueError("Debe ser un número entero")
        if t == "decimal":
            try:
                return float(raw.replace(",", "."))
            except ValueError:
                raise ValueError("Debe ser un número")
        if t == "date":
            try:
                return date.fromisoformat(raw)
            except ValueError:
                raise ValueError("Fecha no válida")
        if t == "datetime":
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                raise ValueError("Fecha y hora no válidas")
        if t == "search":
            if not raw.isdigit():
                raise ValueError("Selección no válida")
            o = db.session.get(f.target, int(raw))
            if o is None or (f.check and not f.check(o)):
                raise ValueError("Selección no válida")
            return int(raw)
        if t == "select":
            if f.depends_on and f.dep_strict:  # opciones según el campo padre (p. ej. cursos del instrumento)
                opciones = f.opciones_dep(form.get(f.depends_on) if form else None, form)
            else:
                opciones = f.options()
            validos = {str(v) for v, _ in opciones}
            if raw not in validos:
                raise ValueError("Selección no válida")
            return int(raw) if raw.lstrip("-").isdigit() else raw
        raise ValueError("Tipo de campo desconocido")

    def apply(self, obj, data):
        for f in self.fields:
            if f.virtual or f.type == "password" or f.name not in data:
                continue
            if f.type == "multiselect":
                setattr(obj, f.a, [db.session.get(f.target, i) for i in data[f.name]])
            else:
                setattr(obj, f.a, data[f.name])


# ------------------------------------------------------------------- utilidades
def _res(key):
    r = RESOURCES.get(key)
    if r is None:
        abort(404)
    return r


def _need(r, accion):
    if not current_user.has_perm(r.perm(accion)):
        abort(403)


def _list_args():
    """Parámetros del listado desde el que se abrió el modal (HX-Current-URL)."""
    cur = request.headers.get("HX-Current-URL", "")
    qs = parse_qs(urlparse(cur).query)
    return MultiDict({k: v[0] for k, v in qs.items()})


def _toast_headers(resp, msg=None, kind="ok", close=False):
    ev = {}
    if msg:
        ev["toast"] = {"msg": msg, "type": kind}
    if close:
        ev["close-modal"] = True
    if ev:
        resp.headers["HX-Trigger"] = json.dumps(ev)  # ASCII (escapa acentos)
    return resp


def _render_table(r, args=None, msg=None, kind="ok", close=False):
    args = args if args is not None else _list_args()
    html = render_template("crud/_table.html", r=r, pagination=r.paginate(args, current_user), args=args)
    resp = make_response(html)
    resp.headers["HX-Retarget"] = "#table-wrap"
    resp.headers["HX-Reswap"] = "innerHTML"
    return _toast_headers(resp, msg, kind, close)


def _render_form(r, obj, values, errors):
    return render_template("crud/_form.html", r=r, obj=obj, values=values, errors=errors)


# ------------------------------------------------------------------------ rutas
@bp.route("/<key>/")
@login_required
def index(key):
    r = _res(key)
    _need(r, "ver")
    pagination = r.paginate(request.args, current_user)
    tpl = "crud/_table.html" if request.headers.get("HX-Target") == "table-wrap" else "crud/list.html"
    return render_template(tpl, r=r, pagination=pagination, args=request.args)


def _necesita_form(r):
    if not (current_user.has_perm(r.perm("crear")) or current_user.has_perm(r.perm("editar"))):
        abort(403)


VACIO_DEP = "— Seleccione primero el campo anterior —"


def _oob_hijos(r, f, valor, params):
    """Selects descendientes de f que deben actualizarse (hx-swap-oob) cuando f cambia."""
    salida = []
    for h in [x for x in r.fields if x.depends_on == f.name]:
        p = dict(params)
        p[f.name] = valor or ""
        ops = h.opciones_dep(valor, p) if valor else []
        unico = ops[0][0] if (h.auto_unico and len(ops) == 1) else ""
        vacio = VACIO_DEP if not valor else ("— Seleccione —" if ops else "— Sin opciones disponibles —")
        salida.append({"name": h.name, "opciones": ops, "vacio": vacio, "actual": unico})
        salida += _oob_hijos(r, h, unico or None, p)
    return salida


@bp.route("/<key>/opciones/<fname>")
@login_required
def opciones(key, fname):
    """Opciones de un select dependiente (p. ej. cursos según el instrumento elegido).
    También devuelve (hx-swap-oob) los selects que dependen de éste, ya reiniciados o actualizados."""
    r = _res(key)
    _necesita_form(r)
    f = next((x for x in r.fields if x.name == fname and x.depends_on), None) or abort(404)
    params = request.args.to_dict()
    padre = params.get(f.depends_on)
    ops = f.opciones_dep(padre, params) if padre else []
    actual = ops[0][0] if (f.auto_unico and len(ops) == 1) else ""
    vacio = VACIO_DEP if not padre else ("— Seleccione —" if ops else "— Sin opciones disponibles —")
    return render_template("crud/_options.html", opciones=ops, vacio=vacio, actual=actual,
                           oob=_oob_hijos(r, f, actual or None, params))


@bp.route("/<key>/buscar/<fname>")
@login_required
def buscar(key, fname):
    """Búsqueda en vivo para campos type="search" (p. ej. alumno por nombre, apellido o C.I.)."""
    r = _res(key)
    _necesita_form(r)
    f = next((x for x in r.fields if x.name == fname and x.type == "search"), None) or abort(404)
    texto = (request.args.get("q") or "").strip()
    if len(texto) < 2:
        return render_template("crud/_search_results.html", items=[], msg="Escriba al menos 2 caracteres")
    return render_template("crud/_search_results.html", items=f.search(texto)[:15], msg=None)


@bp.route("/<key>/nuevo")
@login_required
def nuevo(key):
    r = _res(key)
    _need(r, "crear")
    if not r.puede_crear(current_user):
        abort(403)
    return _render_form(r, None, r.initial_values(None), {})


@bp.route("/<key>/<int:oid>/editar")
@login_required
def editar(key, oid):
    r = _res(key)
    _need(r, "editar")
    obj = r.get_visible(oid)
    if not r.puede_editar(obj, current_user):
        abort(403)
    return _render_form(r, obj, r.initial_values(obj), {})


@bp.post("/<key>/guardar")
@login_required
def guardar(key):
    r = _res(key)
    oid = request.form.get("id", type=int)
    if oid:
        _need(r, "editar")
        obj = r.get_visible(oid)
        if not r.puede_editar(obj, current_user):
            abort(403)
    else:
        _need(r, "crear")
        if not r.puede_crear(current_user):
            abort(403)
        obj = None

    data, errors = r.parse(request.form, obj)
    if not errors and r.validate:
        errors = r.validate(data, obj) or {}
    if errors:
        return _render_form(r, obj, r.form_values(request.form), errors)

    nuevo_reg = obj is None
    try:
        if nuevo_reg:
            obj = r.model()
        r.apply(obj, data)
        if r.before_save:
            r.before_save(obj, data)
        if nuevo_reg:
            db.session.add(obj)
        db.session.flush()
        if r.after_save:
            r.after_save(obj, data, nuevo_reg)
        log_accion("CREATE" if nuevo_reg else "UPDATE", r.key, obj.id, {"registro": str(obj)[:120]})
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return _render_form(r, obj if not nuevo_reg else None, r.form_values(request.form), {"__all__": str(e)})
    except IntegrityError:
        db.session.rollback()
        return _render_form(r, obj if not nuevo_reg else None, r.form_values(request.form),
                            {"__all__": "No se pudo guardar: el valor ya existe o viola una restricción de integridad."})
    return _render_table(r, msg=f"{r.singular} guardado correctamente", close=True)


@bp.post("/<key>/<int:oid>/eliminar")
@login_required
def eliminar(key, oid):
    r = _res(key)
    _need(r, "eliminar")
    obj = r.get_visible(oid)
    if not r.puede_eliminar(obj, current_user):
        abort(403)
    try:
        desc = str(obj)[:120]
        if r.before_delete:
            r.before_delete(obj)
        db.session.delete(obj)
        log_accion("DELETE", r.key, oid, {"registro": desc})
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        return _render_table(r, msg=str(e), kind="error")
    except IntegrityError:
        db.session.rollback()
        return _render_table(r, msg="No se puede eliminar: tiene registros relacionados.", kind="error")
    return _render_table(r, msg=f"{r.singular} eliminado")
