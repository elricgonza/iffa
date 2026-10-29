"""Prueba de humo de extremo a extremo (usa la base de DATABASE_URL).

ATENCIÓN: crea registros de prueba y al final BORRA TODOS los datos de las tablas
académicas. Ejecutar solo contra una base de pruebas:
    SMOKE_TEST_CONFIRM=yes python tests/smoke_test.py
"""
import os
import sys

if os.environ.get("SMOKE_TEST_CONFIRM") != "yes":
    sys.exit("Defina SMOKE_TEST_CONFIRM=yes (usar solo en una base de pruebas).")

import json
import re
import tempfile
from datetime import date, timedelta

# archivo de configuración de notas aislado (no toca el real)
os.environ["CONFIG_NOTAS_PATH"] = os.path.join(tempfile.mkdtemp(), "config_notas.json")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402
from app.models import (Alumno, Asignado, Curso, Gestion, Grado, Inscrito, Instrumento, Materia, Nota, Pago,  # noqa: E402
                        Permiso, Profesor, Rol, Usuario, db)

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


PERIODOS = ("nota1", "nota2", "nota3", "nota4", "2t")


def cfg_post(c, **over):
    """Guarda las fechas de transcripción (todo habilitado de ayer a +5 días, salvo lo indicado)."""
    hoy, data = date.today(), {}
    for k in PERIODOS:
        data[f"habilitar_{k}"] = "on"
        data[f"transcripcion_{k}_inicio"] = (hoy - timedelta(1)).isoformat()
        data[f"transcripcion_{k}_final"] = (hoy + timedelta(5)).isoformat()
    data.update(over)
    return c.post("/config/notas", data={k: v for k, v in data.items() if v is not None})


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

    # ---- Fechas de transcripción de notas (config_notas.json): acceso y valores por defecto
    nid0 = db_(lambda: Nota.query.join(Asignado).filter(Asignado.id_profesor == 1).first().id)
    nval = lambda campo: db_(lambda: getattr(db.session.get(Nota, nid0), campo))
    ok(p1.get("/config/notas").status_code == 403 and p2.get("/config/notas").status_code == 403,
       "Fechas Transcripción Notas: profesores no tienen acceso (403)")
    ok(b"Fechas Transcripci" in admin.get("/config/notas").data, "Fechas Transcripción Notas: el administrador accede")
    ok(b"Fechas Transcripci" in admin.get("/").data and b"Fechas Transcripci" not in p1.get("/").data,
       "la opción de menú solo aparece al administrador")
    ruta = os.environ["CONFIG_NOTAS_PATH"]
    claves = ({f"habilitar_{k}" for k in PERIODOS}
              | {f"transcripcion_{k}_{lado}" for k in PERIODOS for lado in ("inicio", "final")})
    cfg0 = json.load(open(ruta, encoding="utf-8"))
    ok(set(cfg0) == claves and all(cfg0[f"habilitar_{k}"] is False for k in PERIODOS),
       "config_notas.json se crea con las 15 claves y todo restringido")
    r = p1.post("/nota/guardar", data={"id": nid0, "nota1": 60, "obs": "prueba"}, headers={"HX-Request": "true"})
    ok(nval("nota1") is None and nval("obs") == "prueba", "restringido: la nota no cambia (otros campos sí)")
    f = p1.get(f"/nota/{nid0}/editar").data
    ok(b"disabled" in f and b"Bloqueado: Transcripci" in f, "formulario: campos bloqueados con el motivo")
    ok(b"Transcripci" in p1.get("/nota/").data and b"Configurar fechas" not in p1.get("/nota/").data,
       "listado de notas: estado de los períodos (enlace de configuración solo al administrador)")

    # validaciones y guardado del archivo
    r = cfg_post(admin, transcripcion_nota1_inicio=(date.today() + timedelta(8)).isoformat())
    ok(b"posterior" in r.data and json.load(open(ruta, encoding="utf-8")) == cfg0, "inicio posterior al final: rechazado")
    r = cfg_post(admin, transcripcion_nota2_final=None)
    ok("indique la fecha".encode() in r.data and json.load(open(ruta, encoding="utf-8")) == cfg0,
       "habilitar sin fechas: rechazado")
    r = cfg_post(admin, transcripcion_nota3_inicio="31/02/2026")
    ok("no v".encode() in r.data, "fecha inválida: rechazada")
    r = cfg_post(admin)
    cfg1 = json.load(open(ruta, encoding="utf-8"))
    ok(r.status_code == 302 and set(cfg1) == claves and all(cfg1[f"habilitar_{k}"] is True for k in PERIODOS)
       and cfg1["transcripcion_2t_inicio"] == (date.today() - timedelta(1)).isoformat()
       and cfg1["transcripcion_nota4_final"] == (date.today() + timedelta(5)).isoformat(),
       "fechas guardadas en config_notas.json (booleanos y fechas ISO)")

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

    # ---- Restricción por período
    def con(**over):
        cfg_post(admin, **over)
    getn = lambda campo: db_(lambda: getattr(db.session.get(Nota, nid), campo))
    # reenvía los valores actuales (un campo vacío en el formulario borraría la nota) y aplica los cambios pedidos
    actuales = lambda: {c: v for c in ("nota1", "nota2", "nota3", "nota4", "segunda_t") if (v := getn(c)) is not None}
    solo = lambda **d: p1.post("/nota/guardar", data={"id": nid, **actuales(), **d}, headers={"HX-Request": "true"})
    con(habilitar_nota2=None)  # Nota 2 restringida
    solo(nota1=61, nota2=10, nota3=55, nota4=45, segunda_t=70)
    ok(getn("nota2") == 40 and getn("nota1") == 61, "período restringido: Nota 2 intacta, Nota 1 sí cambia")
    con(transcripcion_nota3_final=(date.today() - timedelta(1)).isoformat(),
        transcripcion_nota3_inicio=(date.today() - timedelta(9)).isoformat())
    solo(nota3=99)
    ok(getn("nota3") == 55, "plazo vencido: Nota 3 intacta")
    ok(b"Plazo vencido" in p1.get(f"/nota/{nid}/editar").data, "formulario indica «plazo vencido»")
    con(transcripcion_nota4_inicio=(date.today() + timedelta(2)).isoformat())
    solo(nota4=99)
    ok(getn("nota4") == 45, "aún no habilitado: Nota 4 intacta")
    con(habilitar_2t=None)
    solo(segunda_t=5)
    ok(getn("segunda_t") == 70, "2T restringido: sin cambios")
    admin.post("/nota/guardar", data={"id": nid, "nota2": 77, "nota1": 61, "nota3": 55, "nota4": 45, "segunda_t": 70},
               headers={"HX-Request": "true"})
    ok(getn("nota2") == 77, "el Administrador no está sujeto a la restricción")
    cfg_post(admin)  # deja todo habilitado

    # estudiante
    a1, _ = login("alu1", "Clave12345")
    r = a1.get("/pago/", headers=HX)
    ok(b"Rojas" in r.data and b"225.00" in r.data, "estudiante ve sus pagos")
    ok(b"Vega" not in a1.get("/alumno/", headers=HX).data, "estudiante no ve otros alumnos")
    ok(a1.get(f"/nota/{nid}/editar").status_code == 403, "estudiante no edita notas")

    # ---- 1ro./2do. Técnico Medio: asignación automática, arrastres, requisitos y solo Administrador
    HXH = {"HX-Request": "true"}
    ID = lambda model, **kw: db_(lambda: model.query.filter_by(**kw).first().id)
    post(admin, "instrumento", instrumento="Violín", area="Cuerdas")
    iv = ID(Instrumento, instrumento="Violín")
    for nom in ("1ro. Técnico Medio", "2do. Técnico Medio"):
        post(admin, "grado", id_instrumento=iv, nivel="Técnico Medio", grado=nom)
    g1, g2 = ID(Grado, grado="1ro. Técnico Medio"), ID(Grado, grado="2do. Técnico Medio")
    for nom, g in (("A1", g1), ("A2", g1), ("B1", g2), ("B2", g2), ("B3", g2)):
        post(admin, "materia", id_grado=g, materia=nom, sigla=nom)
    M = {n: ID(Materia, materia=n) for n in ("A1", "A2", "B1", "B2", "B3")}
    for nom, req in (("B1", "A1"), ("B2", "A2")):  # B3 sin requisitos
        admin.post("/materia/guardar", data={"id": M[nom], "id_grado": g2, "materia": nom, "sigla": nom,
                                              "requisitos": [M[req]]}, headers=HXH)
    for g, par in ((g1, "A"), (g1, "B"), (g2, "A")):
        post(admin, "curso", id_gestion=1, id_grado=g, paralelo=par)
    C1 = db_(lambda: Curso.query.filter_by(id_grado=g1, paralelo="A").first().id)
    C1b = db_(lambda: Curso.query.filter_by(id_grado=g1, paralelo="B").first().id)
    C2 = db_(lambda: Curso.query.filter_by(id_grado=g2).first().id)
    for nom in ("Xavier", "Yara", "Zoe"):
        post(admin, "alumno", nombre=nom, paterno="Tm", masculino=True, activo=True)
    X, Y, Z = (ID(Alumno, nombre=n) for n in ("Xavier", "Yara", "Zoe"))

    def inscribir(cli, alu, curso):
        return post(cli, "inscrito", id_alumno=alu, id_curso=curso, id_instrumento=iv, inscrito=True, descuento=0)

    def ins_de(alu, curso):
        return ID(Inscrito, id_alumno=alu, id_curso=curso)

    def asigs(ins):
        return db_(lambda: {(a.materia.materia, a.id_curso) for a in Asignado.query.filter_by(id_inscrito=ins)})

    def aprobar(alu, mat):
        nid_ = db_(lambda: Nota.query.join(Asignado).join(Inscrito).filter(
            Inscrito.id_alumno == alu, Asignado.id_materia == M[mat]).first().id)
        admin.post("/nota/guardar", data={"id": nid_, "nota1": 80, "nota2": 80, "nota3": 80, "nota4": 80}, headers=HXH)

    def cursos_de(ins):
        """Cursos ofrecidos al elegir el inscrito (y texto completo de la respuesta HTMX)."""
        t = admin.get(f"/asignado/opciones/id_curso?id_inscrito={ins}", headers=HXH).get_data(as_text=True)
        cab = t.split("<select", 1)[0]  # opciones propias del select de curso (antes de los OOB)
        return set(int(v) for v in re.findall(r'<option value="(\d+)"', cab)), t

    def permitidas(ins, curso):
        """Materias ofrecidas para el inscrito en el curso elegido."""
        t = admin.get(f"/asignado/opciones/id_materia?id_curso={curso}&id_inscrito={ins}", headers=HXH).get_data(as_text=True)
        return {n for n in M if f">{n} (" in t}, t

    def asignar(cli, ins, mat, curso=None):
        extra = {"id_curso": curso} if curso else {}
        return post(cli, "asignado", id_inscrito=ins, id_materia=M[mat], **extra)

    # 1ro: se asignan solo las materias de 1ro no aprobadas
    inscribir(admin, X, C1)
    iX = ins_de(X, C1)
    ok(asigs(iX) == {("A1", C1), ("A2", C1)}, "1ro T.M.: se asignan las materias de 1ro")
    # solo el Administrador
    post(admin, "usuario", usuario="trans1", email="t@x.com", password="Clave12345", activo=True,
         roles=db_(lambda: Rol.query.filter_by(rol="Transcriptor").first().id))
    tr, _ = login("trans1", "Clave12345")
    ok(tr.get("/inscrito/", headers=HX).status_code == 200, "el Transcriptor sigue usando Inscripciones")
    r = inscribir(tr, Y, C1)
    ok(b"solo puede gestionarlas el Administrador" in r.data and db_(lambda: Inscrito.query.filter_by(id_alumno=Y).count()) == 0,
       "inscripción en Técnico Medio: solo Administrador")
    r = asignar(tr, iX, "B3")
    ok(b"solo puede gestionarlas el Administrador" in r.data, "asignación en Técnico Medio: solo Administrador")
    # 1ro: materias de 2do con curso habilitado y requisitos aprobados
    cur, txt = cursos_de(iX)
    ok(cur == {C2} and f'value="{C2}" selected' in txt, "1ro: único curso candidato (2do) se preselecciona")
    ok('hx-swap-oob="innerHTML"' in txt and ">B3 (" in txt and ">B1 (" not in txt and ">B2 (" not in txt,
       "1ro: la cascada ofrece solo B3 (B1/B2 requieren A1/A2)")
    ok(permitidas(iX, C2)[0] == {"B3"}, "1ro: materias del curso de 2do permitidas = {B3}")
    ok(b"Requisitos sin aprobar: A1" in asignar(admin, iX, "B1").data, "2do con requisito sin aprobar: rechazado")
    db_(lambda: (db.session.query(Curso).filter_by(id=C2).update({"id_gestion": 2}), db.session.commit()))
    ok(b"No existe un curso" in asignar(admin, iX, "B3").data and cursos_de(iX)[0] == set()
       and "Sin opciones" in cursos_de(iX)[1], "sin curso de 2do en la gestión vigente: rechazado")
    db_(lambda: (db.session.query(Curso).filter_by(id=C2).update({"id_gestion": 1}), db.session.commit()))
    asignar(admin, iX, "B3")
    ok(("B3", C2) in asigs(iX), "B3 asignada y cursada en el curso de 2do")
    aprobar(X, "A1")
    ok("B1" in permitidas(iX, C2)[0], "aprobado el requisito A1, B1 queda permitida")
    asignar(admin, iX, "B1")
    ok(("B1", C2) in asigs(iX), "B1 asignada en el curso de 2do")
    # nueva inscripción a 1ro: solo lo no aprobado
    inscribir(admin, X, C1b)
    ok(asigs(ins_de(X, C1b)) == {("A2", C1b)}, "1ro: A1 ya aprobada no se vuelve a asignar")

    # 2do con 1ro completo: asignación automática de 2do
    inscribir(admin, Y, C1)
    aprobar(Y, "A1"); aprobar(Y, "A2")
    inscribir(admin, Y, C2)
    ok(asigs(ins_de(Y, C2)) == {("B1", C2), ("B2", C2), ("B3", C2)}, "2do con 1ro aprobado: se asignan todas las de 2do")

    # 2do con arrastre: no se asigna nada automáticamente; se permite arrastre y 2do con requisitos
    inscribir(admin, Z, C1)
    aprobar(Z, "A1")
    inscribir(admin, Z, C2)
    iZ = ins_de(Z, C2)
    ok(asigs(iZ) == set(), "2do con arrastre: sin asignación automática")
    cur, _t = cursos_de(iZ)
    ok(cur == {C2, C1, C1b}, "2do con arrastre: puede elegir su curso o cualquiera de los cursos de 1ro")
    pa, ta = permitidas(iZ, C1)
    ok(pa == {"A2"} and "arrastre" in ta, "curso de 1ro: solo el arrastre pendiente A2 (A1 ya aprobada)")
    ok(permitidas(iZ, C1b)[0] == {"A2"}, "el otro curso de 1ro ofrece el mismo arrastre")
    ok(permitidas(iZ, C2)[0] == {"B1", "B3"}, "curso de 2do: B1 y B3; no B2 (requiere A2)")
    ok(b"ya aprob" in asignar(admin, iZ, "A1", C1).data, "no se asigna una materia ya aprobada")
    ok(b"Requisitos sin aprobar: A2" in asignar(admin, iZ, "B2", C2).data, "B2 requiere A2 (arrastre pendiente)")
    ok(b"seleccione el curso" in asignar(admin, iZ, "A2").data, "varios cursos de 1ro: obliga a elegir el curso")
    ok(b"no corresponde al grado" in asignar(admin, iZ, "A2", C2).data, "curso que no es del grado de la materia: rechazado")
    asignar(admin, iZ, "A2", C1b); asignar(admin, iZ, "B1")
    ok(asigs(iZ) == {("A2", C1b), ("B1", C2)}, "arrastre cursado en el curso de 1ro elegido (paralelo B); B1 en el de 2do")

    # ---- Asignaciones: «+ Nuevo» solo muestra inscritos de la gestión activa; curso en grados no TM
    nuevo = admin.get("/asignado/nuevo").get_data(as_text=True)
    ok("Rojas" in nuevo and "Tm Xavier" in nuevo, "«+ Nuevo»: aparecen los inscritos de la gestión activa")
    ok("Vega" not in nuevo, "«+ Nuevo»: no aparecen inscritos de gestiones no activas")
    ok('hx-include="#f_id_inscrito"' in nuevo and 'id="f_id_curso"' in nuevo and 'id="f_id_materia"' in nuevo,
       "formulario: inscrito → curso → materia")
    iv2 = db_(lambda: Inscrito.query.filter_by(id_alumno=2, id_curso=2).first().id)  # inscrito de gestión no activa
    r = post(admin, "asignado", id_inscrito=iv2, id_materia=M["A1"])
    ok(b"no v" in r.data, "inscrito de gestión no activa: rechazado por el servidor")
    cur, txt = cursos_de(1)  # inscrito de Piano I (no Técnico Medio)
    ok(cur == {1} and 'value="1" selected' in txt and "Lenguaje" in txt, "grado común: curso único preseleccionado y materias del grado")
    r = post(admin, "asignado", id_inscrito=1, id_materia=1, id_curso=C2)
    ok(b"ya est" in r.data or b"curso de la inscripci" in r.data, "grado común: no se puede elegir otro curso")

    # ---- Gestiones: una sola activa, visible en todas las pantallas, vistas por defecto de la gestión activa
    G = dict(inicio="2026-02-01", fin="2026-12-01")
    r = post(admin, "gestion", gestion=2027, plan="Plan 2027", inicio="2027-02-01", fin="2027-12-01", activo=True)
    ok(b"Solo una gesti" in r.data and db_(lambda: Gestion.query.filter_by(gestion=2027).count()) == 0,
       "gestión nueva «activa» con otra ya activa: rechazada")
    r = post(admin, "gestion", id=2, gestion=2025, plan="Plan 2025", inicio="2025-02-01", fin="2025-12-01", activo=True)
    ok(b"Solo una gesti" in r.data and db_(lambda: db.session.get(Gestion, 2).activo) is False,
       "editar otra gestión a «activa»: rechazado")
    r = post(admin, "gestion", id=1, gestion=2026, plan="Plan 2026", activo=True, **G)
    ok(b"Solo una gesti" not in r.data and db_(lambda: db.session.get(Gestion, 1).activo) is True,
       "la gestión activa se puede editar manteniéndola activa")

    def _dup():
        try:
            db.session.add(Gestion(gestion=2030, plan="X", inicio=date(2030, 1, 1), fin=date(2030, 12, 1),
                                   activo=True, id_usuario=1))
            db.session.commit()
            return False
        except Exception:
            db.session.rollback()
            return True
    ok(db_(_dup), "el índice único de la BD impide dos gestiones activas")

    home = admin.get("/").get_data(as_text=True)
    ok("Gestión activa" in home and "2026 · Plan 2026" in home and "Gestión 2026" in home,
       "la gestión activa se muestra en el menú lateral y en la barra móvil")
    ok("2026 · Plan 2026" in p1.get("/nota/").get_data(as_text=True), "visible también para otros usuarios y pantallas")
    post(admin, "gestion", id=1, gestion=2026, plan="Plan 2026", **G)  # desactivar
    ok("Sin gestión activa" in admin.get("/").get_data(as_text=True), "sin gestión activa: aviso")
    post(admin, "gestion", id=2, gestion=2025, plan="Plan 2025", inicio="2025-02-01", fin="2025-12-01", activo=True)
    ok("2025 · Plan 2025" in admin.get("/").get_data(as_text=True), "cambia la gestión activa (primero desactivar, luego activar)")
    post(admin, "gestion", id=2, gestion=2025, plan="Plan 2025", inicio="2025-02-01", fin="2025-12-01")
    post(admin, "gestion", id=1, gestion=2026, plan="Plan 2026", activo=True, **G)  # restaurar
    ok(db_(lambda: [g.activo for g in Gestion.query.order_by(Gestion.id)])[:2] == [True, False], "estado restaurado")

    # solo el Administrador gestiona las gestiones, aunque otro rol tuviera el permiso
    def _dar(perms):
        rol = Rol.query.filter_by(rol="Transcriptor").first()
        rol.permisos = [p for p in rol.permisos if not p.permiso.startswith("gestion_")] + \
                       Permiso.query.filter(Permiso.permiso.in_(perms)).all()
        db.session.commit()
    db_(lambda: _dar(["gestion_ver", "gestion_crear", "gestion_editar", "gestion_eliminar"]))
    tr, _ = login("trans1", "Clave12345")
    ok(tr.get("/gestion/nuevo").status_code == 403 and tr.post("/gestion/guardar", data={"gestion": 2040}).status_code == 403
       and b"+ Nuevo" not in tr.get("/gestion/").data and b"Editar" not in tr.get("/gestion/", headers=HX).data,
       "gestiones: un rol con permisos no-administrador no puede crear/editar")
    db_(lambda: _dar(["gestion_ver"]))

    # vistas por defecto: gestión activa (con opción de elegir otra)
    def _vieja():
        a = Asignado(id_inscrito=iv2, id_curso=2, id_materia=1, id_usuario=1)
        db.session.add(a)
        db.session.flush()
        n = Nota(id_asignado=a.id, id_usuario=1)
        db.session.add(n)
        db.session.commit()
        return n.id
    nid_old = db_(_vieja)  # Vega: asignación en un curso de la gestión 2025 (no activa)
    for modulo, texto in (("curso", "2025"), ("asignado", "Vega"), ("nota", "Vega")):
        pag = admin.get(f"/{modulo}/").get_data(as_text=True)
        por_def = admin.get(f"/{modulo}/", headers=HX).get_data(as_text=True)
        todas = admin.get(f"/{modulo}/?id_gestion=todas", headers=HX).get_data(as_text=True)
        otra = admin.get(f"/{modulo}/?id_gestion=2", headers=HX).get_data(as_text=True)
        ok('value="activa" selected' in pag and texto not in por_def and texto in todas and texto in otra,
           f"{modulo}: por defecto la gestión activa; se puede elegir otra o «todas»")
    ok("Rojas" in admin.get("/nota/", headers=HX).get_data(as_text=True)
       and "Rojas" not in admin.get("/nota/?id_gestion=2", headers=HX).get_data(as_text=True),
       "calificaciones: la gestión elegida filtra correctamente")
    ok(admin.get(f"/nota/{nid_old}/editar").status_code == 200, "se puede abrir una calificación de una gestión anterior")

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
