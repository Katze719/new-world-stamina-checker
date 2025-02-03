import cv2
import pytesseract
import numpy as np
import os
import re

# Konfiguriere Tesseract für Linux
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

# Video-Pfad
# video_path = "2025-02-03 17-06-46.mp4"
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
skip_until_frame = 0  # Variable zum Tracken des nächsten gültigen Frames

# Frame-Zähler & Null-Stamina-Zähler
zero_stamina_count = 0
frame_count = 0  # Gesamtanzahl der Frames
consecutive_zero_frames = 0  # Zähler für aufeinanderfolgende "0"-Frames
required_zero_frames = 1  # Mindestens 1 Frame mit "0" erforderlich

while True:
    ret, frame = cap.read()
    
    if not ret:
        break  # Video zu Ende
    
    frame_count += 1

    # **Frames überspringen, falls eine `0` in den letzten 4 Sekunden erkannt wurde**
    if frame_count < skip_until_frame:
        continue

    # Größe des Frames abrufen
    height, width, _ = frame.shape

    # 🚀 Dein perfektes ROI nutzen
    roi_x1, roi_x2 = int(width * 0.495), int(width * 0.505)  # Mittig
    roi_y1, roi_y2 = int(height * 0.91), int(height * 0.93)  # Unten
    roi = frame[roi_y1:roi_y2, roi_x1:roi_x2]

    # Bild in Graustufen konvertieren und verarbeiten
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gray = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    # Überprüfen, ob mehr als 40% der Pixel schwarz sind
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

    # Prüfen, ob mindestens 1 Frame mit "0" erkannt wurde
    if is_zero_stamina:
        consecutive_zero_frames += 1
    else:
        consecutive_zero_frames = 0

    # Erst wenn "0" mindestens 1 Frame hintereinander erkannt wurde, zählen wir es
    if consecutive_zero_frames == required_zero_frames:
        zero_stamina_count += 1
        skip_until_frame = frame_count + frames_to_skip  # **Nächste 4 Sekunden überspringen**

        # Speichere den Frame mit erkannter Stamina = 0
        frame_filename = os.path.join(output_dir, f"frame_{frame_count}.png")
        cv2.imwrite(frame_filename, frame)

        # Speichere das ROI-Bild (nur Stamina-Zahl) zur besseren Überprüfung
        roi_filename = os.path.join(output_dir, f"roi_{frame_count}.png")
        cv2.imwrite(roi_filename, gray)

    # Debug-Log für Headless-Modus
    if frame_count % 100 == 0:  # Alle 100 Frames Debug-Log schreiben
        print(f"Frame {frame_count}: Schwarze Pixel: {black_ratio:.2%}, Erkannte Stamina-Zahl = '{stamina_text}', Consecutive 0-Frames: {consecutive_zero_frames}")

# Ergebnisse ausgeben
print(f"Stamina auf 0 gefallen: {zero_stamina_count} Mal")
print(f"Erkannte Frames gespeichert in: {output_dir}")

# Aufräumen
cap.release()
