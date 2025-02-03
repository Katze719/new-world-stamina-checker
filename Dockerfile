# Basis-Image mit Python 3.10
FROM python:3.10

# Installiere benötigte Pakete für OpenCV & Tesseract OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libasound2-dev \
    libavcodec-extra \
    libavformat-dev \
    libswscale-dev \
    libxext6 \
    libxrender-dev \
    libsm6 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Setze das Arbeitsverzeichnis
WORKDIR /app

# Kopiere die `pyproject.toml` und `poetry.lock` (wenn vorhanden)
COPY pyproject.toml poetry.lock* /app/

# Installiere Poetry
RUN pip install poetry

# Installiere Abhängigkeiten nur mit Poetry (ohne virtuelle Umgebung in `/app/.venv`)
RUN poetry config virtualenvs.create false \
    && poetry install --no-dev --no-interaction --no-ansi

# Kopiere den gesamten Code ins Image
COPY . /app

# Starte das Skript `bot.py`
CMD ["python", "bot.py"]
