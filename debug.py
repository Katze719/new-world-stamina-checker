import cv2
import pytesseract
import numpy as np
import os
import re
import multiprocessing as mp
from queue import Empty

# Konfiguriere Tesseract für Linux
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

# Video-Pfad
video_path = "2025-02-02 21-58-14.mp4"

# Output-Verzeichnis für erkannte Frames
output_dir = "./output/"
os.makedirs(output_dir, exist_ok=True)

# Öffne das Video
cap = cv2.VideoCapture(video_path)

if not cap.isOpened():
    print("Fehler: Video konnte nicht geladen werden!")
    exit()

# **Hole die FPS des Videos**
fps = int(cap.get(cv2.CAP_PROP_FPS))
frames_to_skip = fps * 3  # 4 Sekunden keine neue "0" erkennen
skip_until_frame = mp.Value("i", 0)  # Geteilter Wert zwischen Prozessen

# Frame-Zähler
frame_count = mp.Value("i", 0)

# Anzahl der CPU-Kerne für parallele Verarbeitung
num_workers = max(2, mp.cpu_count() - 1)  # Mindestens 2 Prozesse verwenden

def frame_reader(queue, cap, frame_count):
    """ Liest Frames aus dem Video und legt sie in eine Queue """
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        with frame_count.get_lock():
            frame_id = frame_count.value
            frame_count.value += 1

        queue.put((frame_id, frame))

    queue.put(None)  # Signalisiert das Ende

def process_frames(queue, output_queue, skip_until_frame):
    """ Verarbeitet Frames parallel und prüft Stamina = 0 """
    while True:
        try:
            item = queue.get(timeout=5)
        except Empty:
            break

        if item is None:
            break

        frame_id, frame = item

        # **Frames überspringen, falls eine `0` in den letzten 4 Sekunden erkannt wurde**
        with skip_until_frame.get_lock():
            if frame_id < skip_until_frame.value:
                continue

        # Größe des Frames abrufen
        height, width, _ = frame.shape

        # ROI extrahieren
        roi_x1, roi_x2 = int(width * 0.495), int(width * 0.505)  # Mittig
        roi_y1, roi_y2 = int(height * 0.91), int(height * 0.93)  # Unten
        roi = frame[roi_y1:roi_y2, roi_x1:roi_x2]

        # Bild in Graustufen konvertieren und verarbeiten
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        gray = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

        # Überprüfen, ob mehr als 25% der Pixel schwarz sind
        black_pixels = np.sum(gray == 0)
        total_pixels = gray.size
        black_ratio = black_pixels / total_pixels

        if black_ratio >= 0.25:
            is_zero_stamina = False  # Verwerfe als ungültig
        else:
            # OCR mit `--psm 10` für einzelne Zeichen
            custom_config = "--psm 10 digits"
            stamina_text = pytesseract.image_to_string(gray, config=custom_config).strip()
            is_zero_stamina = re.fullmatch(r"0", stamina_text) is not None

        if is_zero_stamina:
            output_queue.put((frame_id, frame, gray))

            with skip_until_frame.get_lock():
                skip_until_frame.value = frame_id + frames_to_skip  # **Nächste 4 Sekunden überspringen**

def save_frames(output_queue):
    """ Speichert die erkannten Frames parallel """
    while True:
        try:
            item = output_queue.get(timeout=5)
        except Empty:
            break

        if item is None:
            break

        frame_id, frame, roi = item

        # Speichere den Frame mit erkannter Stamina = 0
        frame_filename = os.path.join(output_dir, f"frame_{frame_id}.png")
        cv2.imwrite(frame_filename, frame)

        # Speichere das ROI-Bild (nur Stamina-Zahl)
        roi_filename = os.path.join(output_dir, f"roi_{frame_id}.png")
        cv2.imwrite(roi_filename, roi)

if __name__ == "__main__":
    # Starte Prozesse
    frame_queue = mp.Queue(maxsize=50)  # Frame Queue für Producer-Consumer
    output_queue = mp.Queue()

    # Starte einen Prozess für das Lesen der Frames
    reader_process = mp.Process(target=frame_reader, args=(frame_queue, cap, frame_count))
    reader_process.start()

    # Starte mehrere Prozesse für die Verarbeitung der Frames
    worker_processes = []
    for _ in range(num_workers):
        p = mp.Process(target=process_frames, args=(frame_queue, output_queue, skip_until_frame))
        p.start()
        worker_processes.append(p)

    # Starte einen Prozess für das Speichern der Frames
    saver_process = mp.Process(target=save_frames, args=(output_queue,))
    saver_process.start()

    # Warten, bis alle Prozesse beendet sind
    reader_process.join()
    for p in worker_processes:
        p.join()
    saver_process.join()

    print(f"Erkannte Frames gespeichert in: {output_dir}")
