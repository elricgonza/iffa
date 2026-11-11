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
from app.models import (Alumno, Asignado, AuditLog, Curso, Gestion, Grado, Inscrito, Instrumento, Materia, Nota, Pago,  # noqa: E402
                        Permiso, Profesor, Rol, Usuario, db)
from app.reportes import (_clave_orden, alumnos_profesor_xlsx, centralizador_xlsx, datos_alumnos_profesor,  # noqa: E402
                          datos_centralizador, pdf_disponible)

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
    ok(admin.get("/inscrito/2/editar").status_code == 403, "inscripción de una gestión anterior: no editable")
    ok(admin.get("/inscrito/1/editar").status_code == 200, "inscripción de la gestión vigente: editable")
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
    # ---- Edición solo en la gestión vigente (cursos, inscripciones, asignaciones, calificaciones)
    old_asig = db_(lambda: Asignado.query.filter_by(id_curso=2).first().id)
    ok(admin.get("/curso/1/editar").status_code == 200 and admin.get("/curso/2/editar").status_code == 403
       and admin.post("/curso/guardar", data={"id": 2, "id_gestion": 2, "id_grado": 1, "paralelo": "Z"}).status_code == 403
       and admin.post("/curso/2/eliminar").status_code == 403, "cursos: solo se editan/eliminan los de la gestión vigente")
    ok("Solo lectura" in admin.get("/curso/?id_gestion=2", headers=HX).get_data(as_text=True)
       and "Solo lectura" not in admin.get("/curso/", headers=HX).get_data(as_text=True),
       "cursos de otra gestión se muestran como «Solo lectura»")
    ok(admin.get("/inscrito/2/editar").status_code == 403
       and admin.post("/inscrito/guardar", data={"id": 2, "descuento": 5}).status_code == 403
       and admin.post("/inscrito/2/eliminar").status_code == 403, "inscripciones: solo las de la gestión vigente")
    ok(admin.get(f"/asignado/{old_asig}/editar").status_code == 403
       and admin.post("/asignado/guardar", data={"id": old_asig, "id_profesor": 1}).status_code == 403,
       "asignaciones: solo las de la gestión vigente")
    ok(admin.get(f"/nota/{nid_old}/editar").status_code == 403
       and admin.post("/nota/guardar", data={"id": nid_old, "nota1": 90}).status_code == 403,
       "calificaciones: solo las de la gestión vigente")
    ok(admin.get("/asignado/1/editar").status_code in (200, 404) and "2025" not in admin.get("/asignado/masivo").get_data(as_text=True),
       "«Asignar profesor por curso/materia»: solo cursos de la gestión vigente")
    r = admin.post("/asignado/masivo", data={"id_curso": 2, "id_materia": 1, "id_profesor": 1},
                   headers={"HX-Request": "true", "HX-Current-URL": "http://x/asignado/"})
    ok("gestión vigente".encode() in r.data and db_(lambda: db.session.get(Asignado, old_asig).id_profesor) is None,
       "asignación masiva en un curso de otra gestión: rechazada")
    r = post(admin, "inscrito", id_alumno=2, id_curso=2, id_instrumento=1, inscrito=True, descuento=0)
    ok(b"no v" in r.data or "gestión vigente".encode() in r.data, "nueva inscripción en curso de otra gestión: rechazada")
    # sin gestión activa: no se puede crear inscripciones/asignaciones ni asignar profesor
    post(admin, "gestion", id=1, gestion=2026, plan="Plan 2026", inicio="2026-02-01", fin="2026-12-01")  # desactiva
    pag_i, pag_a = admin.get("/inscrito/").get_data(as_text=True), admin.get("/asignado/").get_data(as_text=True)
    ok("+ Nuevo" not in pag_i and "+ Nuevo" not in pag_a and "Asignar profesor por curso" not in pag_a
       and admin.get("/inscrito/nuevo").status_code == 403 and admin.get("/asignado/nuevo").status_code == 403,
       "sin gestión vigente: «+ Nuevo» y asignación masiva no disponibles")
    ok(admin.get("/curso/1/editar").status_code == 403 and admin.get(f"/nota/{nid_old}/editar").status_code == 403,
       "sin gestión vigente: nada es editable")
    post(admin, "gestion", id=1, gestion=2026, plan="Plan 2026", inicio="2026-02-01", fin="2026-12-01", activo=True)  # restaurar
    ok("+ Nuevo" in admin.get("/inscrito/").get_data(as_text=True) and "Asignar profesor por curso" in admin.get("/asignado/").get_data(as_text=True),
       "con gestión vigente: «+ Nuevo» y asignación masiva disponibles")

    # ---- Cambio de contraseña: disponible para todos los usuarios, con política y límite de intentos
    def crear_usuario(nombre, rol=None, clave="Clave12345"):
        d = {"usuario": nombre, "email": f"{nombre}@x.com", "password": clave, "activo": "on"}
        if rol:
            d["roles"] = [db_(lambda: Rol.query.filter_by(rol=rol).first().id)]
        return admin.post("/usuario/guardar", data=d, headers=HXH)

    crear_usuario("pwsinrol"); crear_usuario("pwcons", "Consulta")
    sr, _ = login("pwsinrol", "Clave12345")
    cn, _ = login("pwcons", "Clave12345")
    clientes = [admin, p1, a1, tr, sr, cn]  # Administrador, Profesor, Estudiante, Transcriptor, sin roles, Consulta
    ok(all(c.get("/cambiar-password").status_code == 200 for c in clientes),
       "cambiar contraseña: disponible para todos los roles (incluso un usuario sin roles)")
    ok(all("Cambiar contraseña" in c.get("/perfil").get_data(as_text=True) for c in clientes),
       "cambiar contraseña: accesible para todos desde «Mi perfil»")
    ok(app.test_client().get("/cambiar-password").status_code == 302, "cambiar contraseña: exige sesión iniciada")

    cp = lambda c, **d: c.post("/cambiar-password", data=d)
    t = lambda r: r.get_data(as_text=True)
    r = cp(cn, actual="mala", nueva="Nueva12345", confirmar="Nueva12345")
    ok("actual no es correcta" in t(r) and login("pwcons", "Clave12345")[1].status_code == 302, "contraseña actual incorrecta: rechazado")
    for nueva, texto, desc in (("Ab1", "al menos 8", "muy corta"), ("abcdefghij", "letras y números", "sin números"),
                               ("1234567890", "letras y números", "sin letras"), ("Clave12345", "distinta", "igual a la actual"),
                               ("xpwcons99", "nombre de usuario", "contiene el usuario")):
        ok(texto in t(cp(cn, actual="Clave12345", nueva=nueva, confirmar=nueva)), f"política: {desc}")
    ok("no coincide" in t(cp(cn, actual="Clave12345", nueva="Nueva12345", confirmar="Otra12345")), "confirmación distinta: rechazado")
    r = cp(cn, actual="Clave12345", nueva="Nueva12345", confirmar="Nueva12345")
    ok(r.status_code == 302 and login("pwcons", "Clave12345")[1].status_code == 401
       and login("pwcons", "Nueva12345")[1].status_code == 302, "cambio correcto: la clave anterior deja de servir y la nueva funciona")
    ok(db_(lambda: AuditLog.query.filter(AuditLog.modulo == "auth", AuditLog.detalle.like("%cambio_password%")).count()) >= 2,
       "el cambio (y los intentos fallidos) quedan en la auditoría")
    for _ in range(5):
        cp(sr, actual="mala", nueva="Nueva12345", confirmar="Nueva12345")
    ok("Demasiados intentos" in t(cp(sr, actual="Clave12345", nueva="Nueva12345", confirmar="Nueva12345"))
       and login("pwsinrol", "Clave12345")[1].status_code == 302, "5 intentos fallidos: se bloquea temporalmente (sin cambiar la clave)")
    r = crear_usuario("pwdebil", clave="abcdefgh")
    ok("letras y números" in t(r) and db_(lambda: Usuario.query.filter_by(usuario="pwdebil").count()) == 0,
       "la administración de usuarios aplica la misma política")

    # ---- Presentación: logo institucional, estructura HTML y plantillas de reportes
    from html.parser import HTMLParser

    class Contador(HTMLParser):
        VOID = {"meta", "link", "img", "input", "br", "hr", "path", "circle", "rect", "source", "col"}

        def __init__(self):
            super().__init__()
            self.pila, self.error = [], None

        def handle_starttag(self, tag, attrs):
            if tag not in self.VOID:
                self.pila.append(tag)

        def handle_startendtag(self, tag, attrs):
            pass

        def handle_endtag(self, tag):
            if tag in self.VOID:
                return
            if not self.pila or self.pila[-1] != tag:
                self.error = self.error or f"</{tag}> inesperado (abierto: {self.pila[-3:]})"
            else:
                self.pila.pop()

    def bien_formado(html):
        c = Contador()
        c.feed(html)
        return c.error is None and not c.pila, (c.error or c.pila)

    for nombre in ("logo.png", "logo-sm.png", "logo-blanco.png", "favicon.png"):
        r = admin.get(f"/static/img/{nombre}")
        ok(r.status_code == 200 and r.data[:8] == b"\x89PNG\r\n\x1a\n", f"logo: /static/img/{nombre} se sirve")
    import struct
    ihdr = admin.get("/static/img/logo.png").data[16:24]
    ok(struct.unpack(">II", ihdr) == (447, 447), "logo original conservado (447x447, sin modificar)")
    login_html = app.test_client().get("/login").get_data(as_text=True)
    ok("logo-blanco.png" in login_html and "logo-sm.png" in login_html and "Luis Felipe Arce" in login_html
       and "Instituto de Formación Artística" in login_html, "login: logo e identidad institucional")
    inicio = admin.get("/").get_data(as_text=True)
    ok("logo-blanco.png" in inicio and "favicon.png" in inicio and "print-only" in inicio
       and "Instituto de Formación Artística" in inicio and "Sistema de Gestión Académica" in inicio,
       "layout: logo en el menú, favicon, encabezado de impresión e identidad")
    for url in ("/login", "/", "/perfil", "/cambiar-password", "/config/notas", "/alumno/", "/nota/", "/asignado/", "/inscrito/",
                "/curso/", "/gestion/", "/usuario/"):
        cli = app.test_client() if url == "/login" else admin
        good, det = bien_formado(cli.get(url).get_data(as_text=True))
        ok(good, f"HTML bien formado: {url} {'' if good else det}")
    for url in ("/alumno/nuevo", "/inscrito/nuevo", "/asignado/nuevo", "/curso/1/editar", "/asignado/masivo"):
        good, det = bien_formado(admin.get(url, headers=HXH).get_data(as_text=True))
        ok(good, f"HTML bien formado (modal): {url} {'' if good else det}")
    with app.test_request_context("/"):
        from flask import render_template_string
        rep = render_template_string("{% extends 'reportes/_base.html' %}{% block cuerpo %}<p>cuerpo</p>{% endblock %}",
                                     titulo_reporte="Listado de prueba", gestion_activa=None, fecha_reporte="01/01/2026")
        ok("data:image/png;base64," in rep and "Luis Felipe Arce" in rep and "Listado de prueba" in rep and "<p>cuerpo</p>" in rep,
           "plantilla base de reportes: logo incrustado y encabezado institucional")

    # ---- Boletín de Calificaciones: botón por rol, alcance de datos, contenido y PDF
    roles_con = lambda: db_(lambda: {r.rol for r in Rol.query.all() if any(p.permiso == "nota_boletin" for p in r.permisos)})
    ok(roles_con() == {"Administrador", "Transcriptor", "Estudiante"}, "permiso nota_boletin: administrador, transcriptor y estudiante")
    def _quitar():  # simula una base anterior a esta versión
        db.session.execute(db.text("DELETE FROM rol_permiso WHERE id_permiso IN (SELECT id FROM permiso WHERE permiso='nota_boletin')"))
        db.session.execute(db.text("DELETE FROM permiso WHERE permiso='nota_boletin'"))
        db.session.commit()
    db_(_quitar)
    res = app.test_cli_runner().invoke(args=["seed"])
    ok("nota_boletin" in res.output and roles_con() == {"Administrador", "Transcriptor", "Estudiante"},
       "flask seed otorga el permiso nuevo a los roles base de una base existente (sin tocar Profesor/Consulta)")

    iR = db_(lambda: Inscrito.query.filter_by(id_alumno=1, id_curso=1).first().id)      # Carla Rojas (usuario alu1)
    for nombre, cli in (("administrador", admin), ("transcriptor", tr), ("estudiante", a1)):
        ok("Boletín de Calificaciones" in cli.get("/nota/").get_data(as_text=True),
           f"Calificaciones: botón «Boletín de Calificaciones» visible para {nombre}")
    ok("Boletín de Calificaciones" not in p1.get("/nota/").get_data(as_text=True)
       and "Boletín de Calificaciones" not in cn.get("/nota/").get_data(as_text=True), "botón oculto para profesor y consulta")
    ok(p1.get(f"/reportes/boletin/{iR}").status_code == 403 and cn.get(f"/reportes/boletin/{iR}").status_code == 403
       and p1.get("/reportes/boletin/seleccion").status_code == 403, "profesor y consulta: sin acceso al boletín (403)")
    ok(app.test_client().get(f"/reportes/boletin/{iR}").status_code == 302, "boletín exige sesión iniciada")

    h = admin.get(f"/reportes/boletin/{iR}").get_data(as_text=True)
    ok(all(t in h for t in ("Boletín de Calificaciones", "DATOS DEL ESTUDIANTE", "Rojas Carla", "ANUALIZADO", "MÚSICA ACADÉMICA",
                            "Piano", "LM1", "AR1", "Lenguaje Musical", "data:image/png;base64", "R.M. 685/2016")),
       "boletín: encabezado institucional, datos del estudiante y asignaturas")
    ok('class="total">60<' in h and "APROBADO" in h and ">70<" in h and "PENDIENTE" in h and "EN PROCESO" in h,
       "boletín: P1-P4, total (promedio 59.5 → 60), 2T, literal y situación según la base de datos")
    ok(bool(re.search(r"Gestión 2026", h)) and "Piano I" in h, "boletín: gestión, curso y paralelo del curso inscrito")
    iV = db_(lambda: Inscrito.query.filter_by(id_alumno=2, id_curso=2).first().id)       # Vega, gestión 2025
    ok(tr.get(f"/reportes/boletin/{iV}").status_code == 200 and "2025" in tr.get(f"/reportes/boletin/{iV}").get_data(as_text=True),
       "transcriptor: boletín de cualquier alumno y gestión")
    ok(a1.get(f"/reportes/boletin/{iR}").status_code == 200 and a1.get(f"/reportes/boletin/{iV}").status_code == 404,
       "estudiante: solo su propio boletín (el de otro alumno → 404)")
    zs = a1.get("/reportes/boletin/seleccion").get_data(as_text=True)
    ok(f'value="{iR}"' in zs and f'value="{iV}"' not in zs and "Vega" not in zs, "estudiante: la selección lista solo sus inscripciones")
    ok(a1.get("/reportes/boletin/buscar?q=vega").status_code == 403, "estudiante: no puede buscar a otros alumnos")
    hz = admin.get(f"/reportes/boletin/{iZ}").get_data(as_text=True)
    ok("Arrastre" in hz and "B1" in hz and "A2" in hz, "boletín: las materias de arrastre se identifican con su curso")

    sb = lambda q, cli=admin: cli.get(f"/reportes/boletin/buscar?q={q}", headers=HXH).get_data(as_text=True)
    ok(f'data-id="{iR}"' in sb("rojas") and "C.I." in sb("rojas") and f'data-id="{iR}"' in sb("carla%20roj"),
       "búsqueda de alumno para el boletín: por apellido y por varias palabras")
    ok("Vega" not in sb("vega") and "Vega" in sb("vega&id_gestion=2") and "Vega" in sb("vega&id_gestion=todas"),
       "búsqueda: respeta la gestión elegida (por defecto la activa)")
    ok("al menos 2" in sb("a") and "Sin resultados" in sb("zzzzzz"), "búsqueda: mínimo 2 caracteres y sin resultados")
    ok("bol_gestion" in admin.get("/reportes/boletin/seleccion").get_data(as_text=True), "administrador: selección con gestión y búsqueda de alumno")

    rp = admin.get(f"/reportes/boletin/{iR}?pdf=1")
    if pdf_disponible():
        ok(rp.status_code == 200 and rp.mimetype == "application/pdf" and rp.data[:5] == b"%PDF-", "boletín en PDF (WeasyPrint)")
    else:
        ok(rp.status_code == 501, "PDF no disponible: aviso claro (501) y la versión HTML sigue funcionando")
    ok(db_(lambda: AuditLog.query.filter_by(modulo="reporte_boletin").count()) >= 3, "la generación del boletín queda en la auditoría")

    # ---- Reportes → Centralizador de Calificaciones
    roles_cz = lambda: db_(lambda: {r.rol for r in Rol.query.all() if any(p.permiso == "reporte_centralizador" for p in r.permisos)})
    ok(roles_cz() == {"Administrador", "Transcriptor", "Estudiante"}, "permiso reporte_centralizador: administrador, transcriptor y estudiante")
    def _quitar_cz():
        db.session.execute(db.text("DELETE FROM rol_permiso WHERE id_permiso IN (SELECT id FROM permiso WHERE permiso='reporte_centralizador')"))
        db.session.execute(db.text("DELETE FROM permiso WHERE permiso='reporte_centralizador'"))
        db.session.commit()
    db_(_quitar_cz)
    res = app.test_cli_runner().invoke(args=["seed"])
    ok("reporte_centralizador" in res.output and roles_cz() == {"Administrador", "Transcriptor", "Estudiante"},
       "flask seed otorga el permiso del Centralizador en una base existente")

    for nombre, cli in (("administrador", admin), ("transcriptor", tr), ("estudiante", a1)):
        h0 = cli.get("/").get_data(as_text=True)
        ok("Reportes" in h0 and "Centralizador de Calificaciones" in h0 and cli.get("/reportes/centralizador").status_code == 200,
           f"menú Procesos → Reportes → Centralizador de Calificaciones disponible para {nombre}")
    ok("Centralizador de Calificaciones" not in p1.get("/").get_data(as_text=True)
       and "Centralizador de Calificaciones" not in cn.get("/").get_data(as_text=True)
       and p1.get("/reportes/centralizador").status_code == 403 and cn.get("/reportes/centralizador").status_code == 403
       and p1.get("/reportes/centralizador/generar?id_curso=1").status_code == 403, "profesor y consulta: sin acceso al Centralizador")
    ok(app.test_client().get("/reportes/centralizador").status_code == 302, "el Centralizador exige sesión iniciada")
    good, det = bien_formado(admin.get("/reportes/centralizador").get_data(as_text=True))
    ok(good, f"HTML bien formado: pantalla del Centralizador {'' if good else det}")

    cu = lambda cli, g: cli.get(f"/reportes/centralizador/cursos?id_gestion={g}", headers=HXH).get_data(as_text=True)
    ok("Piano I A / 2026" in cu(admin, "activa") and "Piano I B / 2025" not in cu(admin, "activa")
       and "Piano I B / 2025" in cu(admin, 2) and "Piano I B / 2025" in cu(admin, "todas"), "selector de curso según la gestión elegida")
    ok("Piano I A / 2026" in cu(a1, "activa") and "Técnico Medio" not in cu(a1, "activa"), "estudiante: solo los cursos en los que está inscrito")

    gen = lambda cli, curso, fmt="html", extra="": cli.get(f"/reportes/centralizador/generar?id_curso={curso}&formato={fmt}{extra}")
    h1 = gen(admin, 1, extra="&libro=10&folio=0015").get_data(as_text=True)
    ok(all(t in h1 for t in ("CENTRALIZADOR DE CALIFICACIONES", "ROJAS CARLA", "LM1", "AR1", "AR2", "LIBRO N° 10", "FOLIO N° 0015",
                              "80730917", "034/1993-0685/2016", "ESTUDIANTES INSCRITOS", "ESTADÍSTICAS", "data:image/png;base64")),
       "Centralizador (HTML): encabezado oficial, datos del curso, nómina y estadísticas")
    ok(bool(re.search(r'class="nota [^"]*">60<', h1)) and "EN PROCESO" in h1, "Centralizador: nota final por asignatura (promedio; 2T solo si reprueba) y estado según la base de datos")
    good, det = bien_formado(h1)
    ok(good, f"HTML bien formado: Centralizador {'' if good else det}")
    ok("ARRASTRE" in gen(admin, 3).get_data(as_text=True), "Centralizador: los arrastres se indican en observaciones")

    rx = gen(admin, 1, "xlsx", "&libro=10&folio=0015")
    ok(rx.status_code == 200 and "spreadsheetml" in rx.mimetype and "attachment" in rx.headers["Content-Disposition"], "Centralizador en Excel (descarga .xlsx)")
    import io
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(rx.data))
    av, rv = wb["ANVERSO"], wb["REVERSO"]
    celdas = {c.value for fila in av.iter_rows() for c in fila if c.value is not None}
    ok(wb.sheetnames == ["ANVERSO", "REVERSO"] and {"CENTRALIZADOR DE CALIFICACIONES", "ROJAS CARLA", "LIBRO N° 10", "FOLIO N° 0015",
                                                      "CÉDULA DE IDENTIDAD ", "ESTADO", "NÓMINA ESTUDIANTES"} <= celdas
       and {"AR1", "AR2", "LM1"} <= celdas, "Excel: hojas ANVERSO y REVERSO con el formato oficial y los datos")
    fila_carla = next(c.row for c in av["B"] if c.value == "ROJAS CARLA")
    ok(60 in [c.value for c in av[fila_carla]] and "EN PROCESO" in [c.value for c in av[fila_carla]], "Excel: calificación final y estado del estudiante")
    ok(any(str(c.value).startswith("=IF(") for fila in rv.iter_rows() for c in fila if c.value is not None)
       and rv.page_setup.orientation == "landscape", "Excel: estadísticas con porcentajes (fórmulas) y página horizontal")

    def _inyeccion():
        d = datos_centralizador(db.session.get(Curso, 1))
        d["filas"][0]["nombre"] = "=HYPERLINK(\"http://x\",\"x\")"
        d["filas"][0]["obs"] = "=1+1"
        w = load_workbook(io.BytesIO(centralizador_xlsx(d)))["ANVERSO"]
        return [(c.value, c.data_type) for fila in w.iter_rows(min_row=16, max_row=16) for c in fila if c.value in (d["filas"][0]["nombre"], "=1+1")]
    inj = db_(_inyeccion)
    ok(len(inj) == 2 and all(t == "s" for _, t in inj), "Excel: un nombre u observación que empiece con «=» se guarda como texto (sin fórmulas)")

    rp = gen(admin, 1, "pdf")
    if pdf_disponible():
        ok(rp.status_code == 200 and rp.mimetype == "application/pdf" and rp.data[:5] == b"%PDF-", "Centralizador en PDF (apaisado)")
    else:
        ok(rp.status_code == 501, "PDF no disponible: aviso (501); Excel y HTML siguen funcionando")
    ok(gen(admin, 1, "otro").status_code == 400 and admin.get("/reportes/centralizador/generar?id_curso=99999").status_code == 404,
       "formato no válido (400) y curso inexistente (404)")
    ok(tr.get("/reportes/centralizador/generar?id_curso=3").status_code == 200, "transcriptor: Centralizador de cualquier curso")

    # estudiante: solo su fila (con las estadísticas del curso) y solo cursos propios
    crear_usuario("zoeuser", "Estudiante")
    db_(lambda: (db.session.query(Alumno).filter_by(id=Z).update({"usr_id_login": db.session.query(Usuario.id).filter_by(usuario="zoeuser").scalar()}), db.session.commit()))
    zc, _ = login("zoeuser", "Clave12345")
    hz1 = gen(zc, 3).get_data(as_text=True)
    ok("TM ZOE" in hz1 and "TM XAVIER" not in hz1 and "TM YARA" not in hz1 and "solo se muestran sus propios datos" in hz1
       and "ESTUDIANTES INSCRITOS" in hz1, "estudiante: el Centralizador muestra solo su fila (estadísticas del curso completo)")
    xz = load_workbook(io.BytesIO(gen(zc, 3, "xlsx").data))["ANVERSO"]
    nombres_z = {c.value for c in xz["B"] if isinstance(c.value, str)}
    ok("TM ZOE" in nombres_z and "TM XAVIER" not in nombres_z, "estudiante: el Excel tampoco incluye a otros estudiantes")
    ok(gen(zc, 4).status_code == 404, "estudiante: un curso en el que no está inscrito → 404")
    app.config["CENTRALIZADOR_ESTUDIANTE_VE_TODOS"] = True
    ok("TM XAVIER" in gen(zc, 3).get_data(as_text=True), "configuración CENTRALIZADOR_ESTUDIANTE_VE_TODOS=1: nómina completa")
    app.config["CENTRALIZADOR_ESTUDIANTE_VE_TODOS"] = False
    ok(db_(lambda: AuditLog.query.filter_by(modulo="reporte_centralizador").count()) >= 5, "la generación del Centralizador queda en la auditoría")

    # ---- Reportes → Alumnos asignados a Profesor
    roles_ap = lambda: db_(lambda: {r.rol for r in Rol.query.all() if any(p.permiso == "reporte_alumnos_profesor" for p in r.permisos)})
    ok(roles_ap() == {"Administrador", "Profesor", "Transcriptor"}, "permiso reporte_alumnos_profesor: administrador, profesor y transcriptor")
    def _quitar_ap():
        db.session.execute(db.text("DELETE FROM rol_permiso WHERE id_permiso IN (SELECT id FROM permiso WHERE permiso='reporte_alumnos_profesor')"))
        db.session.execute(db.text("DELETE FROM permiso WHERE permiso='reporte_alumnos_profesor'"))
        db.session.commit()
    db_(_quitar_ap)
    ok("reporte_alumnos_profesor" in app.test_cli_runner().invoke(args=["seed"]).output and roles_ap() == {"Administrador", "Profesor", "Transcriptor"},
       "flask seed otorga el permiso de este reporte en una base existente")

    for nombre, cli in (("administrador", admin), ("transcriptor", tr), ("profesor", p1)):
        ok("Alumnos asignados a Profesor" in cli.get("/").get_data(as_text=True) and cli.get("/reportes/alumnos-profesor").status_code == 200,
           f"menú Reportes → Alumnos asignados a Profesor disponible para {nombre}")
    ok("Alumnos asignados a Profesor" not in a1.get("/").get_data(as_text=True) and "Alumnos asignados a Profesor" not in cn.get("/").get_data(as_text=True)
       and a1.get("/reportes/alumnos-profesor").status_code == 403 and cn.get("/reportes/alumnos-profesor").status_code == 403
       and a1.get("/reportes/alumnos-profesor/generar?id_gestion=1&id_profesor=1").status_code == 403, "estudiante y consulta: sin acceso a este reporte")
    ok(p2.get("/reportes/alumnos-profesor").status_code == 403, "profesor sin vincular: sin acceso (nunca ve datos de otros)")
    ok(app.test_client().get("/reportes/alumnos-profesor").status_code == 302, "este reporte exige sesión iniciada")
    sel_admin = admin.get("/reportes/alumnos-profesor").get_data(as_text=True)
    sel_prof = p1.get("/reportes/alumnos-profesor").get_data(as_text=True)
    ok('id="sel-profesor"' in sel_admin and 'id="sel-profesor"' not in sel_prof and "Pérez Ana" in sel_prof, "selector: el administrador elige profesor; el profesor ve solo el suyo")
    good, det = bien_formado(sel_admin)
    ok(good, f"HTML bien formado: pantalla del reporte {'' if good else det}")
    ok("Pérez Ana" in admin.get("/reportes/alumnos-profesor/profesores?id_gestion=1", headers=HXH).get_data(as_text=True)
       and "Sin profesores" in admin.get("/reportes/alumnos-profesor/profesores?id_gestion=99999", headers=HXH).get_data(as_text=True)
       and p1.get("/reportes/alumnos-profesor/profesores?id_gestion=1").status_code == 403, "selector de profesor según la gestión (solo administrador/transcriptor)")

    # datos de prueba: el profesor 1 dicta las materias de los cursos de Técnico Medio (incluidos arrastres)
    db_(lambda: (db.session.query(Asignado).filter(Asignado.id_curso.in_([C1, C1b, C2])).update({"id_profesor": 1}, synchronize_session=False), db.session.commit()))
    gen_ap = lambda cli, fmt="html", ges=1, prof=1, extra="": cli.get(f"/reportes/alumnos-profesor/generar?id_gestion={ges}&id_profesor={prof}&formato={fmt}{extra}")
    hp = gen_ap(admin).get_data(as_text=True)
    ok(all(t in hp for t in ("ALUMNOS ASIGNADOS A PROFESOR", "Pérez Ana", "Gestión 2026", "Rojas Carla", "Tm Xavier", "Lenguaje Musical",
                              "CURSO: PIANO I A", "CURSO: 1RO. TÉCNICO MEDIO A", "APELLIDOS Y NOMBRES", "MATERIA ASIGNADA", "CURSO DE INSCRIPCIÓN", "arrastre")),
       "reporte (HTML): profesor como título, gestión, grupos por curso y columnas pedidas")
    good, det = bien_formado(hp)
    ok(good, f"HTML bien formado: reporte {'' if good else det}")

    def _datos():
        d = datos_alumnos_profesor(db.session.get(Profesor, 1), db.session.get(Gestion, 1))
        ordenados = all(
            [(_clave_orden(f["paterno"]), _clave_orden(f["materno"]), _clave_orden(f["nombre"]), _clave_orden(f["materia"])) for f in g["filas"]]
            == sorted((_clave_orden(f["paterno"]), _clave_orden(f["materno"]), _clave_orden(f["nombre"]), _clave_orden(f["materia"])) for f in g["filas"])
            for g in d["grupos"])
        return d, ordenados
    d_ap, ordenados = db_(_datos)
    nombres_curso = [g["curso"] for g in d_ap["grupos"]]
    ok(ordenados and len(nombres_curso) >= 4 and len(set(nombres_curso)) == len(nombres_curso)
       and all([f["n"] for f in g["filas"]] == list(range(1, len(g["filas"]) + 1)) for g in d_ap["grupos"]),
       "agrupado por curso (sin repetir) y ordenado por apellido paterno, apellido materno, nombre (y materia)")
    grupo_2do = next(g for g in d_ap["grupos"] if g["curso"].startswith("2do"))
    ok([f["nombre"] for f in grupo_2do["filas"] if f["materia"] == "B1"] == ["Xavier", "Yara", "Zoe"]
       and any(f["arrastre"] for g in d_ap["grupos"] for f in g["filas"]), "orden por nombre dentro del mismo apellido y marca de arrastre")
    ok(d_ap["total_alumnos"] == len({f["nombre_completo"] for g in d_ap["grupos"] for f in g["filas"]})
       and d_ap["total_asignaciones"] == sum(len(g["filas"]) for g in d_ap["grupos"]), "totales de alumnos y asignaciones coherentes")
    ok(_clave_orden("Muñoz") > _clave_orden("Munoz") and _clave_orden("Ñandú") > _clave_orden("Nuñez") and _clave_orden("Árbol") < _clave_orden("Berna"),
       "orden alfabético en español (tildes y ñ)")

    # el profesor solo obtiene SU reporte aunque pida otro
    hp1 = gen_ap(p1, prof=2).get_data(as_text=True)
    ok("Pérez Ana" in hp1 and "GÓMEZ LUIS" not in hp1.upper(), "profesor: siempre su propio reporte (ignora el id_profesor pedido)")
    ok(gen_ap(tr).status_code == 200, "transcriptor: reporte de cualquier profesor")

    # exclusiones y gestión
    db_(lambda: (db.session.query(Inscrito).filter_by(id_alumno=Y, id_curso=C1).update({"abandono": True}), db.session.commit()))
    d2, _o = db_(_datos)
    g_c1 = next(g for g in d2["grupos"] if g["curso"].startswith("1ro. Técnico Medio A"))
    ok(all(f["nombre"] != "Yara" for f in g_c1["filas"]), "alumnos que abandonaron no se incluyen")
    db_(lambda: (db.session.query(Inscrito).filter_by(id_alumno=Y, id_curso=C1).update({"abandono": False}), db.session.commit()))
    db_(lambda: (db.session.query(Asignado).filter_by(id=old_asig).update({"id_profesor": 1}), db.session.commit()))
    ok("Vega" in gen_ap(admin, ges=2).get_data(as_text=True) and "Vega" not in gen_ap(admin, ges=1).get_data(as_text=True),
       "el reporte respeta la gestión elegida")
    db_(lambda: (db.session.query(Asignado).filter_by(id=old_asig).update({"id_profesor": None}), db.session.commit()))

    rx = gen_ap(admin, "xlsx")
    ok(rx.status_code == 200 and "spreadsheetml" in rx.mimetype and "attachment" in rx.headers["Content-Disposition"], "exportar a Excel (.xlsx)")
    import io
    from openpyxl import load_workbook
    wb2 = load_workbook(io.BytesIO(rx.data))
    hoja1 = {c.value for fila in wb2["Alumnos asignados"].iter_rows() for c in fila if c.value is not None}
    filas_datos = list(wb2["Datos"].iter_rows(min_row=2, values_only=True))
    ok(wb2.sheetnames == ["Alumnos asignados", "Datos"] and {"ALUMNOS ASIGNADOS A PROFESOR", "PÉREZ ANA", "ROJAS CARLA", "APELLIDOS Y NOMBRES"} <= hoja1
       and len(filas_datos) == d_ap["total_asignaciones"] + sum(1 for _ in []) and wb2["Datos"].auto_filter.ref.startswith("A1:J")
       and wb2["Datos"]["D1"].value == "Apellido paterno", "Excel: hoja del reporte agrupado y hoja «Datos» plana con filtros")
    def _inyeccion_ap():
        d = datos_alumnos_profesor(db.session.get(Profesor, 1), db.session.get(Gestion, 1))
        d["grupos"][0]["filas"][0]["nombre_completo"] = "=1+1"
        d["grupos"][0]["filas"][0]["nombre"] = "=HYPERLINK(\"http://x\")"
        w = load_workbook(io.BytesIO(alumnos_profesor_xlsx(d)))
        return [c.data_type for fila in w["Alumnos asignados"].iter_rows() for c in fila if c.value == "=1+1"] + \
               [c.data_type for fila in w["Datos"].iter_rows() for c in fila if isinstance(c.value, str) and c.value.startswith("=HYPERLINK")]
    tipos = db_(_inyeccion_ap)
    ok(len(tipos) == 2 and all(t == "s" for t in tipos), "Excel: valores que empiezan con «=» se guardan como texto (sin fórmulas)")

    rpd = gen_ap(admin, "pdf")
    if pdf_disponible():
        ok(rpd.status_code == 200 and rpd.mimetype == "application/pdf" and rpd.data[:5] == b"%PDF-", "exportar a PDF")
    else:
        ok(rpd.status_code == 501, "PDF no disponible: aviso (501); Excel y HTML siguen funcionando")
    ok(gen_ap(admin, "otro").status_code == 400 and gen_ap(admin, prof=99999).status_code == 404 and gen_ap(admin, ges=99999).status_code == 404,
       "formato no válido (400); profesor o gestión inexistentes (404)")
    ok(db_(lambda: AuditLog.query.filter_by(modulo="reporte_alumnos_profesor").count()) >= 6, "la generación del reporte queda en la auditoría")
    db_(lambda: (db.session.query(Asignado).filter(Asignado.id_curso.in_([C1, C1b, C2])).update({"id_profesor": None}, synchronize_session=False), db.session.commit()))

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
