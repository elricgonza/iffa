"""Modelos SQLAlchemy mapeados 1:1 a scripts/schema.sql."""
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP

from flask_login import UserMixin, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def _uid():
    """id del usuario autenticado (para la columna id_usuario)."""
    try:
        return current_user.id if current_user and current_user.is_authenticated else None
    except RuntimeError:  # fuera de un contexto de petición (CLI)
        return None


class Auditado:
    """Columnas de auditoría comunes (creado, act, id_usuario)."""
    creado = db.Column(db.DateTime, nullable=False, default=datetime.now)
    act = db.Column(db.DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    id_usuario = db.Column(db.Integer, nullable=False, default=_uid)


# ----------------------------------------------------------------- tablas puente
usuario_rol = db.Table(
    "usuario_rol",
    db.Column("id_usuario", db.Integer, db.ForeignKey("usuario.id"), primary_key=True),
    db.Column("id_rol", db.Integer, db.ForeignKey("rol.id"), primary_key=True),
)
rol_permiso = db.Table(
    "rol_permiso",
    db.Column("id_rol", db.Integer, db.ForeignKey("rol.id"), primary_key=True),
    db.Column("id_permiso", db.Integer, db.ForeignKey("permiso.id"), primary_key=True),
)
requisito = db.Table(
    "requisito",
    db.Column("id_materia", db.Integer, db.ForeignKey("materia.id"), primary_key=True),
    db.Column("id_materia_req", db.Integer, db.ForeignKey("materia.id"), primary_key=True),
)


# ----------------------------------------------------- usuarios, roles, permisos
class Permiso(db.Model):
    __tablename__ = "permiso"
    id = db.Column(db.Integer, primary_key=True)
    permiso = db.Column(db.String(50), unique=True, nullable=False)
    descripcion = db.Column(db.String(255))
    creado = db.Column(db.DateTime, default=datetime.now)
    act = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __str__(self):
        return self.permiso


class Rol(db.Model):
    __tablename__ = "rol"
    id = db.Column(db.Integer, primary_key=True)
    rol = db.Column(db.String(50), unique=True, nullable=False)
    descripcion = db.Column(db.String(255))
    creado = db.Column(db.DateTime, default=datetime.now)
    act = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    permisos = db.relationship("Permiso", secondary=rol_permiso, backref="roles")

    def __str__(self):
        return self.rol


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuario"
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    activo = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.Date, nullable=False, default=date.today)
    creado = db.Column(db.DateTime, default=datetime.now)
    act = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    roles = db.relationship("Rol", secondary=usuario_rol, backref="usuarios")

    @property
    def is_active(self):  # Flask-Login: una cuenta inactiva no puede iniciar sesión
        return bool(self.activo)

    def set_password(self, raw):
        self.password = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password, raw)

    def has_role(self, nombre):
        return any(r.rol == nombre for r in self.roles)

    def has_perm(self, permiso):
        if not hasattr(self, "_perm_cache"):
            self._perm_cache = {p.permiso for r in self.roles for p in r.permisos}
        return permiso in self._perm_cache

    def __str__(self):
        return self.usuario


# ------------------------------------------------------------------- académico
class Gestion(Auditado, db.Model):
    __tablename__ = "gestion"
    id = db.Column(db.Integer, primary_key=True)
    gestion = db.Column(db.SmallInteger)
    plan = db.Column(db.String(150), nullable=False)
    inicio = db.Column(db.Date, nullable=False)
    fin = db.Column(db.Date, nullable=False)
    activo = db.Column(db.Boolean, nullable=False, default=True)

    def __str__(self):
        return f"{self.gestion} - {self.plan}"


class Instrumento(Auditado, db.Model):
    __tablename__ = "instrumento"
    id = db.Column(db.Integer, primary_key=True)
    instrumento = db.Column(db.String(150), nullable=False)
    area = db.Column(db.String(50))

    def __str__(self):
        return self.instrumento


class Grado(Auditado, db.Model):
    __tablename__ = "grado"
    id = db.Column(db.Integer, primary_key=True)
    grado = db.Column(db.String(255), nullable=False)
    nivel = db.Column(db.String(150), nullable=False)
    id_instrumento = db.Column(db.Integer, db.ForeignKey("instrumento.id"), nullable=False)
    instrumento = db.relationship("Instrumento")

    def __str__(self):
        return f"{self.grado} ({self.instrumento})"


class Materia(Auditado, db.Model):
    __tablename__ = "materia"
    id = db.Column(db.Integer, primary_key=True)
    materia = db.Column(db.String(150), nullable=False)
    sigla = db.Column(db.String(20))
    id_grado = db.Column(db.Integer, db.ForeignKey("grado.id"), nullable=False)
    grado = db.relationship("Grado")
    requisitos = db.relationship(
        "Materia", secondary=requisito,
        primaryjoin=id == requisito.c.id_materia,
        secondaryjoin=id == requisito.c.id_materia_req,
    )

    def __str__(self):
        return f"{self.materia} ({self.grado.grado})"


class Profesor(Auditado, db.Model):
    __tablename__ = "profesor"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    paterno = db.Column(db.String(50))
    materno = db.Column(db.String(50))
    masculino = db.Column(db.Boolean, nullable=False, default=True)
    ci = db.Column(db.Integer)
    formacion = db.Column(db.String(100))
    email = db.Column(db.String(100))
    activo = db.Column(db.Boolean, nullable=False, default=True)
    usr_id_login = db.Column(db.Integer)

    @property
    def nombre_completo(self):
        return " ".join(p for p in (self.paterno, self.materno, self.nombre) if p)

    def __str__(self):
        return self.nombre_completo


class Curso(Auditado, db.Model):
    __tablename__ = "curso"
    id = db.Column(db.Integer, primary_key=True)
    curso = db.Column(db.String(150))
    paralelo = db.Column(db.String(50), nullable=False)
    aula = db.Column(db.String(50))
    capacidad = db.Column(db.SmallInteger)
    id_gestion = db.Column(db.Integer, db.ForeignKey("gestion.id"), nullable=False)
    id_grado = db.Column(db.Integer, db.ForeignKey("grado.id"), nullable=False)
    gestion = db.relationship("Gestion")
    grado = db.relationship("Grado")

    def __str__(self):
        return f"{self.curso or self.grado.grado} {self.paralelo} / {self.gestion.gestion}"


class Alumno(Auditado, db.Model):
    __tablename__ = "alumno"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    paterno = db.Column(db.String(100))
    materno = db.Column(db.String(100))
    nacimiento = db.Column(db.Date)
    masculino = db.Column(db.Boolean, nullable=False, default=True)
    ci = db.Column(db.Integer)
    direccion = db.Column(db.String(150))
    email = db.Column(db.String(100))
    activo = db.Column(db.Boolean, nullable=False, default=True)
    obs = db.Column(db.String(100))
    usr_id_login = db.Column(db.Integer)
    foto_ruta = db.Column(db.String(150))

    @property
    def nombre_completo(self):
        return " ".join(p for p in (self.paterno, self.materno, self.nombre) if p)

    def __str__(self):
        return self.nombre_completo


class Inscrito(Auditado, db.Model):
    __tablename__ = "inscrito"
    id = db.Column(db.Integer, primary_key=True)
    reserva = db.Column(db.Boolean, default=False)
    inscrito = db.Column(db.Boolean, nullable=False, default=True)
    descuento = db.Column(db.SmallInteger, nullable=False, default=0)
    motivo_descuento = db.Column(db.String(100))
    abandono = db.Column(db.Boolean, nullable=False, default=False)
    obs = db.Column(db.String(200))
    otras_materias = db.Column(db.Boolean, nullable=False, default=False)
    id_alumno = db.Column(db.Integer, db.ForeignKey("alumno.id"), nullable=False)
    id_curso = db.Column(db.Integer, db.ForeignKey("curso.id"), nullable=False)
    id_instrumento = db.Column(db.Integer, db.ForeignKey("instrumento.id"), nullable=False)
    alumno = db.relationship("Alumno")
    curso = db.relationship("Curso")
    instrumento = db.relationship("Instrumento")
    pagos = db.relationship("Pago", back_populates="inscrito")
    asignados = db.relationship("Asignado", back_populates="inscrito")

    def __str__(self):
        return f"{self.alumno} — {self.curso}"


class Asignado(Auditado, db.Model):
    __tablename__ = "asignado"
    id = db.Column(db.Integer, primary_key=True)
    id_inscrito = db.Column(db.Integer, db.ForeignKey("inscrito.id"), nullable=False)
    id_curso = db.Column(db.Integer, db.ForeignKey("curso.id"), nullable=False)
    id_materia = db.Column(db.Integer, db.ForeignKey("materia.id"), nullable=False)
    id_profesor = db.Column(db.Integer, db.ForeignKey("profesor.id"))
    inscrito = db.relationship("Inscrito", back_populates="asignados")
    curso = db.relationship("Curso")
    materia = db.relationship("Materia")
    profesor = db.relationship("Profesor")
    nota = db.relationship("Nota", back_populates="asignado", uselist=False)

    def __str__(self):
        return f"{self.inscrito.alumno} / {self.materia.materia}"


def _redondear(valor, decimales):
    q = Decimal(1).scaleb(-decimales)
    return Decimal(str(valor)).quantize(q, rounding=ROUND_HALF_UP)


class Nota(Auditado, db.Model):
    __tablename__ = "nota"
    id = db.Column(db.Integer, primary_key=True)
    nota1 = db.Column(db.SmallInteger)
    nota2 = db.Column(db.SmallInteger)
    nota3 = db.Column(db.SmallInteger)
    nota4 = db.Column(db.SmallInteger)
    nota_final = db.Column(db.Numeric(5, 1))
    segunda_t = db.Column("2t", db.SmallInteger)  # segundo turno / instancia
    nota_aprob = db.Column(db.SmallInteger)
    aprobado = db.Column(db.Boolean)
    obs = db.Column(db.String(150))
    id_asignado = db.Column(db.Integer, db.ForeignKey("asignado.id"), nullable=False)
    asignado = db.relationship("Asignado", back_populates="nota")

    def calcular(self, minimo):
        """nota_final = promedio de las notas registradas; si reprueba y existe
        nota de segundo turno (2t), ésta es la nota de aprobación."""
        vals = [n for n in (self.nota1, self.nota2, self.nota3, self.nota4) if n is not None]
        if not vals:
            self.nota_final = self.nota_aprob = self.aprobado = None
            return
        self.nota_final = _redondear(sum(vals) / len(vals), 1)
        base = int(_redondear(self.nota_final, 0))
        if base < minimo and self.segunda_t is not None:
            base = self.segunda_t
        self.nota_aprob = base
        self.aprobado = base >= minimo


class Pago(Auditado, db.Model):
    __tablename__ = "pago"
    id = db.Column(db.Integer, primary_key=True)
    cuota = db.Column(db.Float, nullable=False)
    pagado = db.Column(db.Boolean, default=False)
    metodo_pago = db.Column(db.String(50))
    fecha_pago = db.Column(db.DateTime)
    referencia_pago = db.Column(db.String(100))
    obs = db.Column(db.String(100))
    id_inscrito = db.Column(db.Integer, db.ForeignKey("inscrito.id"), nullable=False)
    inscrito = db.relationship("Inscrito", back_populates="pagos")


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(80), nullable=False, default="anónimo")
    accion = db.Column(db.String(20), nullable=False)
    modulo = db.Column(db.String(50), nullable=False)
    entidad_id = db.Column(db.Integer)
    detalle = db.Column(db.Text)
    ip = db.Column(db.String(45))
    endpoint = db.Column(db.String(150))
    metodo = db.Column(db.String(10))
    status = db.Column(db.SmallInteger, default=200)
    creado = db.Column(db.DateTime, nullable=False, default=datetime.now)
    id_usuario = db.Column(db.Integer, db.ForeignKey("usuario.id"))
