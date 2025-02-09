import cv2
import numpy as np
from collections import Counter
import os
import asyncio

class VideoAnalyzer:
    def __init__(self, video_path, output_dir="./output/", debug=False):
        self.video_path = video_path
        self.output_dir = output_dir
        self.debug = debug
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.cap = cv2.VideoCapture(self.video_path)
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = int(self.cap.get(cv2.CAP_PROP_FPS))
        
        self.roi_x1_percent, self.roi_y1_percent = 0.405, 0.82  # links,  oben
        self.roi_x2_percent, self.roi_y2_percent = 0.595, 0.96  # rechts, unten
        
        self.min_rect_width = 150  
        self.min_rect_height = 8   
        
        self.lower_yellow = np.array([15, 90, 100])  
        self.upper_yellow = np.array([50, 255, 255])  
        
        self.rectangle_counter = Counter()
        self.saved_timestamps = []
        
    async def find_stable_rectangle(self, training_frame_count: int, skip_first_frames_count: int):
        frame_number = 0
        min_x_threshold = self.frame_width * 0.5 - (self.frame_width * 0.05)  # 5% links von der Mitte

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
                if x is not None and x < min_x_threshold:  # Überprüfung, ob das Rechteck links von der Mitte liegt
                    detected_rect = frame[y:y+h, x:x+w]
                    yellow_ratio = self._calculate_yellow_ratio(detected_rect, w, h)
                    if yellow_ratio >= 0.25:
                        self.rectangle_counter[(x, y, w, h)] += 1

        self.cap.release()
        return self._get_best_rectangle()

    async def analyze_video(self, stable_rectangle, on_progress = None):
        if not stable_rectangle:
            print("Kein stabiles Rechteck gefunden.")
            return
        
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
                if x is not None:
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
                                cv2.rectangle(frame, (x_fixed, y_fixed), (x_fixed + w_fixed, y_fixed + h_fixed), (255, 0, 0), 2)
                                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                                cv2.rectangle(frame, (x1, y1), (x2, y2), (138, 43, 226), 2)
                                cv2.imwrite(f"{self.output_dir}/{frame_number}.jpg", frame)
            
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
            if w >= self.min_rect_width and h >= self.min_rect_height:
                return x + x1, y + y1, w, h
        return None, None, None, None

    def _calculate_yellow_ratio(self, detected_rect, w, h):
        hsv = cv2.cvtColor(detected_rect, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.lower_yellow, self.upper_yellow)
        yellow_pixels = np.count_nonzero(mask)
        return yellow_pixels / (w * h)

    def _get_best_rectangle(self):
        if self.rectangle_counter:
            best_rectangle, _ = self.rectangle_counter.most_common(1)[0]
            print(f"Stabilstes Rechteck gefunden: {best_rectangle}")
            return best_rectangle
        return None

if __name__ == "__main__":
    video_analyzer = VideoAnalyzer("./downloads/video.mp4", debug=True)
    stable_rectangle = asyncio.run(video_analyzer.find_stable_rectangle(15000))
    timestamps = asyncio.run(video_analyzer.analyze_video(stable_rectangle))

    print(timestamps)
