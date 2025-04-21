# Basis-Image mit Python 3.10
FROM python:3.10

# Installiere benötigte Pakete für OpenCV & Tesseract OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-deu \
    libtesseract-dev \
    libasound2-dev \
    libavcodec-extra \
    libavformat-dev \
    libswscale-dev \
    libxext6 \
    libxrender-dev \
    libsm6 \
    ffmpeg \
    locales \
    fonts-noto-color-emoji \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Note: There's an issue with matplotlib loading the Noto Color Emoji font
# In the code we only use DejaVu Sans for compatibility

RUN echo "de_DE.UTF-8 UTF-8" >> /etc/locale.gen; \
    locale-gen

# Setze das Arbeitsverzeichnis
WORKDIR /app

# Kopiere die `pyproject.toml` und `poetry.lock` (wenn vorhanden)
COPY pyproject.toml poetry.lock* /app/

# Installiere Poetry
RUN pip install poetry

# Installiere Abhängigkeiten nur mit Poetry (ohne virtuelle Umgebung in `/app/.venv`)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi

# Kopiere den gesamten Code ins Image
COPY . /app

# Starte das Skript `bot.py`
CMD ["python", "./src/bot.py"]
