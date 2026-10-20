-- =====================================================================
-- IFALFA - Esquema PostgreSQL (>= 12)
-- Basado en la estructura especificada. Ejecutar como el usuario dueño
-- de la base (uaded) conectado a la base dbaded:
--     psql -U uaded -h localhost -d dbaded -f scripts/schema.sql
-- Ajustes respecto al DDL original (ver README):
--   1. instrumento usa su propia secuencia y PK (el original reutilizaba
--      grado_id_seq y el nombre grado_pkey, lo que falla en PostgreSQL).
--   2. requisito.id_materia es NOT NULL (forma parte de la clave única).
--   3. usuario.last_login tiene DEFAULT CURRENT_DATE.
--   4. Se agregan índices/restricciones de integridad adicionales.
-- =====================================================================
BEGIN;

CREATE SEQUENCE alumno_id_seq;
CREATE SEQUENCE asignado_id_seq;
CREATE SEQUENCE curso_id_seq;
CREATE SEQUENCE gestion_id_seq;
CREATE SEQUENCE grado_id_seq;
CREATE SEQUENCE instrumento_id_seq;
CREATE SEQUENCE inscrito_id_seq;
CREATE SEQUENCE materia_id_seq;
CREATE SEQUENCE nota_id_seq;
CREATE SEQUENCE pago_id_seq;
CREATE SEQUENCE permiso_id_seq;
CREATE SEQUENCE profesor_id_seq;
CREATE SEQUENCE rol_id_seq;
CREATE SEQUENCE usuario_id_seq;
CREATE SEQUENCE audit_log_id_seq;

CREATE TABLE usuario (
  id integer NOT NULL DEFAULT nextval('usuario_id_seq'),
  usuario varchar(80) NOT NULL,
  email varchar(100) NOT NULL,
  password varchar(255) NOT NULL,
  activo boolean,
  last_login date NOT NULL DEFAULT CURRENT_DATE,
  creado timestamp,
  act timestamp,
  CONSTRAINT usuario_pkey PRIMARY KEY (id),
  CONSTRAINT usuario_usuario_key UNIQUE (usuario)
);

CREATE TABLE rol (
  id integer NOT NULL DEFAULT nextval('rol_id_seq'),
  rol varchar(50) NOT NULL,
  descripcion varchar(255),
  creado timestamp, act timestamp,
  CONSTRAINT rol_pkey PRIMARY KEY (id),
  CONSTRAINT rol_rol_key UNIQUE (rol)
);

CREATE TABLE permiso (
  id integer NOT NULL DEFAULT nextval('permiso_id_seq'),
  permiso varchar(50) NOT NULL,
  descripcion varchar(255),
  creado timestamp, act timestamp,
  CONSTRAINT permiso_pkey PRIMARY KEY (id),
  CONSTRAINT permiso_permiso_key UNIQUE (permiso)
);

CREATE TABLE usuario_rol (
  id_usuario integer NOT NULL,
  id_rol integer NOT NULL,
  CONSTRAINT usuario_rol_pkey PRIMARY KEY (id_usuario, id_rol),
  CONSTRAINT usuario_rol_usu_id_fkey FOREIGN KEY (id_usuario) REFERENCES usuario (id),
  CONSTRAINT usuario_rol_rol_id_fkey FOREIGN KEY (id_rol) REFERENCES rol (id)
);

CREATE TABLE rol_permiso (
  id_rol integer NOT NULL,
  id_permiso integer NOT NULL,
  CONSTRAINT rol_permiso_pkey PRIMARY KEY (id_rol, id_permiso),
  CONSTRAINT rol_permiso_rol_id_fkey FOREIGN KEY (id_rol) REFERENCES rol (id),
  CONSTRAINT rol_permiso_per_id_fkey FOREIGN KEY (id_permiso) REFERENCES permiso (id)
);

CREATE TABLE gestion (
  id integer NOT NULL DEFAULT nextval('gestion_id_seq'),
  gestion smallint,
  plan varchar(150) NOT NULL,
  inicio date NOT NULL, fin date NOT NULL,
  activo boolean NOT NULL,
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  CONSTRAINT gestion_pkey PRIMARY KEY (id)
);

CREATE TABLE instrumento (
  id integer NOT NULL DEFAULT nextval('instrumento_id_seq'),
  instrumento varchar(150) NOT NULL,
  area varchar(50),
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  CONSTRAINT instrumento_pkey PRIMARY KEY (id)
);

CREATE TABLE grado (
  id integer NOT NULL DEFAULT nextval('grado_id_seq'),
  grado varchar(255) NOT NULL,
  nivel varchar(150) NOT NULL,
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  id_instrumento integer NOT NULL,
  CONSTRAINT grado_pkey PRIMARY KEY (id),
  CONSTRAINT instrumento_fk FOREIGN KEY (id_instrumento) REFERENCES instrumento (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE TABLE materia (
  id integer NOT NULL DEFAULT nextval('materia_id_seq'),
  materia varchar(150) NOT NULL,
  sigla varchar(20),
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  id_grado integer NOT NULL,
  CONSTRAINT materia_pkey PRIMARY KEY (id),
  CONSTRAINT grado_fk FOREIGN KEY (id_grado) REFERENCES grado (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE TABLE requisito (
  id_materia integer NOT NULL,
  id_materia_req integer NOT NULL,
  CONSTRAINT materia_fk FOREIGN KEY (id_materia) REFERENCES materia (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT materia_req_fk FOREIGN KEY (id_materia_req) REFERENCES materia (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT requisito_distintos CHECK (id_materia <> id_materia_req)
);
CREATE UNIQUE INDEX idx_uniq_req ON requisito USING btree (id_materia, id_materia_req);

CREATE TABLE profesor (
  id integer NOT NULL DEFAULT nextval('profesor_id_seq'),
  nombre varchar(100) NOT NULL, paterno varchar(50), materno varchar(50),
  masculino boolean NOT NULL, ci integer, formacion varchar(100), email varchar(100),
  activo boolean NOT NULL, usr_id_login integer,
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  CONSTRAINT profesor_pkey PRIMARY KEY (id)
);
CREATE UNIQUE INDEX idx_profesor_login ON profesor (usr_id_login) WHERE usr_id_login IS NOT NULL;

CREATE TABLE curso (
  id integer NOT NULL DEFAULT nextval('curso_id_seq'),
  curso varchar(150),
  paralelo varchar(50) NOT NULL,
  aula varchar(50),
  capacidad smallint,
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  id_gestion integer NOT NULL,
  id_grado integer NOT NULL,
  CONSTRAINT curso_pkey PRIMARY KEY (id),
  CONSTRAINT gestion_fk FOREIGN KEY (id_gestion) REFERENCES gestion (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT grado_fk FOREIGN KEY (id_grado) REFERENCES grado (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE TABLE alumno (
  id integer NOT NULL DEFAULT nextval('alumno_id_seq'),
  nombre varchar(100) NOT NULL, paterno varchar(100), materno varchar(100),
  nacimiento date, masculino boolean NOT NULL, ci integer,
  direccion varchar(150), email varchar(100),
  activo boolean NOT NULL, obs varchar(100), usr_id_login integer, foto_ruta varchar(150),
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  CONSTRAINT alumno_pkey PRIMARY KEY (id)
);
CREATE UNIQUE INDEX idx_alumno_login ON alumno (usr_id_login) WHERE usr_id_login IS NOT NULL;

CREATE TABLE inscrito (
  id integer NOT NULL DEFAULT nextval('inscrito_id_seq'),
  reserva boolean,
  inscrito boolean NOT NULL,
  descuento smallint NOT NULL,
  motivo_descuento varchar(100),
  abandono boolean NOT NULL,
  obs varchar(200),
  otras_materias boolean NOT NULL DEFAULT false,
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  id_alumno integer NOT NULL,
  id_curso integer NOT NULL,
  id_instrumento integer NOT NULL,
  CONSTRAINT inscrito_pk PRIMARY KEY (id),
  CONSTRAINT inscrito_descuento_chk CHECK (descuento BETWEEN 0 AND 100),
  CONSTRAINT alumno_fk FOREIGN KEY (id_alumno) REFERENCES alumno (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT curso_fk FOREIGN KEY (id_curso) REFERENCES curso (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT instrumento_fk FOREIGN KEY (id_instrumento) REFERENCES instrumento (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE
);
CREATE UNIQUE INDEX idx_inscrito_alumno_curso ON inscrito (id_alumno, id_curso);

CREATE TABLE asignado (
  id integer NOT NULL DEFAULT nextval('asignado_id_seq'),
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  id_inscrito integer NOT NULL,
  id_curso integer NOT NULL,
  id_materia integer NOT NULL,
  id_profesor integer,
  CONSTRAINT asignado_pkey PRIMARY KEY (id),
  CONSTRAINT inscrito_fk FOREIGN KEY (id_inscrito) REFERENCES inscrito (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT curso_fk FOREIGN KEY (id_curso) REFERENCES curso (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT materia_fk FOREIGN KEY (id_materia) REFERENCES materia (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT profesor_fk FOREIGN KEY (id_profesor) REFERENCES profesor (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE
);
CREATE UNIQUE INDEX idx_asignado_ins_mat ON asignado (id_inscrito, id_materia);
CREATE INDEX idx_asignado_profesor ON asignado (id_profesor);
CREATE INDEX idx_asignado_curso_mat ON asignado (id_curso, id_materia);

CREATE TABLE nota (
  id integer NOT NULL DEFAULT nextval('nota_id_seq'),
  nota1 smallint, nota2 smallint, nota3 smallint, nota4 smallint,
  nota_final numeric(5,1),
  "2t" smallint,
  nota_aprob smallint,
  aprobado boolean,
  obs varchar(150),
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  id_asignado integer NOT NULL,
  CONSTRAINT nota_pkey PRIMARY KEY (id),
  CONSTRAINT asignado_fk FOREIGN KEY (id_asignado) REFERENCES asignado (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE
);
CREATE UNIQUE INDEX idx_nota_asignado ON nota (id_asignado);

CREATE TABLE pago (
  id integer NOT NULL DEFAULT nextval('pago_id_seq'),
  cuota double precision NOT NULL,
  pagado boolean,
  metodo_pago varchar(50),
  fecha_pago timestamp,
  referencia_pago varchar(100),
  obs varchar(100),
  creado timestamp NOT NULL, act timestamp NOT NULL, id_usuario integer NOT NULL,
  id_inscrito integer NOT NULL,
  CONSTRAINT pago_pkey PRIMARY KEY (id),
  CONSTRAINT inscrito_fk FOREIGN KEY (id_inscrito) REFERENCES inscrito (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE
);
CREATE INDEX idx_pago_inscrito ON pago (id_inscrito);

CREATE TABLE audit_log (
  id integer NOT NULL DEFAULT nextval('audit_log_id_seq'),
  usuario varchar(80) NOT NULL DEFAULT 'anónimo',
  accion varchar(20) NOT NULL,
  modulo varchar(50) NOT NULL,
  entidad_id integer,
  detalle text,
  ip varchar(45),
  endpoint varchar(150),
  metodo varchar(10),
  status smallint DEFAULT 200,
  creado timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  id_usuario integer,
  CONSTRAINT audit_log_accion_check CHECK (accion IN
    ('CREATE','UPDATE','DELETE','LOGIN','LOGOUT','BULK','ERROR')),
  CONSTRAINT audit_log_pkey PRIMARY KEY (id),
  CONSTRAINT usuario_fk FOREIGN KEY (id_usuario) REFERENCES usuario (id)
    MATCH FULL ON DELETE RESTRICT ON UPDATE CASCADE
);
COMMENT ON TABLE audit_log IS 'Registro de auditoría de acciones del sistema';
COMMENT ON COLUMN audit_log.accion IS 'Tipo de acción: CREATE, UPDATE, DELETE, LOGIN, LOGOUT, BULK, ERROR';
COMMENT ON COLUMN audit_log.modulo IS 'Módulo del sistema: grado, materia, alumno, pago, etc.';
COMMENT ON COLUMN audit_log.detalle IS 'JSON con detalles relevantes de la acción';
CREATE INDEX idx_audit_log_creado ON audit_log (creado) WITH (FILLFACTOR = 90);
CREATE INDEX idx_audit_log_accion ON audit_log (accion) WITH (FILLFACTOR = 90);
CREATE INDEX idx_audit_log_modulo ON audit_log (modulo) WITH (FILLFACTOR = 90);

COMMIT;
