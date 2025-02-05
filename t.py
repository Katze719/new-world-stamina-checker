import cv2
import numpy as np
from collections import Counter
import os
# Video laden
video_path = "./downloads/video.mp4"  # Pfad zum Video
cap = cv2.VideoCapture(video_path)

output_dir = "./output/"
os.makedirs(output_dir, exist_ok=True)

# Video-Parameter auslesen
frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = int(cap.get(cv2.CAP_PROP_FPS))
time_stamp = 3*60+27
frame_for_info = fps * time_stamp

# ROI in Prozent (für den ersten Durchgang)
roi_x1_percent, roi_y1_percent = 0.405, 0.82  
roi_x2_percent, roi_y2_percent = 0.605, 0.96  

# Mindestmaße für das Rechteck
min_rect_width = 150  # Mindestbreite 300 Pixel
min_rect_height = 8   # Mindesthöhe 50 Pixel

# HSV-Farbwerte für HEX #CDB22C (Dunkelgelb)
lower_yellow = np.array([15, 90, 100])  # Untere Grenze für dunkelgelb (HSV)
upper_yellow = np.array([50, 255, 255])  # Obere Grenze für dunkelgelb (HSV)

# Speicher für gültige Rechtecke mit Gelb-Anteil
rectangle_counter = Counter()

print("=== Starte Pre-Processing: Suche nach stabilen Rechteck-Koordinaten ===")

# 1. DURCHGANG: STABILES RECHTECK FINDEN
frame_number = 0
while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    frame_number += 1

    if frame_number > 15000:
        break

    h, w = frame.shape[:2]

    # ROI in absolute Pixelwerte umwandeln
    x1, y1 = int(roi_x1_percent * w), int(roi_y1_percent * h)
    x2, y2 = int(roi_x2_percent * w), int(roi_y2_percent * h)

    # ROI ausschneiden
    roi = frame[y1:y2, x1:x2]

    # Kanten erkennen
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)

    # Konturen finden
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    for contour in contours:
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) == 4:
            x, y, w_rect, h_rect = cv2.boundingRect(approx)

            if w_rect >= min_rect_width and h_rect >= min_rect_height:  # Mindestgröße prüfen
                x += x1  
                y += y1

                # **Prüfen, ob Gelb im Rechteck vorhanden ist**
                detected_rect = frame[y:y + h_rect, x:x + w_rect]
                hsv = cv2.cvtColor(detected_rect, cv2.COLOR_BGR2HSV)
                mask = cv2.inRange(hsv, lower_yellow, upper_yellow)

                yellow_pixels = np.count_nonzero(mask)
                total_pixels = w_rect * h_rect
                yellow_ratio = yellow_pixels / total_pixels

                if yellow_ratio >= 0.25:  # Mindestens 5% Gelb notwendig
                    rectangle_counter[(x, y, w_rect, h_rect)] += 1

    if frame_number % 100 == 0:
        print(f"Frame {frame_number}/{frame_count} verarbeitet.")

cap.release()

# Bestes Rechteck bestimmen
if rectangle_counter:
    best_rectangle, _ = rectangle_counter.most_common(1)[0]
    x_fixed, y_fixed, w_fixed, h_fixed = best_rectangle
    print(f"\n=== Stabilstes Rechteck gefunden bei: {x_fixed, y_fixed, w_fixed, h_fixed} ===\n")
    with open("coord.txt", "w") as f:
        f.write(f"width: {w_fixed}, height: {h_fixed}")
else:
    print("\n=== Kein stabiles Rechteck mit Gelb gefunden! Beende Programm. ===")
    exit()

# 2. DURCHGANG: NEUES RECHTECK SUCHEN & GELBANALYSE
cap = cv2.VideoCapture(video_path)
low_yellow_frame_count = 0
frame_number = 0
high_yellow_found = False  # Zustand für "High-Yellow"-Frame

print("=== Starte zweite Analyse: Rechteck bestätigen und Gelb-Pixel-Zählung ===")

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    frame_number += 1
    h, w = frame.shape[:2]  

    # ROI ausschneiden
    x1, y1 = int(roi_x1_percent * w), int(roi_y1_percent * h)
    x2, y2 = int(roi_x2_percent * w), int(roi_y2_percent * h)
    roi = frame[y1:y2, x1:x2]

    # Kanten erkennen
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)

    # Konturen finden
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    found_rectangles = []

    if frame_number == frame_for_info:
        info_frame = frame
        cv2.rectangle(info_frame, (x_fixed, y_fixed), (x_fixed + w_fixed, y_fixed + h_fixed), (255, 0, 0), 2)
        cv2.rectangle(info_frame, (x1, y1), (x2, y2), (138, 43, 226), 2)
        cv2.imwrite(f"{frame_number}info.jpg", info_frame)

    for contour in contours:
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) == 4:
            x, y, w_rect, h_rect = cv2.boundingRect(approx)

            if w_rect >= min_rect_width and h_rect >= min_rect_height:  # Mindestgröße prüfen
                x += x1
                y += y1
                found_rectangles.append((x, y, w_rect, h_rect))

    # Prüfen, ob das Rechteck dem stabilen Rechteck ähnelt
    for found_rectangle in found_rectangles:
        x, y, w_rect, h_rect = found_rectangle
        deviation = abs(x - x_fixed) + abs(y - y_fixed) + abs(w_rect - w_fixed) + abs(h_rect - h_fixed)

        if deviation <= 60:  # Rechteck muss ungefähr gleich sein
            detected_rect = frame[y_fixed:y_fixed + h_fixed, x_fixed:x_fixed + w_fixed]
            hsv = cv2.cvtColor(detected_rect, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, lower_yellow, upper_yellow)

            yellow_pixels = np.count_nonzero(mask)
            total_pixels = w_fixed * h_fixed
            yellow_ratio = yellow_pixels / total_pixels

            if yellow_ratio > 0.08:
                high_yellow_found = True  # Reset, sobald ein High-Yellow-Frame erkannt wird
                # cv2.rectangle(frame, (x, y), (x + w_rect, y + h_rect), (0, 255, 0), 3)
                # cv2.imwrite(f"./output/{frame_number}-f.jpg", frame)

            if yellow_ratio < 0.02 and high_yellow_found:
                low_yellow_frame_count += 1
                high_yellow_found = False  # Sperre bis ein High-Yellow-Frame kommt
                cv2.rectangle(frame, (x_fixed, y_fixed), (x_fixed + w_fixed, y_fixed + h_fixed), (255, 0, 0), 2)
                cv2.rectangle(frame, (x, y), (x + w_rect, y + h_rect), (0, 255, 0), 2)
                cv2.rectangle(frame, (x1, y1), (x2, y2), (138, 43, 226), 2)
                cv2.imwrite(f"./output/{frame_number}.jpg", frame)

    print(f"Frame {frame_number}/{frame_count} analysiert.")

cap.release()

print(f"\nAnzahl der Frames mit weniger als 5% dunkelgelben Pixeln: {low_yellow_frame_count}")
