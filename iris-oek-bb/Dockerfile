# Python 3.11+ is required by the assignment; 3.11 is used here. All pinned packages
# ship binary wheels (GEOS/PROJ/GDAL/libpq are bundled), so no system packages are needed.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY . .

RUN useradd --create-home appuser && mkdir -p /app/reports && chown -R appuser /app
USER appuser

CMD ["python", "scripts/run.py", "--help"]
