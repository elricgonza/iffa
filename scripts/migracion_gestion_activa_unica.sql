-- Garantiza en la base de datos que solo UNA gestión pueda estar activa.
-- Ejecutar una vez en bases ya creadas:
--     psql -h localhost -U uaded -d dbaded -f scripts/migracion_gestion_activa_unica.sql
-- Si hay más de una gestión activa, el script se detiene y las lista: deje activa solo la vigente
-- (Gestiones académicas → Editar) y vuelva a ejecutarlo.
DO $$
DECLARE n integer; lista text;
BEGIN
  SELECT count(*), string_agg(id || ' (' || coalesce(gestion::text, '?') || ' - ' || plan || ')', ', ')
    INTO n, lista FROM gestion WHERE activo;
  IF n > 1 THEN
    RAISE EXCEPTION 'Hay % gestiones activas: %. Deje solo una activa y reintente.', n, lista;
  END IF;
  CREATE UNIQUE INDEX IF NOT EXISTS idx_gestion_activa ON gestion (activo) WHERE activo;
END $$;
