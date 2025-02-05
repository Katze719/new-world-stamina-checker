import cv2
import numpy as np

# Bild laden
image = cv2.imread("bild.png")  # Dein Bild
h, w = image.shape[:2]  # Bildhöhe und -breite ermitteln

# ROI in Prozent
roi_x1_percent, roi_y1_percent = 0.425, 0.90  
roi_x2_percent, roi_y2_percent = 0.575, 0.96  

# ROI in absolute Pixelwerte umwandeln
x1, y1 = int(roi_x1_percent * w), int(roi_y1_percent * h)
x2, y2 = int(roi_x2_percent * w), int(roi_y2_percent * h)

# Bild auf den ROI beschränken
roi = image[y1:y2, x1:x2]

# Canny Edge Detection auf ROI anwenden
gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
edges = cv2.Canny(gray, 50, 150)

# Konturen finden
contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

# Zähler für Rechtecke mit wenigen dunkelgelben Pixeln
low_yellow_count = 0

# HSV-Farbwerte für HEX #CDB22C
lower_yellow = np.array([2, 120, 100])  # Untere Grenze für dunkelgelb (HSV)
upper_yellow = np.array([200, 255, 230])  # Obere Grenze für dunkelgelb (HSV)

# Bild für Maske vorbereiten
mask_full = np.zeros((h, w), dtype=np.uint8)

for contour in contours:
    approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
    if len(approx) == 4:
        x, y, w_rect, h_rect = cv2.boundingRect(approx)

        # Position korrigieren
        x += x1
        y += y1

        # Bereich ausschneiden
        detected_rect = image[y:y + h_rect, x:x + w_rect]

        # In HSV-Farbraum umwandeln
        hsv = cv2.cvtColor(detected_rect, cv2.COLOR_BGR2HSV)

        # Maske für Gelb erstellen
        mask = cv2.inRange(hsv, lower_yellow, upper_yellow)

        # Maske ins Gesamtbild einsetzen (an die richtige Stelle)
        mask_full[y:y + h_rect, x:x + w_rect] = mask

        # Anzahl der dunkelgelben Pixel berechnen
        yellow_pixels = np.count_nonzero(mask)
        total_pixels = w_rect * h_rect
        yellow_ratio = yellow_pixels / total_pixels

        # Falls weniger als 5% der Pixel dunkelgelb sind, Counter erhöhen
        if yellow_ratio < 0.05:
            low_yellow_count += 1

        # Rechteck zeichnen (grün)
        cv2.rectangle(image, (x, y), (x + w_rect, y + h_rect), (0, 255, 0), 3)

# Blaues Rechteck für den ursprünglichen ROI zeichnen
cv2.rectangle(image, (x1, y1), (x2, y2), (255, 0, 0), 2)  

# Maske speichern (zeigt, welche Pixel als Gelb erkannt wurden)
cv2.imwrite("gelb_maske.jpg", mask_full)

# Bild speichern mit eingezeichneten Rechtecken
cv2.imwrite("erkanntes_rechteck_mit_gelbprüfung.jpg", image)

# Gefiltertes Bild erzeugen (zeigt nur Gelbtöne)
yellow_only = cv2.bitwise_and(image, image, mask=mask_full)
cv2.imwrite("gelb_gefunden.jpg", yellow_only)

print(f"Anzahl der Rechtecke mit weniger als 5% dunkelgelben Pixeln: {low_yellow_count}")
