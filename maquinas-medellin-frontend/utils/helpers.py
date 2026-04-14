from flask import json


def parse_json_col(value, default):
    """
    Parsea una columna JSON de la BD que puede llegar como string
    o ya deserializada como dict/list por el conector de MySQL.
    Retorna `default` si el valor es None o el parse falla.
    """
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default
