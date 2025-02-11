import cv2
import numpy as np
from collections import Counter
import os
import asyncio
import random

def generate_distinct_colors(n):
    if n == 0:
        return []  # Falls keine Rechtecke vorhanden sind, leere Liste zurückgeben
    colors = []
    for i in range(n):
        hue = int(180 * (i / n))  # Gleichmäßige Verteilung über den Farbkreis
        saturation = 200  # Hohe Sättigung für kräftige Farben
        value = 255  # Maximale Helligkeit
        color = np.uint8([[[hue, saturation, value]]])  # HSV Farbe
        color = cv2.cvtColor(color, cv2.COLOR_HSV2BGR)[0][0]  # Umwandlung nach BGR für OpenCV
        colors.append((int(color[0]), int(color[1]), int(color[2])))  # In Tupel konvertieren
    return colors

class VideoAnalyzer:
    def __init__(self, video_path, output_dir="./output/", debug=False):
        self.video_path = video_path
        self.output_dir = output_dir
        self.output_dir_debug = "./debug/"
        self.debug = debug
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.output_dir_debug, exist_ok=True)
        
        self.cap = cv2.VideoCapture(self.video_path)
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        print(self.frame_width)
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = int(self.cap.get(cv2.CAP_PROP_FPS))
        
        self.roi_x1_percent, self.roi_y1_percent = 0.400, 0.82  # links,  oben
        self.roi_x2_percent, self.roi_y2_percent = 0.560, 0.97  # rechts, unten
        
        self.min_rect_width = 80  
        self.min_rect_height = 2   

        self.max_rect_height = 20

        self.min_x_threshold = self.frame_width * 0.5 - (self.frame_width * 0.05)  # 5% links von der Mitte
        self.max_x_threshold = self.frame_width * 0.5 - (self.frame_width * 0.5 * 0.177) # 17.65% maximal entfernt von der Mitte
        
        self.lower_yellow = np.array([15, 88, 92])  
        self.upper_yellow = np.array([45, 221, 210])  
        
        self.rectangle_counter = Counter()
        self.saved_timestamps = []

        self.yellow_hex_colors = set()
        
    async def find_stable_rectangle(self, training_frame_count: int, skip_first_frames_count: int):
        frame_number = 0

        while self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                break

            frame_number += 1
            if frame_number > training_frame_count + skip_first_frames_count:
                break

            if frame_number < skip_first_frames_count:
                continue

            x1, y1, x2, y2 = self._calculate_roi(frame)
            roi = frame[y1:y2, x1:x2]
            contours = await asyncio.to_thread(self._find_contours, roi)
            
            for contour in contours:
                x, y, w, h = self._validate_rectangle(contour, x1, y1)
                if x is not None and x < self.min_x_threshold and x > self.max_x_threshold:  # Überprüfung, ob das Rechteck links von der Mitte liegt
                    detected_rect = frame[y:y+h, x:x+w]
                    yellow_ratio = self._calculate_yellow_ratio(detected_rect, w, h)
                    if yellow_ratio >= 0.45:
                        self.rectangle_counter[(x, y, w, h)] += 1
                        if self.debug:
                            self._add_to_yellow_hex_list(detected_rect, w, h)

        self.cap.release()
        return self._get_best_rectangle()

    async def analyze_video(self, stable_rectangle, on_progress = None):
        if not stable_rectangle:
            return []
        
        x_fixed, y_fixed, w_fixed, h_fixed = stable_rectangle
        cap = cv2.VideoCapture(self.video_path)
        frame_number = 0
        low_yellow_frame_count = 0
        high_yellow_found = False
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            if on_progress and frame_number % 1000 == 0:
                await on_progress(frame_number, self.frame_count)

            frame_number += 1
            x1, y1, x2, y2 = self._calculate_roi(frame)
            roi = frame[y1:y2, x1:x2]
            contours = await asyncio.to_thread(self._find_contours, roi)
            
            for contour in contours:
                x, y, w, h = self._validate_rectangle(contour, x1, y1)
                if x is not None and x < self.min_x_threshold and x > self.max_x_threshold:  # Überprüfung, ob das Rechteck links von der Mitte liegt
                    deviation = abs(x - x_fixed) + abs(y - y_fixed) + abs(w - w_fixed) + abs(h - h_fixed)
                    if deviation <= 60:
                        stable_rect = frame[y_fixed:y_fixed + h_fixed, x_fixed:x_fixed + w_fixed]
                        yellow_ratio = self._calculate_yellow_ratio(stable_rect, w_fixed, h_fixed)
                        if yellow_ratio > 0.08:
                            high_yellow_found = True
                        if yellow_ratio < 0.01 and high_yellow_found:
                            low_yellow_frame_count += 1
                            high_yellow_found = False

                            timestamp = frame_number / self.fps
                            minutes = int(timestamp // 60)
                            seconds = int(timestamp % 60)
                            formatted_timestamp = f"{minutes:02}:{seconds:02}"
                            self.saved_timestamps.append(formatted_timestamp)

                            if self.debug:
                                copy_frame = frame.copy()
                                cv2.rectangle(copy_frame, (x_fixed, y_fixed), (x_fixed + w_fixed, y_fixed + h_fixed), (255, 0, 0), 2)
                                cv2.rectangle(copy_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                                cv2.rectangle(copy_frame, (x1, y1), (x2, y2), (138, 43, 226), 2)
                                cv2.imwrite(f"{self.output_dir}/{frame_number}.jpg", copy_frame)
                                self._save_debug_frame(frame)
            
        cap.release()
        print(f"Anzahl der Frames mit weniger als 5% Gelb: {low_yellow_frame_count}")
        return self.saved_timestamps

    def _calculate_roi(self, frame):
        h, w = frame.shape[:2]
        x1, y1 = int(self.roi_x1_percent * w), int(self.roi_y1_percent * h)
        x2, y2 = int(self.roi_x2_percent * w), int(self.roi_y2_percent * h)
        return x1, y1, x2, y2

    def _find_contours(self, roi):
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return contours

    def _validate_rectangle(self, contour, x1, y1):
        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) == 4:
            x, y, w, h = cv2.boundingRect(approx)
            if w >= self.min_rect_width and h >= self.min_rect_height and h <= self.max_rect_height:
                return x + x1, y + y1, w, h
        return None, None, None, None

    def _calculate_yellow_ratio(self, detected_rect, w, h):
        hsv = cv2.cvtColor(detected_rect, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.lower_yellow, self.upper_yellow)
        yellow_pixels = np.count_nonzero(mask)
        return yellow_pixels / (w * h)
        
    def _add_to_yellow_hex_list(self, detected_rect, w, h):
        """
        Berechnet das Verhältnis der Gelb-Pixel und speichert nur gültige Gelbtöne als Hex-Werte.
        """
        # Konvertiere in HSV
        hsv = cv2.cvtColor(detected_rect, cv2.COLOR_BGR2HSV)
        
        # Erstelle eine Maske für Gelb
        mask = cv2.inRange(hsv, self.lower_yellow, self.upper_yellow)
        
        # Anzahl der gelben Pixel berechnen
        yellow_pixels = np.count_nonzero(mask)
        
        # Berechne das Gelb-Verhältnis
        yellow_ratio = yellow_pixels / (w * h) if w * h > 0 else 0

        if yellow_ratio < 0.80:
            return

        # Falls keine Gelb-Pixel gefunden wurden, direkt zurückkehren
        if yellow_pixels == 0:
            return

        # Extrahiere nur die Gelb-Pixel in HSV
        yellow_hsv_pixels = hsv[mask > 0]

        # Prüfen, ob yellow_hsv_pixels tatsächlich Werte enthält
        if yellow_hsv_pixels.size == 0:
            return  # Falls leer, direkt zurückkehren

        yellow_bgr_pixels = detected_rect[mask > 0]

        yellow_rgb_pixels = [(r, g, b) for b, g, r in yellow_bgr_pixels]

        # Konvertiere RGB zu Hex (jetzt richtig!)
        new_hex_colors = {"#{:02x}{:02x}{:02x}".format(r, g, b) for r, g, b in yellow_rgb_pixels}

        # Nur neue Farben speichern
        self.yellow_hex_colors.update(new_hex_colors)
        return


    def _get_best_rectangle(self):
        if self.rectangle_counter:
            best_rectangle, count = self.rectangle_counter.most_common(1)[0]
            print(f"Stabilstes Rechteck gefunden: {best_rectangle} mit häufigkeit {count}")
            return best_rectangle
        return None
    
    def _save_debug_frame(self, img):
        copy_frame = img.copy()

        legend_width = 500  

        # Erweitere das Bild nach links für die Legende
        height, width, _ = copy_frame.shape
        new_width = width + legend_width
        extended_image = np.ones((height, new_width, 3), dtype=np.uint8) * 255  # Weißer Hintergrund
        extended_image[:, legend_width:] = copy_frame  # Originalbild rechts einfügen

        valid_rectangles = [(rect, count) for rect, count in self.rectangle_counter.items() if count >= 8]
        num_rectangles = len(valid_rectangles)  # Anzahl der Rechtecke

        colors = generate_distinct_colors(num_rectangles + 1)

        # Zeichne Rechtecke und erstelle die Legende
        legend_y = 30  # Startpunkt für den Legendentext
        index = 1

        for i, ((x, y, w, h), count) in enumerate(valid_rectangles):
            color = colors[i]  # Kontrastreiche Farbe zuweisen
            # Rechteck im Bild zeichnen
            text = f"{index}. {count}x"
            cv2.putText(extended_image, text, (10, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            # Kleines Farbfeld neben den Text zeichnen
            cv2.rectangle(extended_image, (150, legend_y - 10), (170, legend_y + 10), color, -1)

            # Zusätzlicher schwarzer Text mit den Koordinaten (x, y, w, h)
            coord_text = f"({x}, {y}, {w}, {h})"
            cv2.putText(extended_image, coord_text, (180, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

            cv2.rectangle(extended_image, (x + legend_width, y), (x + w + legend_width, y + h), color, 2)

            legend_y += 30  # Abstand für die nächste Zeile
            index += 1
        cv2.imwrite(f"{self.output_dir_debug}/debug.jpg", extended_image)


if __name__ == "__main__":
    video_analyzer = VideoAnalyzer("./downloads/video.mp4", debug=True)
    stable_rectangle = asyncio.run(video_analyzer.find_stable_rectangle(15000))
    timestamps = asyncio.run(video_analyzer.analyze_video(stable_rectangle))

    print(timestamps)
