# Maquinas Medellin - Frontend (Flask)

Instrucciones rápidas para desarrollar / ejecutar la app `maquinas-medellin-frontend` en Windows PowerShell.

Requisitos:
- Python 3.8+ (recomendado 3.10/3.11)
- pip

Crear y activar entorno virtual (PowerShell):

```powershell
# Crear venv
python -m venv .venv

# Activar (PowerShell)
.\.venv\Scripts\Activate.ps1

# Actualizar pip
python -m pip install --upgrade pip
```

Instalar dependencias:

```powershell
pip install -r requirements.txt
# Si tienes problemas con permisos o con versiones, usa:
python -m pip install Flask mysql-connector-python Flask-Cors pytz sentry-sdk
```

Ejecutar la app:

```powershell
# Desde el directorio maquinas-medellin-frontend
python app.py
# o exportar vars y usar flask run (PowerShell)
$env:FLASK_APP = 'app.py'; $env:FLASK_ENV = 'development'; flask run
```

Solución de problemas:
- Asegúrate de usar el mismo Python con que creaste el venv: `python -m pip install ...`.
- Si obtienes `no module named flask` después de instalar, activa el venv (o instala con `python -m pip install flask`).
- Revisa `python --version` y `which python` / `Get-Command python` para confirmar.

Si quieres, puedo añadir un `pyproject.toml` o `Pipfile` y configurar un entorno reproducible.

echo "## Último deploy: $(date '+%Y-%m-%d %H:%M:%S')" >> README.md
