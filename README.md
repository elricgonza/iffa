# IFALFA — Software de gestión para el Instituto de Educación Musical

Stack: **PostgreSQL · Python/Flask · HTMX · Alpine.js · Tailwind CSS** (entorno: Ubuntu 22.04/24.04).

Funciones: autenticación y autorización por roles/permisos, CRUD de todas las tablas, inscripción que genera
pagos y asignaciones, calificaciones con cálculo automático, alcance de datos por rol (un profesor solo ve sus
materias, un estudiante solo lo suyo), auditoría, diseño responsivo (menú lateral colapsable, tablas con scroll
y columnas secundarias ocultas en móvil, formularios como modal de pantalla completa en celular).

## 1. Instalación en Ubuntu

```bash
# 1) Paquetes del sistema
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib

# 2) Base de datos (cambie la clave)
sudo -u postgres psql <<'SQL'
CREATE ROLE uaded LOGIN ENCRYPTED PASSWORD 'CAMBIAR_CLAVE';
CREATE DATABASE dbaded OWNER uaded ENCODING 'UTF8';
SQL

# 3) Proyecto
sudo mkdir -p /opt/ifalfa && sudo chown $USER /opt/ifalfa      # (o cualquier carpeta)
cd /opt/ifalfa                                                  # copie aquí el contenido de este proyecto
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 4) Configuración
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"       # pegue el resultado en SECRET_KEY
nano .env                                                       # SECRET_KEY, DATABASE_URL (clave del paso 2)

# 5) Esquema, roles/permisos y primer administrador
PGPASSWORD='CAMBIAR_CLAVE' psql -h localhost -U uaded -d dbaded -f scripts/schema.sql
flask seed
flask create-user admin --email admin@instituto.edu          # pide la contraseña (mín. 8 caracteres)

# 6) Ejecutar (desarrollo)
flask run --debug            # http://127.0.0.1:5000
```

## 2. Puesta en producción (Gunicorn + systemd + Nginx)

```bash
sudo chown -R www-data:www-data /opt/ifalfa
sudo cp deploy/ifalfa.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ifalfa
sudo cp deploy/nginx-ifalfa.conf /etc/nginx/sites-available/ifalfa     # editar server_name
sudo ln -s /etc/nginx/sites-available/ifalfa /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx && sudo certbot --nginx -d su.dominio
```
Con HTTPS ponga en `.env`: `SESSION_COOKIE_SECURE=1` y `TRUST_PROXY=1`, y reinicie (`sudo systemctl restart ifalfa`).
Respaldo: `pg_dump -h localhost -U uaded dbaded > respaldo.sql`.

## 3. Roles y permisos

Cada recurso tiene permisos `<recurso>_ver|crear|editar|eliminar` (catálogo en **Administración → Permisos**;
los roles se editan en **Administración → Roles** y la asignación a usuarios en **Usuarios**). `flask seed` es
idempotente: crea lo que falta, **no pisa** permisos que usted ya personalizó (excepto Administrador, que siempre
recibe todos).

| Rol | Puede |
|---|---|
| Administrador | Todo, incluida administración de usuarios, roles y auditoría |
| Transcriptor | Alta/edición de alumnos, inscripciones, asignaciones y **notas de cualquier curso**; consulta catálogos |
| Profesor | Ver y **calificar solo las materias que tiene asignadas** (y sus alumnos); consulta catálogos |
| Estudiante | Ver **solo** sus inscripciones, asignaciones, notas y pagos |
| Consulta | Solo lectura de toda la información académica y de pagos |

**Vínculo usuario ↔ persona:** un usuario con rol Profesor/Estudiante se vincula a su ficha desde el campo
*Usuario de acceso* en **Profesores** / **Alumnos**. Si no está vinculado, el sistema **no muestra ningún dato**
(nunca cae a la vista global).

## 4. Reglas de negocio implementadas

* **Inscripción** (alumno + curso): valida duplicado y cupos (`curso.capacidad`); al guardar **genera el pago**
  aplicando `descuento` (%) sobre `CUOTA_BASE` (o el monto base indicado en el formulario), repartido en
  `NUM_CUOTAS` registros; y **asigna por defecto las materias del grado del curso**, con su registro de nota vacío.
  Si cambia el descuento, se reajustan las cuotas aún no pagadas.
* **Asignado**: materias de otros grados solo si `inscrito.otras_materias` es verdadero y si el alumno aprobó los
  **requisitos** de la materia. El profesor se asigna individualmente o en bloque por curso/materia
  (botón *Asignar profesor por curso/materia*); al inscribir a un alumno nuevo se reutiliza el profesor ya asignado
  a esa materia en el curso.
* **Notas** (0–100): `nota_final` = promedio de las notas registradas; si no alcanza `NOTA_APROBACION` (51 por
  defecto) y existe nota de 2.º turno (`2t`), ésta es la `nota_aprob`. `aprobado` se calcula automáticamente.
* **1ro./2do. Técnico Medio** (grados llamados «1ro. Técnico Medio» y «2do. Técnico Medio» del mismo instrumento;
  **solo el Administrador** puede inscribir/asignar en ellos). «Aprobada» = nota con `aprobado = true` en cualquier
  inscripción previa del alumno.
  * Inscripción a **1ro**: se asignan solo las materias de 1ro aún no aprobadas. Manualmente pueden agregarse
    materias de 2do si existe un curso de 2do habilitado en la gestión vigente y están aprobados sus requisitos.
  * Inscripción a **2do**: si tiene aprobadas todas las de 1ro, se asignan automáticamente las de 2do no aprobadas;
    si no, no se asigna nada automáticamente y se permiten (manualmente) los **arrastres** de 1ro no aprobados y las
    materias de 2do cuyos requisitos estén aprobados.
  * **Asignaciones → «+ Nuevo»**: el combo *Inscrito* lista solo a los inscritos (confirmados y sin abandono) de la gestión
    activa. Luego se elige el **curso** y después la **materia**: en Técnico Medio el curso puede ser el propio o el de
    otro grado con materias pendientes (p. ej. un alumno de 2do. elige entre «1ro. Técnico Medio A», «B», … para un
    arrastre); si hay un único curso candidato se preselecciona, y la lista de materias muestra solo las pendientes
    o permitidas de ese curso.
  * Las materias de otro grado se cursan en un curso de ese grado de la gestión vigente (queda en `asignado.id_curso`).
    El selector de materia de *Asignaciones* muestra solo las permitidas para el inscrito elegido.
* **Gestión activa**: solo UNA gestión académica puede estar «Activa» (se valida al crear/editar y lo refuerza un
  índice único en la base: en bases ya creadas ejecute `scripts/migracion_gestion_activa_unica.sql`). Para cambiar de
  gestión primero se desactiva la vigente y luego se activa la nueva. La gestión activa se muestra siempre en el menú
  lateral (junto al usuario) y en la barra superior móvil. Crear/editar/eliminar gestiones es exclusivo del Administrador.
  Los listados de **Cursos, Inscripciones, Asignaciones y Calificaciones** muestran por defecto la gestión activa; el
  combo *Gestión* permite elegir otra o «Todas las gestiones».
* **Fechas de transcripción de notas** (solo Administrador: *Administración → Fechas Transcripción Notas*): se guardan
  en `config_notas.json` (raíz del proyecto, o la ruta de `CONFIG_NOTAS_PATH` en `.env`; se crea solo la primera vez y
  el usuario del servicio —`www-data`— debe poder escribir en esa carpeta). Por cada período (`nota1`…`nota4` y `2t`):
  `habilitar_X` (true/false), `transcripcion_X_inicio` y `transcripcion_X_final` (fechas `AAAA-MM-DD`, inclusive).
  Con `habilitar_X = true` solo se puede transcribir dentro del rango; con `false` queda restringido. Los campos
  fuera de plazo se muestran bloqueados y el servidor conserva su valor aunque se manipule el formulario. El
  Administrador no está sujeto a la restricción. Si el archivo se daña, se restringe todo (opción segura).
* **Pagos**: se generan solo desde la inscripción; se actualizan registrando método, fecha y referencia.
* No se eliminan inscripciones con pagos o notas registradas, ni registros con dependencias.
* **Auditoría**: altas, cambios, bajas, inicios/cierres de sesión (incluidos intentos fallidos) y acciones masivas.

> Supuestos a confirmar (el documento no los define): cálculo del promedio, nota mínima 51, monto/número de cuotas
> (configurables en `.env`). Ajústelos en `app/models.py` (`Nota.calcular`) y `.env`.

## 5. Ajustes al DDL original (`scripts/schema.sql`)

1. `instrumento` usaba `grado_id_seq` y el nombre `grado_pkey` (ya usado por `grado`): PostgreSQL lo rechaza.
   Ahora tiene su propia secuencia y `instrumento_pkey`.
2. `requisito.id_materia` pasa a `NOT NULL` (forma parte de la clave única) y se añade `CHECK (id_materia <> id_materia_req)`.
3. `usuario.last_login` con `DEFAULT CURRENT_DATE`.
4. Índices únicos: `(id_alumno,id_curso)` en inscrito, `(id_inscrito,id_materia)` en asignado, `id_asignado` en nota,
   y `usr_id_login` en profesor/alumno; `CHECK` de descuento 0–100.
5. Se omiten `CREATE ROLE ... PASSWORD '****'` y `OWNER TO` (se crea el rol en el paso 2 y las tablas quedan a su nombre).

## 6. Justificación de componentes adicionales

* **Flask-SQLAlchemy** (ORM), **Flask-Login** (sesiones), **Flask-WTF** (solo protección CSRF; HTMX envía el token en cabecera).
* **psycopg 3** como driver (`postgresql+psycopg://`). **Gunicorn + Nginx** para producción.
* HTMX/Alpine.js/Tailwind se cargan por CDN con versión fijada (sin paso de compilación). Para uso sin Internet o
  máximo rendimiento, descargue los archivos a `app/static/` y compile Tailwind con su CLI.

## 7. Seguridad

Contraseñas con hash (werkzeug), CSRF en todos los POST, cookies HttpOnly/SameSite, límite de 5 intentos de login
fallidos por IP cada 5 min (en memoria por proceso), redirecciones `next` validadas, cabeceras de seguridad,
autorización verificada **en el servidor** en cada ruta (ocultar botones no es la protección) y datos filtrados por rol.

## 8. Pruebas

`SMOKE_TEST_CONFIRM=yes python tests/smoke_test.py` — prueba de extremo a extremo (⚠ **borra los datos académicos**
de la base de `DATABASE_URL`; usar solo en una base de pruebas).

## 9. Estructura

```
wsgi.py  config.py  requirements.txt  .env.example
scripts/schema.sql          esquema PostgreSQL
app/__init__.py             fábrica, seguridad HTTP, comandos `flask seed` y `flask create-user`
app/models.py               modelos        app/services.py   pagos / asignaciones / requisitos
app/crud.py                 motor CRUD HTMX  app/resources.py  definición de cada recurso
app/views.py                login, panel, perfil, asignación masiva   app/security.py  permisos y auditoría
app/templates/              base, login, panel, crud/*
deploy/                     systemd y Nginx
```
