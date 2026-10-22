"""Prueba de humo de extremo a extremo (usa la base de DATABASE_URL).

ATENCIÓN: crea registros de prueba y al final BORRA TODOS los datos de las tablas
académicas. Ejecutar solo contra una base de pruebas:
    SMOKE_TEST_CONFIRM=yes python tests/smoke_test.py
"""
import os
import sys

if os.environ.get("SMOKE_TEST_CONFIRM") != "yes":
    sys.exit("Defina SMOKE_TEST_CONFIRM=yes (usar solo en una base de pruebas).")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402
from app.models import (Alumno, Asignado, Curso, Gestion, Inscrito, Nota, Pago, Profesor, Rol, Usuario, db)  # noqa: E402

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
HX = {"HX-Request": "true", "HX-Target": "table-wrap"}
ok = lambda c, m: print(("OK   " if c else "FAIL ") + m) or (c or sys.exit(1))


def login(user, pw):
    c = app.test_client()
    r = c.post("/login", data={"usuario": user, "password": pw})
    return c, r


def db_(fn):
    """Ejecuta una consulta en su propio contexto (evita compartir g entre peticiones)."""
    with app.app_context():
        return fn()


def post(c, key, **data):
    data = {k: ("on" if v is True else v) for k, v in data.items() if v is not False}
    return c.post(f"/{key}/guardar", data=data, headers={"HX-Request": "true"})


def _limpiar():
    db.session.execute(db.text("TRUNCATE nota,pago,asignado,inscrito,alumno,profesor,curso,requisito,materia,"
                               "grado,instrumento,gestion RESTART IDENTITY CASCADE"))
    for sq in ("nota", "pago", "asignado", "inscrito", "alumno", "profesor", "curso", "materia", "grado",
               "instrumento", "gestion"):  # las secuencias no pertenecen a las columnas: reinicio manual
        db.session.execute(db.text(f"ALTER SEQUENCE {sq}_id_seq RESTART WITH 1"))
    db.session.execute(db.text("DELETE FROM usuario_rol WHERE id_usuario IN (SELECT id FROM usuario WHERE usuario<>'admin')"))
    db.session.execute(db.text("DELETE FROM audit_log WHERE id_usuario IN (SELECT id FROM usuario WHERE usuario<>'admin')"))
    db.session.execute(db.text("DELETE FROM usuario WHERE usuario<>'admin'"))
    db.session.commit()


if True:
    db_(_limpiar)
    admin, r = login("admin", "Admin12345")
    ok(r.status_code == 302, "login administrador")
    ok(login("admin", "mala")[1].status_code == 401, "login incorrecto rechazado")
    ok(admin.get("/").status_code == 200, "panel")

    # catálogos
    post(admin, "gestion", gestion=2026, plan="Plan 2026", inicio="2026-02-01", fin="2026-12-01", activo=True)
    post(admin, "instrumento", instrumento="Piano", area="Cuerdas")
    r = post(admin, "grado", id_instrumento=1, nivel="Básico", grado="Piano I")
    ok(b"guardado" in r.headers.get("HX-Trigger", "").encode() or r.status_code == 200, "grado creado")
    post(admin, "materia", id_grado=1, materia="Lenguaje Musical", sigla="LM1")
    post(admin, "materia", id_grado=1, materia="Armonía", sigla="AR1")
    r = admin.post("/materia/guardar", data={"id_grado": 1, "materia": "Armonía II", "sigla": "AR2", "requisitos": [2]},
                   headers={"HX-Request": "true"})
    ok(r.status_code == 200, "materia con requisito")
    post(admin, "curso", id_gestion=1, id_grado=1, paralelo="A", capacidad=1)

    # usuarios y vínculos
    for nombre, rol in (("profe1", "Profesor"), ("profe2", "Profesor"), ("alu1", "Estudiante")):
        rid = db_(lambda: Rol.query.filter_by(rol=rol).first().id)
        r = admin.post("/usuario/guardar", data={"usuario": nombre, "email": f"{nombre}@x.com",
                       "password": "Clave12345", "activo": "on", "roles": [rid]}, headers={"HX-Request": "true"})
    ok(db_(lambda: Usuario.query.filter_by(usuario="profe2").first()) is not None, "usuarios creados")
    u = db_(lambda: {x.usuario: x.id for x in Usuario.query.all()})
    post(admin, "profesor", nombre="Ana", paterno="Pérez", masculino=False, activo=True, usr_id_login=u["profe1"])
    post(admin, "profesor", nombre="Luis", paterno="Gómez", masculino=True, activo=True)  # profe2 sin vincular
    post(admin, "alumno", nombre="Carla", paterno="Rojas", masculino=False, activo=True, usr_id_login=u["alu1"])
    post(admin, "alumno", nombre="Diego", paterno="Vega", masculino=True, activo=True)
    r = post(admin, "alumno", nombre="Otro", paterno="Dup", masculino=True, activo=True, usr_id_login=u["alu1"])
    ok(b"ya est" in r.data, "vínculo duplicado alumno-usuario rechazado")

    # inscripción -> pagos y asignados
    r = post(admin, "inscrito", id_alumno=1, id_curso=1, id_instrumento=1, inscrito=True, descuento=10,
             motivo_descuento="Hermanos", cuota_base="")
    pagos = db_(lambda: [p.cuota for p in Pago.query.order_by(Pago.id)])
    ok(len(pagos) == 2 and abs(sum(pagos) - 450) < 0.01, "pago generado con 10% descuento (450 en 2 cuotas)")
    ok(db_(lambda: (Asignado.query.count(), Nota.query.count())) == (3, 3), "materias del grado asignadas + notas vacías")
    r = post(admin, "inscrito", id_alumno=2, id_curso=1, id_instrumento=1, inscrito=True, descuento=0)
    ok(b"cupos" in r.data, "capacidad del curso respetada")
    r = post(admin, "inscrito", id_alumno=1, id_curso=1, id_instrumento=1, inscrito=True, descuento=0)
    ok(b"ya est" in r.data, "inscripción duplicada rechazada")

    # ---- Inscripciones: gestión activa por defecto, curso según instrumento, búsqueda de alumno
    post(admin, "instrumento", instrumento="Guitarra", area="Cuerdas")                     # id 2
    post(admin, "gestion", gestion=2025, plan="Plan 2025", inicio="2025-02-01", fin="2025-12-01")  # id 2, inactiva
    def _ins_vieja():
        db.session.add(Curso(curso=None, paralelo="B", id_gestion=2, id_grado=1, id_usuario=1))
        db.session.flush()
        db.session.add(Inscrito(id_alumno=2, id_curso=2, id_instrumento=1, inscrito=True, abandono=False,
                                descuento=0, id_usuario=1))
        db.session.commit()
    db_(_ins_vieja)
    r = admin.get("/inscrito/", headers=HX)
    ok(b"Rojas" in r.data and b"Vega" not in r.data, "listado: por defecto solo la gestión activa")
    ok(b"Vega" in admin.get("/inscrito/?id_gestion=todas", headers=HX).data, "listado: opción «todas las gestiones»")
    ok(b"Vega" in admin.get("/inscrito/?id_gestion=2", headers=HX).data, "listado: filtro por gestión anterior")
    ok(b"Vega" not in admin.get("/inscrito/?id_gestion=abc", headers=HX).data, "parámetro manipulado no rompe (500)")
    page = admin.get("/inscrito/").data
    ok(b'value="activa" selected' in page, "filtro de gestión preseleccionado en «Gestión activa»")
    ok(admin.get("/inscrito/2/editar").status_code == 200, "se puede editar una inscripción de gestión anterior")
    ok(b"Vega" in admin.get("/inscrito/?q=vega&id_gestion=todas", headers=HX).data, "búsqueda en listado")

    r = admin.get("/inscrito/opciones/id_curso?id_instrumento=1", headers={"HX-Request": "true"})
    ok(b"Piano I A" in r.data and b"Piano I B" not in r.data, "cursos del instrumento solo de la gestión activa")
    r = admin.get("/inscrito/opciones/id_curso?id_instrumento=2", headers={"HX-Request": "true"})
    ok(b"Sin opciones" in r.data, "instrumento sin cursos: aviso")
    r = post(admin, "inscrito", id_alumno=2, id_curso=1, id_instrumento=2, inscrito=True, descuento=0)
    ok(b"no v" in r.data, "curso de otro instrumento rechazado por el servidor")
    r = post(admin, "inscrito", id_alumno=2, id_curso=2, id_instrumento=1, inscrito=True, descuento=0)
    ok(b"no v" in r.data, "curso de gestión no activa rechazado por el servidor")
    nuevo = admin.get("/inscrito/nuevo").data
    ok(b"searchSelect" in nuevo and b"crud/opciones" in nuevo.replace(b"/inscrito/opciones", b"crud/opciones"),
       "formulario: buscador de alumno + curso dependiente")

    post(admin, "alumno", nombre="José", paterno="Muñoz", materno="Díaz", masculino=True, activo=True, ci=1234567)  # id 3
    post(admin, "alumno", nombre="Inactivo", paterno="Muñoz", masculino=True, ci=7654321)                              # id 4
    bus = lambda q: admin.get(f"/inscrito/buscar/id_alumno?q={q}", headers={"HX-Request": "true"}).data
    ok(b"Rojas" in bus("rojas") and b"C.I." in bus("rojas"), "buscar alumno por apellido (muestra C.I.)")
    ok(b"Rojas" in bus("carla%20roj"), "buscar alumno con varias palabras parciales")
    ok("Muñoz".encode() in bus("jose%20munoz"), "buscar sin distinguir tildes ni mayúsculas")
    ok("Muñoz".encode() in bus("MU%C3%91OZ"), "buscar con la ñ")
    ok("José".encode() in bus("123456"), "buscar alumno por C.I.")
    ok(b"Inactivo" not in bus("munoz"), "alumnos inactivos no aparecen")
    ok(b"al menos 2" in bus("a"), "mínimo 2 caracteres")
    ok(b"Sin resultados" in bus("zzzz"), "sin resultados")
    ok(b"Rojas" in admin.get("/alumno/?q=rojas%20carla", headers=HX).data, "listado de alumnos: búsqueda multipalabra")
    r = post(admin, "inscrito", id_alumno=4, id_curso=1, id_instrumento=1, inscrito=True, descuento=0)
    ok(b"no v" in r.data, "alumno inactivo no puede inscribirse")

    # asignación masiva de profesor
    r = admin.post("/asignado/masivo", data={"id_curso": 1, "id_materia": 1, "id_profesor": 1},
                   headers={"HX-Request": "true", "HX-Current-URL": "http://x/asignado/"})
    ok(r.status_code == 200 and db_(lambda: Asignado.query.filter_by(id_profesor=1).count()) == 1, "asignación masiva de profesor")

    # alcance por rol
    p1, _ = login("profe1", "Clave12345")
    r = p1.get("/nota/", headers=HX)
    ok(b"Lenguaje" in r.data and "Armonía".encode() not in r.data, "profesor vinculado ve solo su materia")
    p2, _ = login("profe2", "Clave12345")
    r = p2.get("/nota/", headers=HX)
    ok(b"Rojas" not in r.data and b"Sin registros" in r.data, "profesor SIN vínculo no ve ningún dato")
    ok(p2.get("/alumno/", headers=HX).status_code == 200 and b"Rojas" not in p2.get("/alumno/", headers=HX).data,
       "profesor sin vínculo: alumnos vacío")
    ok(p1.get("/usuario/").status_code == 403, "profesor sin acceso a usuarios (403)")
    ok(p1.post("/gestion/guardar", data={}).status_code == 403, "profesor no puede crear gestiones")

    # edición de notas y cálculo
    nid = db_(lambda: Nota.query.join(Asignado).filter(Asignado.id_profesor == 1).first().id)
    r = p1.post("/nota/guardar", data={"id": nid, "nota1": 60, "nota2": 40, "nota3": 55, "nota4": 45},
                headers={"HX-Request": "true"})
    n = db_(lambda: (lambda x: (float(x.nota_final), x.aprobado))(db.session.get(Nota, nid)))
    ok(n == (50.0, False), "promedio 50 -> reprobado (mínimo 51)")
    p1.post("/nota/guardar", data={"id": nid, "nota1": 60, "nota2": 40, "nota3": 55, "nota4": 45, "segunda_t": 70},
            headers={"HX-Request": "true"})
    n = db_(lambda: (lambda x: (x.nota_aprob, x.aprobado))(db.session.get(Nota, nid)))
    ok(n == (70, True), "segundo turno aprueba")
    otra = db_(lambda: Nota.query.join(Asignado).filter(Asignado.id_profesor.is_(None)).first().id)
    ok(p1.get(f"/nota/{otra}/editar").status_code == 404, "profesor no puede abrir nota ajena")
    ok(p1.post("/nota/guardar", data={"id": otra, "nota1": 99}, headers={"HX-Request": "true"}).status_code == 404,
       "profesor no puede modificar nota ajena")

    # estudiante
    a1, _ = login("alu1", "Clave12345")
    r = a1.get("/pago/", headers=HX)
    ok(b"Rojas" in r.data and b"225.00" in r.data, "estudiante ve sus pagos")
    ok(b"Vega" not in a1.get("/alumno/", headers=HX).data, "estudiante no ve otros alumnos")
    ok(a1.get(f"/nota/{nid}/editar").status_code == 403, "estudiante no edita notas")

    # integridad
    r = admin.post("/inscrito/1/eliminar", headers={"HX-Request": "true"})
    ok("notas" in r.headers.get("HX-Trigger", ""), "no se elimina inscripción con notas")
    r = admin.post("/instrumento/1/eliminar", headers={"HX-Request": "true"})
    ok("relacionados" in r.headers.get("HX-Trigger", ""), "no se elimina instrumento con grados")
    r = admin.post("/pago/1/guardar", data={}, headers={"HX-Request": "true"})
    ok(r.status_code in (403, 404), "pagos no se crean manualmente")
    r = admin.post("/pago/guardar", data={"id": 1, "pagado": "on"}, headers={"HX-Request": "true"})
    ok(b"todo de pago" in r.data, "pago exige método")
    r = admin.post("/pago/guardar", data={"id": 1, "pagado": "on", "metodo_pago": "Efectivo"}, headers={"HX-Request": "true"})
    ok(db_(lambda: db.session.get(Pago, 1).fecha_pago) is not None, "pago registrado con fecha")

    # CSRF activo
    app.config["WTF_CSRF_ENABLED"] = True
    ok(app.test_client().post("/login", data={"usuario": "admin", "password": "Admin12345"}).status_code == 400,
       "CSRF exigido en login")
    print("\nTODAS LAS PRUEBAS OK")
