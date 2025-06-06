import cv2
import numpy as np
from collections import Counter
import os
import asyncio
import random
import time
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import math

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

# Define a worker function that will run in a separate process
def process_frame_chunk(chunk_data):
    video_path, start_frame, end_frame, rectangle_coords, lower_yellow, upper_yellow, secondary_lower_yellow, secondary_upper_yellow, debug, debug_interval, output_dir_debug, output_dir = chunk_data
    
    x, y, w, h = rectangle_coords
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    # Status tracking variables
    stamina_events = []
    current_frame = start_frame
    stamina_empty = False
    yellow_ratios = []
    yellow_pixels_history = []
    buffer_size = 5
    empty_buffer = [False] * buffer_size
    pattern_history = []
    pattern_length = 10
    rising_pattern = [False] * 3
    falling_pattern = [False] * 3
    
    # Calibration variables
    max_observed_ratio = 0
    min_observed_ratio = 1.0
    max_observed_pixels = 0
    calibration_frames = min(300, (end_frame - start_frame) // 4)  # Adjust calibration for chunk size
    high_threshold = 0.35
    low_threshold = 0.08
    
    # Process frames in this chunk
    while current_frame < end_frame and cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        try:
            # Extract stamina region
            stamina_region = frame[y:y+h, x:x+w]
            
            # Convert to HSV and create masks
            hsv = cv2.cvtColor(stamina_region, cv2.COLOR_BGR2HSV)
            primary_mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
            secondary_mask = cv2.inRange(hsv, secondary_lower_yellow, secondary_upper_yellow)
            combined_mask = cv2.bitwise_or(primary_mask, secondary_mask)
            
            # Enhanced preprocessing
            kernel = np.ones((3, 3), np.uint8)
            combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, kernel)
            combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)
            
            # Calculate yellow pixel ratio
            yellow_pixels = np.count_nonzero(combined_mask)
            total_pixels = w * h
            current_ratio = yellow_pixels / total_pixels if total_pixels > 0 else 0
            
            # Store pixel history
            yellow_pixels_history.append(yellow_pixels)
            if len(yellow_pixels_history) > pattern_length:
                yellow_pixels_history.pop(0)
            
            # Calibration during initial frames
            if current_frame - start_frame <= calibration_frames:
                max_observed_ratio = max(max_observed_ratio, current_ratio)
                min_observed_ratio = min(min_observed_ratio, current_ratio)
                max_observed_pixels = max(max_observed_pixels, yellow_pixels)
                
                # Adjust thresholds after calibration
                if current_frame - start_frame == calibration_frames and max_observed_ratio > 0.05:
                    high_threshold = max(0.15, min(0.5, max_observed_ratio * 0.6))
                    low_threshold = max(0.05, min(0.15, max_observed_ratio * 0.12))
            
            # Update buffers
            yellow_ratios.append(current_ratio)
            if len(yellow_ratios) > 10:
                yellow_ratios.pop(0)
            
            avg_ratio = sum(yellow_ratios) / len(yellow_ratios)
            
            # Detect trends
            if len(yellow_ratios) >= 3:
                rising_pattern.append(yellow_ratios[-1] > yellow_ratios[-2] > yellow_ratios[-3])
                falling_pattern.append(yellow_ratios[-1] < yellow_ratios[-2] < yellow_ratios[-3])
                rising_pattern.pop(0)
                falling_pattern.pop(0)
            
            # Pattern detection
            if len(yellow_pixels_history) >= pattern_length:
                consistently_low = all(p < max_observed_pixels * 0.15 for p in yellow_pixels_history[-3:])
                sudden_drop = (yellow_pixels_history[-1] < max_observed_pixels * 0.2 and
                              yellow_pixels_history[-4] > max_observed_pixels * 0.5)
                
                pattern_empty = consistently_low or sudden_drop
                pattern_history.append(pattern_empty)
                if len(pattern_history) > buffer_size:
                    pattern_history.pop(0)
            
            # Debug output
            if debug and current_frame % debug_interval == 0:
                debug_output = frame.copy()
                rect_color = (0, 0, 255) if avg_ratio < low_threshold else (0, 255, 0)
                cv2.rectangle(debug_output, (x, y), (x + w, y + h), rect_color, 2)
                
                info_text = f"Frame {current_frame}: Gelb-Ratio = {avg_ratio:.3f}, Pixel = {yellow_pixels}"
                threshold_text = f"Schwellen: H={high_threshold:.2f}, L={low_threshold:.2f}, Max={max_observed_ratio:.2f}"
                pattern_text = f"Pattern: {'Leer' if sum(pattern_history) > len(pattern_history)//2 else 'Gefüllt'}"
                
                cv2.putText(debug_output, info_text, (x, y - 50), 
                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, rect_color, 2)
                cv2.putText(debug_output, threshold_text, (x, y - 30), 
                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, rect_color, 2)
                cv2.putText(debug_output, pattern_text, (x, y - 10), 
                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, rect_color, 2)
                
                mask_overlay = np.zeros_like(stamina_region)
                mask_overlay[combined_mask > 0] = [0, 255, 255]
                debug_region = cv2.addWeighted(stamina_region, 0.7, mask_overlay, 0.3, 0)
                debug_output[y:y+h, x:x+w] = debug_region
                
                # Save debug image
                cv2.imwrite(f"{output_dir_debug}/frame_{current_frame}.jpg", debug_output)
            
            # Multi-criteria stamina detection
            ratio_empty = avg_ratio < low_threshold
            pattern_empty = len(pattern_history) > 0 and sum(pattern_history) > len(pattern_history)//2
            trend_empty = all(falling_pattern) and avg_ratio < high_threshold * 0.4
            
            is_empty = ratio_empty or (pattern_empty and avg_ratio < high_threshold * 0.3) or trend_empty
            
            empty_buffer.append(is_empty)
            empty_buffer.pop(0)
            buffer_empty = sum(empty_buffer) > len(empty_buffer) // 2
            
            # Detect state changes
            if buffer_empty and not stamina_empty:
                stamina_empty = True
                # Save frame number and empty state instead of formatted time
                stamina_events.append((current_frame, True))
                
                # Save the image of the moment when stamina becomes empty
                if debug:
                    stamina_lost_frame = frame.copy()
                    cv2.rectangle(stamina_lost_frame, (x, y), (x + w, y + h), (0, 0, 255), 2)  # Red = empty
                    cv2.putText(stamina_lost_frame, f"Out of Stamina @ Frame {current_frame} (Ratio: {avg_ratio:.2f})", 
                              (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    cv2.imwrite(f"{output_dir}/stamina_lost_{current_frame}.jpg", stamina_lost_frame)
                
            elif not buffer_empty and stamina_empty:
                stamina_empty = False
                # Save frame number and recovery state
                stamina_events.append((current_frame, False))
                
                # Save the image of the moment when stamina recovers
                if debug:
                    recovery_frame = frame.copy()
                    cv2.rectangle(recovery_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)  # Green = filled
                    cv2.putText(recovery_frame, f"Stamina Recovery @ Frame {current_frame} (Ratio: {avg_ratio:.2f})", 
                              (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                    # cv2.imwrite(f"{output_dir}/stamina_recovery_{current_frame}.jpg", recovery_frame)
            
            current_frame += 1
            
        except Exception as e:
            print(f"Error processing frame {current_frame}: {str(e)}")
            current_frame += 1
    
    cap.release()
    return stamina_events

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
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = int(self.cap.get(cv2.CAP_PROP_FPS))
        self.duration = self.frame_count / self.fps if self.fps > 0 else 0  # Video duration in seconds
        
        # New World spezifische ROI für die Stamina-Leiste
        # Typischerweise ist die Stamina-Leiste im unteren mittleren Bereich des Bildschirms
        # Diese Werte sind spezifisch auf New World UI abgestimmt
        self.roi_x1_percent, self.roi_y1_percent = 0.35, 0.82  # links, oben - erweitert für UI-Varianten
        self.roi_x2_percent, self.roi_y2_percent = 0.65, 0.95  # rechts, unten - erweitert für UI-Varianten
        
        # Parameter für die Rechteckerkennung, angepasst an die typischen Abmessungen der New World Stamina-Leiste
        # Die Stamina-Leiste ist ein dünnes, horizontales Rechteck
        self.min_rect_width = self.frame_width * 0.15  # Mindestens 15% der Bildbreite
        self.max_rect_width = self.frame_width * 0.35  # Maximal 35% der Bildbreite
        self.min_rect_height = 3  # Mindesthöhe in Pixeln
        self.max_rect_height = 15  # Maximalhöhe in Pixeln
        
        # Typische Positionen der Stamina-Leiste
        self.target_y_position = self.frame_height * 0.88  # Typische vertikale Position der Stamina-Leiste bei 88% der Bildhöhe
        self.y_position_tolerance = self.frame_height * 0.06  # Toleranz für vertikale Position (6% der Bildhöhe)
        
        # Horizontale Position - mittig im Bildschirm
        self.target_x_center = self.frame_width * 0.5  # Zentrum des Bildschirms
        self.x_center_tolerance = self.frame_width * 0.15  # Toleranz für horizontale Position (15% der Bildbreite)
        
        # Anfängliche HSV-Bereiche für Gelb - auf New World Stamina-Gelb abgestimmt
        self.lower_yellow = np.array([15, 70, 80])  
        self.upper_yellow = np.array([60, 255, 255])  
        
        self.rectangle_counter = Counter()
        self.saved_timestamps = []
        self.yellow_hex_colors = set()
        
        # Für adaptive Erkennung
        self.color_samples = []
        self.stamina_positions = []
        
    async def _process_frame_for_samples(self, frame, x1, y1, x2, y2):
        """Verarbeitet einen Frame für die Farbprobensammlung - asynchron"""
        # Diese rechenintensive Funktion in einen separaten Thread auslagern
        def process_frame():
            roi = frame[y1:y2, x1:x2]
            hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv_roi, self.lower_yellow, self.upper_yellow)
            yellow_pixels = []
            
            if np.count_nonzero(mask) > 50:  # Wenn genügend gelbe Pixel vorhanden sind
                # Extrahiere die gelben Pixel
                yellow_pixels = hsv_roi[mask > 0].tolist()
            
            return yellow_pixels
        
        # Führe rechenintensive Operationen in einem Thread aus
        return await asyncio.to_thread(process_frame)
        
    async def _collect_color_samples(self, frame_count, skip_frames=0):
        """Sammelt Farbproben aus dem Video, um die HSV-Bereiche anzupassen"""
        cap = cv2.VideoCapture(self.video_path)
        samples = []
        frames_to_sample = 20  # Anzahl der Frames für Proben
        
        # Berechne Schrittweite, um Proben über das gesamte Video zu verteilen
        step = max(1, (frame_count - skip_frames) // frames_to_sample)
        
        for i in range(skip_frames + 1, frame_count, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if not ret:
                break
                
            # ROI extrahieren
            x1, y1, x2, y2 = self._calculate_roi(frame)
            
            # Verarbeite Frame asynchron
            yellow_pixels = await self._process_frame_for_samples(frame, x1, y1, x2, y2)
            samples.extend(yellow_pixels)
            
            # Erlaube dem Event Loop andere Tasks zu verarbeiten
            await asyncio.sleep(0)
        
        cap.release()
        return samples
        
    def _calculate_hsv_range(self, samples):
        """
        Berechnet optimalen HSV-Bereich aus Farbproben mit spezieller Anpassung für
        die gelbe Stamina-Leiste in New World unter verschiedenen Beleuchtungsbedingungen.
        """
        if not samples or len(samples) < 50:
            # Fallback auf vorkonfigurierte Werte für New World Stamina-Gelb
            # Breiterer Bereich, der verschiedene Beleuchtungsbedingungen abdeckt
            return np.array([20, 70, 80]), np.array([60, 255, 255])
            
        samples = np.array(samples)
        
        # Berechne Mittelwerte und Standardabweichungen
        mean = np.mean(samples, axis=0)
        std = np.std(samples, axis=0)
        
        if self.debug:
            print(f"Farbsamples: {len(samples)} Proben")
            print(f"HSV Mittelwert: {mean}")
            print(f"HSV Standardabweichung: {std}")
        
        # Identifiziere und entferne Ausreißer für stabilere Ergebnisse
        filtered_samples = []
        for sample in samples:
            h, s, v = sample
            # Ausreißerfilter: Entferne Samples, die mehr als 2 Std.-Abw. vom Mittelwert entfernt sind
            if (abs(h - mean[0]) < 2.5 * std[0] and 
                abs(s - mean[1]) < 2.5 * std[1] and 
                abs(v - mean[2]) < 2.5 * std[2]):
                filtered_samples.append(sample)
        
        # Wenn zu viele Ausreißer entfernt wurden, verwende die Originalproben
        if len(filtered_samples) < len(samples) * 0.6:
            filtered_samples = samples
        else:
            samples = np.array(filtered_samples)
            # Berechne neue Mittelwerte und Standardabweichungen nach der Filterung
            mean = np.mean(samples, axis=0)
            std = np.std(samples, axis=0)
            
            if self.debug:
                print(f"Nach Ausreißerfilterung: {len(filtered_samples)} Proben")
                print(f"Gefilterte HSV Mittelwerte: {mean}")
        
        # Berechne die Anzahl der einzigartigen Farbwerte in jeder Dimension
        unique_h = len(np.unique(samples[:, 0]))
        unique_s = len(np.unique(samples[:, 1]))
        unique_v = len(np.unique(samples[:, 2]))
        
        if self.debug:
            print(f"Einzigartige Werte - H: {unique_h}, S: {unique_s}, V: {unique_v}")
        
        # Adaptive Strategie basierend auf der Variabilität der Proben
        # Stamina-Leiste in New World hat eine relativ konsistente Farbe, aber
        # variiert je nach Beleuchtung des Spiels und Video-Komprimierung
        
        # 1. Ermittle, ob die Proben konsistent sind
        is_consistent_h = std[0] < 8.0  # Farbton hat geringe Varianz
        is_consistent_s = std[1] < 40.0  # Sättigung kann mehr variieren
        is_consistent_v = std[2] < 50.0  # Helligkeit kann stark variieren
        
        if is_consistent_h and is_consistent_s:
            # Bei konsistenten Proben: Enger Bereich um den Mittelwert
            h_range = [
                max(0, mean[0] - 1.8 * std[0]),
                min(180, mean[0] + 1.8 * std[0])
            ]
            s_range = [
                max(0, mean[1] - 1.8 * std[1]),
                min(255, mean[1] + 1.8 * std[1])
            ]
            v_range = [
                max(0, mean[2] - 2.5 * std[2]),
                min(255, mean[2] + 2.5 * std[2])
            ]
        else:
            # Bei inkonsistenten Proben: Breiterer Bereich mit New World spezifischen Anpassungen
            h_range = [
                max(0, mean[0] - 2.5 * std[0]),
                min(180, mean[0] + 2.5 * std[0])
            ]
            s_range = [
                max(0, mean[1] - 2.2 * std[1]),
                min(255, mean[1] + 2.2 * std[1])
            ]
            v_range = [
                max(0, mean[2] - 3.0 * std[2]),
                min(255, mean[2] + 3.0 * std[2])
            ]
        
        # Garantiere Mindestbreite für die Bereiche, um Erkennungsprobleme zu vermeiden
        # H: Typisches New World Stamina-Gelb liegt im Bereich 20-60
        if h_range[1] - h_range[0] < 10:
            h_center = (h_range[0] + h_range[1]) / 2
            h_range[0] = max(0, h_center - 5)
            h_range[1] = min(180, h_center + 5)
        
        # Spezifische Anpassungen für New World Stamina-Leiste
        # Untere Grenzen für gelbe Töne nicht zu hoch ansetzen, da die Leiste
        # gegen Ende durchsichtiger/blasser wird
        
        # Sättigung: In dunkleren Umgebungen ist die Leiste weniger gesättigt
        s_range[0] = min(s_range[0], 60)
        
        # Helligkeit: Muss hell genug sein, um nicht mit dem Hintergrund zu verschmelzen
        v_range[0] = min(v_range[0], 70)
        
        # Ergebnisbereiche
        lower_yellow = np.array([h_range[0], s_range[0], v_range[0]])
        upper_yellow = np.array([h_range[1], s_range[1], v_range[1]])
        
        # New World spezifische Nachkorrektur: Stelle sicher, dass der gelbe Farbton abgedeckt ist
        # Falls die Proben nicht repräsentativ sind, führe eine Mindestbereichskorrektur durch
        if lower_yellow[0] > 25:
            lower_yellow[0] = 20  # Erweitere den unteren H-Wert für dunklere Gelbtöne
        if upper_yellow[0] < 50:
            upper_yellow[0] = 60  # Erweitere den oberen H-Wert für hellere Gelbtöne
        
        if self.debug:
            print(f"Berechneter HSV-Bereich für Gelb: {lower_yellow} bis {upper_yellow}")
            
        return lower_yellow.astype(np.uint8), upper_yellow.astype(np.uint8)

    async def _process_frame(self, frame, x1, y1, x2, y2):
        """Verarbeitet einen Frame für die Stamina-Erkennung - asynchron"""
        # Diese rechenintensive Funktion in einen separaten Thread auslagern
        def process():
            roi = frame[y1:y2, x1:x2]
            
            # In verschiedene Farbräume konvertieren für robustere Erkennung
            hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            
            # Primäre Maske mit unseren HSV-Grenzen
            yellow_mask = cv2.inRange(hsv_roi, self.lower_yellow, self.upper_yellow)
            
            # Verbesserte Vorverarbeitung
            kernel = np.ones((3,3), np.uint8)
            yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_OPEN, kernel)
            yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_CLOSE, kernel)
            
            # Rauschunterdrückung
            yellow_mask = cv2.medianBlur(yellow_mask, 5)
            
            # Kontur-basierte Erkennung
            contours, _ = cv2.findContours(yellow_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            stamina_candidates = []
            
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < 50:  # Ignoriere zu kleine Konturen
                    continue
                    
                # Bounding box der Kontur
                x, y, w, h = cv2.boundingRect(contour)
                
                # Anpassung der Koordinaten zum Originalbild
                x_global, y_global = x + x1, y + y1
                
                # Prüfe Rechteckform (sollte längliches Rechteck sein)
                aspect_ratio = w / h if h > 0 else 0
                
                # Erweitere die Kriterien für mögliche Stamina-Leisten
                if (w >= self.min_rect_width and 
                    h >= self.min_rect_height and 
                    h <= self.max_rect_height and
                    aspect_ratio > 4):  # Etwas flexibler mit dem Seitenverhältnis
                    
                    # Prüfe horizontale Position (sollte ungefähr mittig sein)
                    center_x = x_global + w/2
                    center_y = y_global + h/2
                    
                    # Erweitere den akzeptierten Bereich
                    if 0.3 * self.frame_width <= center_x <= 0.7 * self.frame_width:
                        stamina_candidates.append((x_global, y_global, w, h, area, aspect_ratio))
            
            return stamina_candidates
        
        # Führe rechenintensive Operationen in einem Thread aus
        return await asyncio.to_thread(process)

    async def find_stable_rectangle(self, training_frame_count: int, skip_first_frames_count: int):
        """
        Findet die stabile Position der Stamina-Leiste durch Analyse mehrerer Frames.
        Die Funktion sucht nach einem gelben, länglichen Rechteck, das stabil im unteren Bildschirmbereich verbleibt.
        """
        # Dynamische Farbanpassung durch Sammeln von Farbproben
        samples = await self._collect_color_samples(training_frame_count, skip_first_frames_count)
        if samples:
            # Optimiere den HSV-Farbbereich basierend auf den gesammelten Farbproben
            self.lower_yellow, self.upper_yellow = self._calculate_hsv_range(samples)
            
        # Beginne die Suche nach dem stabilen Rechteck
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, skip_first_frames_count)
        frame_number = skip_first_frames_count
        
        # Fallback-Rechteck für New World Stamina-Leiste - positioniert im typischen UI-Bereich
        # Dies wird verwendet, wenn keine guten Erkennungsergebnisse vorliegen
        stamina_region_y = int(self.target_y_position)
        stamina_region_height = 8  # Typische Höhe der New World Stamina-Leiste
        stamina_region_width = int(self.frame_width * 0.25)  # Typische Breite (25% der Bildschirm-Breite)
        stamina_region_x = int(self.frame_width * 0.5 - stamina_region_width / 2)  # Zentriert
        
        fallback_rect = (stamina_region_x, stamina_region_y, stamina_region_width, stamina_region_height)
        
        if self.debug:
            print(f"Fallback-Rechteck bei: {fallback_rect}")
            print(f"Trainiere mit {training_frame_count} Frames")
            print(f"Calibrierter HSV-Bereich: {self.lower_yellow} bis {self.upper_yellow}")

        # Tracking für Verarbeitungsfortschritt
        last_progress_time = time.time()
        progress_interval = 2  # Sekunden zwischen Fortschrittsupdates
        
        # Merkmale für die New World Stamina-Leiste:
        # - Konstante Position im unteren mittleren Bildbereich
        # - Gelbe Farbe (HSV-Bereich 20-60, 70-255, 80-255)
        # - Rechteckiges, längliches Format mit Seitenverhältnis >5:1
        # - Erwartete Höhe zwischen 5-15 Pixeln
        # - Erwartete Breite ca. 15-35% der Bildschirmbreite
        
        # Sammle Kandidaten für die stärkere Gewichtung von Positionen
        position_weighted_counter = Counter()  # Zählt die Y-Position (Höhe) der erkannten Rechtecke
        
        # Score-basierter Ansatz für die Bewertung der Rechteck-Kandidaten
        rectangle_scores = {}  # {(x,y,w,h): score}
        
        # Wichtig: Wir wollen eine stabile Stamina-Leiste innerhalb des ROI finden, 
        # nicht den ROI selbst verwenden
        
        while self.cap.isOpened() and frame_number < (training_frame_count + skip_first_frames_count):
            ret, frame = self.cap.read()
            if not ret:
                break

            frame_number += 1
            
            # ROI auf den unteren mittleren Bereich des Bildschirms beschränken
            x1, y1, x2, y2 = self._calculate_roi(frame)
            
            # Debug-Ausgabe der ROI für jeden 200. Frame
            if self.debug and frame_number % 200 == 0:
                debug_frame = frame.copy()
                cv2.rectangle(debug_frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
                cv2.putText(debug_frame, "ROI für Stamina-Suche", (x1, y1 - 10), 
                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                cv2.imwrite(f"{self.output_dir_debug}/roi_frame_{frame_number}.jpg", debug_frame)
            
            # Asynchrone Frame-Verarbeitung
            candidates = await self._process_stamina_frame_for_detection(frame, x1, y1, x2, y2)
            
            # WICHTIGER FIX: Bewerte nur die inneren Rechtecke, nicht den kompletten ROI
            # Multi-Methoden-Scoring für robustere Erkennung
            if not candidates and frame_number % 300 == 0 and self.debug:
                # Wenn keine Kandidaten gefunden wurden, speichere ein Debug-Bild
                debug_frame = frame.copy()
                cv2.rectangle(debug_frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                cv2.putText(debug_frame, "Keine Kandidaten gefunden", (x1, y1 - 10), 
                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
                cv2.imwrite(f"{self.output_dir_debug}/no_candidates_{frame_number}.jpg", debug_frame)
                
            for rect_info in candidates:
                x_global, y_global, w, h, area, aspect_ratio, yellow_ratio = rect_info
                
                # Die Stamina-Leiste muss INNERHALB des ROI sein und KLEINER als der ROI
                # Prüfen, ob das Rechteck viel kleiner als der ROI ist
                roi_area = (x2 - x1) * (y2 - y1)
                rect_area = w * h
                
                # Wenn das Rechteck zu groß ist (mehr als 50% des ROI), ignoriere es
                if rect_area > roi_area * 0.5:
                    continue
                
                # 1. POSITION - Bewertet die Übereinstimmung mit der erwarteten vertikalen Position
                # New World Stamina-Leiste befindet sich typischerweise bei ca. 85-90% der Bildhöhe
                vertical_position_score = 1.0 - (abs(y_global + h/2 - self.target_y_position) / self.frame_height)
                vertical_position_score = max(0, min(1, vertical_position_score * 2))  # Steile Bewertungskurve
                
                # 2. HORIZONTAL - Bewertet die horizontale Zentrierung
                # Stamina-Leiste ist typischerweise horizontal zentriert
                horizontal_center_score = 1.0 - (abs(x_global + w/2 - self.frame_width/2) / (self.frame_width/2))
                horizontal_center_score = max(0, min(1, horizontal_center_score * 1.5))  # Erhöhte Gewichtung
                
                # 3. FORM - Bewertet das Seitenverhältnis
                # Stamina-Leiste ist typischerweise 8-15x breiter als hoch
                aspect_ratio_score = 0
                if aspect_ratio >= 5 and aspect_ratio <= 30:  # Gültiger Bereich
                    if aspect_ratio <= 20:
                        aspect_ratio_score = aspect_ratio / 15  # Linear ansteigend bis 15:1
                    else:
                        aspect_ratio_score = 1.0 - (aspect_ratio - 15) / 15  # Linear abfallend über 15:1
                
                # 4. DIMENSIONEN - Bewertet die Größe
                # Typische Dimensionen der Stamina-Leiste
                width_score = 0
                height_score = 0
                
                # Höhe: Typischerweise 5-12 Pixel - STRIKTER FÜR KORREKTE ERKENNUNG
                if 5 <= h <= 10:
                    height_score = 1.0  # Perfekte Höhe
                elif 3 <= h < 5 or 10 < h <= 12:
                    height_score = 0.7  # Akzeptable Höhe
                else:
                    height_score = 0.3 * max(0, 1 - (abs(h - 7.5) / 7.5))  # Abnehmende Bewertung
                
                # Breite: Typischerweise 15-25% der Bildschirmbreite
                width_ratio = w / self.frame_width
                if 0.15 <= width_ratio <= 0.25:
                    width_score = 1.0  # Perfekte Breite
                elif 0.1 <= width_ratio < 0.15 or 0.25 < width_ratio <= 0.35:
                    width_score = 0.7  # Akzeptable Breite
                else:
                    width_score = 0.3 * max(0, 1 - (abs(width_ratio - 0.2) / 0.2))  # Abnehmende Bewertung
                
                size_score = (height_score + width_score) / 2
                
                # 5. FARBE - Bewertet den Gelbanteil - KRITISCH FÜR KORREKTE ERKENNUNG
                # Stamina-Leiste sollte einen hohen Anteil an gelben Pixeln haben
                color_score = min(1.0, yellow_ratio * 2)  # Linear bis 50%, dann bei 1.0 gedeckelt
                
                # Bonus für sehr hohen Gelbanteil (typisch für die eigentliche Stamina-Leiste)
                if yellow_ratio > 0.6:
                    color_score += 0.5
                
                # 6. KONSISTENZ - Bewertet die temporale Stabilität
                # Dieser Teil wird durch die Counter-Mechanik implementiert
                
                # Gewichteter Gesamt-Score für diesen Kandidaten
                # VERSTÄRKTE GEWICHTUNG FÜR GRÖSSEN- UND FARBKRITERIEN
                total_score = (
                    vertical_position_score * 2.0 +    # Position ist wichtig
                    horizontal_center_score * 2.0 +    # Zentrierung ist wichtig
                    aspect_ratio_score * 2.0 +         # Form ist wichtig
                    size_score * 3.0 +                 # Dimensionen sind SEHR wichtig (erhöht)
                    color_score * 3.0                  # Farbe ist SEHR wichtig (erhöht)
                )
                
                # ZUSÄTZLICHE STRAFE FÜR ZU GROSSE RECHTECKE
                if h > 12 or width_ratio > 0.3:
                    total_score *= 0.5  # Stark reduzierter Score für zu große Rechtecke
                
                # Speichere den Score
                rect_key = (x_global, y_global, w, h)
                rectangle_scores[rect_key] = total_score
                
                # Höher bewertete Kandidaten werden mehrfach gezählt
                count_weight = max(1, int(total_score))
                for _ in range(count_weight):
                    self.rectangle_counter[rect_key] += 1
                
                # Für die Höhenerkennung
                position_weighted_counter[y_global] += count_weight
                
                # Debug-Ausgabe für Kandidaten mit hohen Scores
                if self.debug and total_score > 5.0 and frame_number % 100 == 0:
                    debug_frame = frame.copy()
                    # Zeichne ROI
                    cv2.rectangle(debug_frame, (x1, y1), (x2, y2), (255, 255, 0), 1)
                    
                    # Zeichne farbiges Rechteck basierend auf Score
                    score_pct = min(1.0, total_score / 10.0)
                    green = int(255 * score_pct)
                    red = int(255 * (1 - score_pct))
                    cv2.rectangle(debug_frame, (x_global, y_global), (x_global + w, y_global + h), (0, green, red), 2)
                    
                    # Füge detaillierte Score-Informationen hinzu
                    score_info = [
                        f"Total Score: {total_score:.1f}",
                        f"Position V/H: {vertical_position_score:.2f}/{horizontal_center_score:.2f}",
                        f"Form: {aspect_ratio_score:.2f} (Ratio {aspect_ratio:.1f})",
                        f"Größe: {size_score:.2f} ({w}x{h} Pixel)",
                        f"Farbe: {color_score:.2f} (Gelb: {yellow_ratio:.2f})"
                    ]
                    
                    for i, text in enumerate(score_info):
                        text_y = y_global - 120 + i * 20
                        cv2.putText(debug_frame, text, (x_global, text_y), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, green, red), 1)
                    
                    cv2.imwrite(f"{self.output_dir_debug}/candidate_{frame_number}_{total_score:.1f}.jpg", debug_frame)
            
            # Fortschrittsanzeige
            current_time = time.time()
            if current_time - last_progress_time > progress_interval:
                progress_pct = (frame_number - skip_first_frames_count) / training_frame_count * 100
                print(f"Training: {frame_number - skip_first_frames_count}/{training_frame_count} Frames ({progress_pct:.1f}%)")
                last_progress_time = current_time
                
            # Erlaube dem Event Loop andere Tasks zu verarbeiten
            if frame_number % 10 == 0:
                await asyncio.sleep(0)

        self.cap.release()
        
        # Kombiniere die Bewertungen für eine präzisere Erkennung
        best_rectangle = None
        best_score = -1
        
        # Identifiziere das Rechteck mit dem höchsten Gesamtscore
        for rect, count in self.rectangle_counter.most_common(20):  # Betrachte die Top 20 Kandidaten
            if rect in rectangle_scores:
                # Gewichtet Score mit Häufigkeit
                final_score = rectangle_scores[rect] * (count ** 0.5)  # Quadratwurzel der Häufigkeit als Faktor
                
                # WICHTIGER FILTER: Ignoriere zu große Rechtecke (nicht die gesamte ROI nehmen)
                x, y, w, h = rect
                if h > 15 or w > self.frame_width * 0.35:
                    # Reduziere den Score drastisch für zu große Rechtecke
                    final_score *= 0.1
                
                if final_score > best_score:
                    best_score = final_score
                    best_rectangle = rect
        
        # Fallback-Strategie, wenn kein guter Kandidat gefunden wurde
        if not best_rectangle:
            if self.debug:
                print("Kein stabiles Rechteck gefunden. Verwende Fallback-Rechteck.")
            best_rectangle = fallback_rect
        elif self.debug:
            x, y, w, h = best_rectangle
            print(f"Bestes Rechteck: ({x}, {y}, {w}, {h}) mit Gesamtscore {best_score:.2f}")
        
        # Visualisiere das beste Rechteck
        if self.debug:
            await self._save_best_rectangle_visualization(skip_first_frames_count, training_frame_count, best_rectangle, fallback_rect)
            
        return best_rectangle

    async def _process_stamina_frame_for_detection(self, frame, x1, y1, x2, y2):
        """Verarbeitet einen Frame speziell für die Erkennung der Stamina-Leiste - asynchron"""
        def process():
            roi = frame[y1:y2, x1:x2]
            
            # In verschiedene Farbräume konvertieren für robustere Erkennung
            hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            
            # Primäre Maske mit unseren HSV-Grenzen
            yellow_mask = cv2.inRange(hsv_roi, self.lower_yellow, self.upper_yellow)
            
            # Verbesserte Vorverarbeitung
            kernel = np.ones((3,3), np.uint8)
            yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_OPEN, kernel)
            yellow_mask = cv2.morphologyEx(yellow_mask, cv2.MORPH_CLOSE, kernel)
            
            # Rauschunterdrückung
            yellow_mask = cv2.medianBlur(yellow_mask, 5)
            
            # Kontur-basierte Erkennung
            contours, _ = cv2.findContours(yellow_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            stamina_candidates = []
            
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < 50:  # Ignoriere zu kleine Konturen
                    continue
                    
                # Bounding box der Kontur
                x, y, w, h = cv2.boundingRect(contour)
                
                # Anpassung der Koordinaten zum Originalbild
                x_global, y_global = x + x1, y + y1
                
                # Prüfe Rechteckform (sollte längliches Rechteck sein)
                aspect_ratio = w / h if h > 0 else 0
                
                # Berechne den gelben Anteil im erkannten Rechteck
                rect_mask = yellow_mask[y:y+h, x:x+w]
                yellow_pixels = np.count_nonzero(rect_mask)
                yellow_ratio = yellow_pixels / (w * h) if w * h > 0 else 0
                
                # Füge alle potenziellen Kandidaten hinzu, auch wenn sie nicht perfekt sind
                # Die Filterung erfolgt später anhand kombinierter Kriterien
                stamina_candidates.append((x_global, y_global, w, h, area, aspect_ratio, yellow_ratio))
            
            return stamina_candidates
        
        # Führe rechenintensive Operationen in einem Thread aus
        return await asyncio.to_thread(process)

    async def _save_best_rectangle_visualization(self, skip_first_frames_count, training_frame_count, rectangle, fallback_rect=None):
        """Visualisiert das gefundene beste Rechteck sowie das Fallback-Rechteck - asynchron"""
        sample_frame = cv2.VideoCapture(self.video_path)
        # Nimm einen Frame aus der Mitte des Videos für die Visualisierung
        frame_pos = skip_first_frames_count + training_frame_count // 2
        sample_frame.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)
        ret, frame = sample_frame.read()
        if ret:
            # Erstelle ein größeres Bild mit Debug-Informationen
            height, width = frame.shape[:2]
            debug_image = np.zeros((height + 200, width, 3), dtype=np.uint8)
            debug_image[:height, :width] = frame
            
            # Zeichne das gefundene Rechteck ein
            x, y, w, h = rectangle
            cv2.rectangle(debug_image, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(debug_image, "Erkannte Stamina-Leiste", (x, y - 10), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            
            # Zeichne ROI ein
            x1, y1, x2, y2 = self._calculate_roi(frame)
            cv2.rectangle(debug_image, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.putText(debug_image, "Suchbereich (ROI)", (x1, y1 - 10), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            
            # Wenn ein Fallback-Rechteck vorhanden ist, zeichne es ebenfalls ein
            if fallback_rect and fallback_rect != rectangle:
                fx, fy, fw, fh = fallback_rect
                cv2.rectangle(debug_image, (fx, fy), (fx + fw, fy + fh), (255, 0, 255), 2)
                cv2.putText(debug_image, "Fallback-Rechteck", (fx, fy - 10), 
                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
            
            # Füge Stamina-Bereichs-Ausschnitt hinzu
            stamina_region = frame[y:y+h, x:x+w]
            
            if stamina_region.size > 0:
                # Stelle sicher, dass die Region nicht leer ist
                # Skaliere die Region für bessere Sichtbarkeit
                scale_factor = min(5, 200 / h)  # Maximal 5x oder passend für 200 Pixel Höhe
                scaled_width = int(w * scale_factor)
                scaled_height = int(h * scale_factor)
                
                if scaled_width > 0 and scaled_height > 0:
                    stamina_region_scaled = cv2.resize(stamina_region, (scaled_width, scaled_height))
                    
                    # Platziere skalierten Ausschnitt unten im Bild
                    y_pos = height + 20
                    if y_pos + scaled_height <= debug_image.shape[0] and scaled_width <= debug_image.shape[1]:
                        debug_image[y_pos:y_pos + scaled_height, 10:10 + scaled_width] = stamina_region_scaled
                    
                        # Füge Beschriftung hinzu
                        cv2.putText(debug_image, "Vergrößerte Stamina-Leiste:", (10, y_pos - 5), 
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            
            # HSV-Farbbereich anzeigen
            y_info = height + 150
            cv2.putText(debug_image, f"HSV-Farbbereich für Gelb: [{self.lower_yellow[0]}-{self.upper_yellow[0]}, {self.lower_yellow[1]}-{self.upper_yellow[1]}, {self.lower_yellow[2]}-{self.upper_yellow[2]}]", 
                      (10, y_info), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            
            # Speichere Debug-Bild
            cv2.imwrite(f"{self.output_dir_debug}/best_rectangle.jpg", debug_image)
            
            # Speichere auch das ursprüngliche Bild
            cv2.imwrite(f"{self.output_dir_debug}/original_frame_{frame_pos}.jpg", frame)
        
        sample_frame.release()

    def _calculate_roi(self, frame):
        h, w = frame.shape[:2]
        x1, y1 = int(self.roi_x1_percent * w), int(self.roi_y1_percent * h)
        x2, y2 = int(self.roi_x2_percent * w), int(self.roi_y2_percent * h)
        return x1, y1, x2, y2

    def _calculate_yellow_ratio(self, detected_rect, w, h):
        hsv = cv2.cvtColor(detected_rect, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.lower_yellow, self.upper_yellow)
        yellow_pixels = np.count_nonzero(mask)
        return yellow_pixels / (w * h) if w * h > 0 else 0
        
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
        if not self.rectangle_counter:
            return None
            
        # Sortieren nach Häufigkeit
        candidates = self.rectangle_counter.most_common(5)
        
        # Filter für zu kleine Kandidaten
        candidates = [(rect, count) for rect, count in candidates if count >= 3]
        
        if not candidates:
            return None
            
        # Nimm das am häufigsten erkannte Rechteck
        best_rectangle, count = candidates[0]
        print(f"Stabilstes Rechteck gefunden: {best_rectangle} mit Häufigkeit {count}")
        return best_rectangle
    
    async def analyze_video(self, rectangle_coords, progress_callback=None):
        """
        Analyzes the video and detects when the player is out of stamina using parallel processing
        to better utilize CPU cores while remaining responsive.
        """
        if not rectangle_coords:
            print("No stamina area defined!")
            return []
            
        # Ensure output directories exist
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.output_dir_debug, exist_ok=True)
        
        # Initialize video stream to get properties
        self.cap = cv2.VideoCapture(self.video_path)
        frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.cap.release()
        
        # If no frames, return empty list
        if frame_count <= 0:
            print("No frames to process!")
            return []
        
        # Secondary color mask variables
        secondary_lower_yellow = np.array([max(10, self.lower_yellow[0] - 10), 
                                          max(30, self.lower_yellow[1] - 40), 
                                          max(40, self.lower_yellow[2] - 40)])
        
        secondary_upper_yellow = np.array([min(40, self.upper_yellow[0] + 10), 
                                          min(255, self.upper_yellow[1] + 40), 
                                          min(255, self.upper_yellow[2] + 40)])
        
        # Define the number of processes based on CPU cores
        num_processes = max(1, min(multiprocessing.cpu_count() - 1, 8))  # Use at most n-1 cores, max 8
        
        if self.debug:
            print(f"Using {num_processes} processes for parallel processing")
        
        # Calculate chunk size for each process
        # Ensure we have at least 1 chunk per process, and at least 100 frames per chunk when possible
        if frame_count <= num_processes:
            chunk_size = 1  # If fewer frames than processes, each process gets 1 frame
            num_chunks = frame_count
        else:
            # Try to have at least 100 frames per chunk
            ideal_chunk_size = max(100, frame_count // num_processes)
            # But make sure we don't have too few chunks (should have at least one per process)
            num_chunks = min(num_processes, frame_count // ideal_chunk_size)
            if num_chunks < 1:
                num_chunks = 1
            chunk_size = frame_count // num_chunks
        
        # Prepare data chunks for parallel processing
        chunks = []
        for i in range(num_chunks):
            start_frame = i * chunk_size
            end_frame = min(start_frame + chunk_size, frame_count)
            
            # Create chunk data
            chunk_data = (
                self.video_path, start_frame, end_frame, rectangle_coords,
                self.lower_yellow, self.upper_yellow,
                secondary_lower_yellow, secondary_upper_yellow,
                self.debug, 300, self.output_dir_debug, self.output_dir
            )
            chunks.append(chunk_data)
        
        # Create a progress tracker
        progress_tracker = [0] * len(chunks)
        last_progress_time = time.time()
        progress_interval = 1  # seconds between progress updates
        
        # Process chunks in parallel but ensure everything remains async
        all_events = []
        
        # Create the process pool executor
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=num_processes) as executor:
            # Submit all tasks and store futures
            futures = [executor.submit(process_frame_chunk, chunk) for chunk in chunks]
            
            # Track which futures are still pending
            pending_futures = {i: future for i, future in enumerate(futures)}
            
            # Asynchronously wait for futures to complete
            while pending_futures:
                # Use asyncio.sleep to yield control back to the event loop frequently
                await asyncio.sleep(0.1)
                
                # Check for completed futures
                completed_indices = []
                for idx, future in list(pending_futures.items()):
                    if future.done():
                        # Process this completed future
                        try:
                            chunk_events = future.result()
                            all_events.extend(chunk_events)
                            
                            # Update progress
                            start_frame = chunks[idx][1]
                            end_frame = chunks[idx][2]
                            completed_frames = end_frame - start_frame
                            
                            # Call progress callback if provided and if it's time for an update
                            current_time = time.time()
                            if progress_callback and (current_time - last_progress_time >= progress_interval):
                                total_completed = sum([chunks[i][2] - chunks[i][1] for i in completed_indices])
                                await progress_callback(total_completed, frame_count)
                                last_progress_time = current_time
                        
                        except Exception as e:
                            print(f"Error processing chunk {idx}: {str(e)}")
                        
                        # Mark this future as completed
                        completed_indices.append(idx)
                
                # Remove completed futures from pending
                for idx in completed_indices:
                    del pending_futures[idx]
        
        # Final progress update
        if progress_callback:
            await progress_callback(frame_count, frame_count)
        
        # Sort events by timestamp (they might be out of order due to parallel processing)
        all_events.sort(key=lambda x: x[0])
        
        # Convert event times to formatted strings
        formatted_events = []
        for frame_number, is_empty in all_events:
            timestamp = self._frame_to_timestamp(frame_number, fps)
            formatted_events.append(timestamp)
        
        return formatted_events

    def _frame_to_timestamp(self, frame_number, fps):
        """Convert frame number to a timestamp string in MM:SS format"""
        seconds = frame_number / fps
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"
        
    async def _save_debug_frame(self, img):
        """Erstellt und speichert ein Debug-Bild - asynchron"""
        def save_image():
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
        
        await asyncio.to_thread(save_image)


if __name__ == "__main__":
    video_analyzer = VideoAnalyzer("./downloads/video.mp4", debug=True)
    stable_rectangle = asyncio.run(video_analyzer.find_stable_rectangle(15000))
    timestamps = asyncio.run(video_analyzer.analyze_video(stable_rectangle))

    print(timestamps)
