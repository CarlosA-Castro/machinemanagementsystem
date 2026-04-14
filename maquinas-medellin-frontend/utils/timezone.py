import pytz
from datetime import datetime

COLOMBIA_TZ = pytz.timezone('America/Bogota')


def get_colombia_time():
    """Retorna la hora actual en zona horaria Colombia."""
    return datetime.now(COLOMBIA_TZ)


def format_datetime_for_db(dt):
    """Formatea un datetime para guardarlo en BD (sin timezone info)."""
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def parse_db_datetime(dt_str):
    """Convierte un string de BD '%Y-%m-%d %H:%M:%S' a datetime con tz Colombia."""
    if not dt_str:
        return None
    naive_dt = datetime.strptime(str(dt_str), '%Y-%m-%d %H:%M:%S')
    return COLOMBIA_TZ.localize(naive_dt)
