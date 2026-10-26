import os
from dotenv import load_dotenv

load_dotenv()


def _req(name):
    v = os.environ.get(name)
    if not v or v.startswith("CAMBIAR"):
        raise RuntimeError(f"Falta la variable de entorno {name}. Revise el archivo .env")
    return v


class Config:
    SECRET_KEY = _req("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _req("DATABASE_URL")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 horas

    CUOTA_BASE = float(os.environ.get("CUOTA_BASE", "500"))
    NUM_CUOTAS = int(os.environ.get("NUM_CUOTAS", "1"))
    NOTA_APROBACION = float(os.environ.get("NOTA_APROBACION", "51"))
    PER_PAGE = int(os.environ.get("PER_PAGE", "15"))
    # Archivo con las fechas de habilitación de la transcripción de notas
    CONFIG_NOTAS_PATH = os.environ.get(
        "CONFIG_NOTAS_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_notas.json"))
