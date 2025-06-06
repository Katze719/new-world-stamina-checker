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
        
    async def find_stable_rectangle(self, training_frame_count: int):
        frame_number = 0
        while self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                break

            frame_number += 1
            if frame_number > training_frame_count:
                break

            x1, y1, x2, y2 = self._calculate_roi(frame)
            roi = frame[y1:y2, x1:x2]
            contours = await asyncio.to_thread(self._find_contours, roi)
            
            for contour in contours:
                x, y, w, h = self._validate_rectangle(contour, x1, y1)
                if x is not None:
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
        
        # Track stamina levels throughout the video
        stamina_data = []
        hue_data = []
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            
            if on_progress and frame_number % 1000 == 0:
                await on_progress(frame_number, self.frame_count)

            frame_number += 1
            timestamp = frame_number / self.fps
            
            x1, y1, x2, y2 = self._calculate_roi(frame)
            roi = frame[y1:y2, x1:x2]
            contours = await asyncio.to_thread(self._find_contours, roi)
            
            # Always measure current stamina in the fixed rectangle
            stable_rect = frame[y_fixed:y_fixed + h_fixed, x_fixed:x_fixed + w_fixed]
            yellow_ratio = self._calculate_yellow_ratio(stable_rect, w_fixed, h_fixed)
            
            # Store stamina level data (yellow ratio) and timestamp
            stamina_data.append((timestamp, yellow_ratio))
            
            # Store hue distribution data
            hsv = cv2.cvtColor(stable_rect, cv2.COLOR_BGR2HSV)
            # Get only yellow pixels to analyze their hue
            mask = cv2.inRange(hsv, self.lower_yellow, self.upper_yellow)
            if np.count_nonzero(mask) > 0:
                # Calculate average hue, saturation, value of yellow pixels
                yellow_pixels = hsv[mask > 0]
                if len(yellow_pixels) > 0:
                    avg_hue = np.mean(yellow_pixels[:, 0])
                    avg_saturation = np.mean(yellow_pixels[:, 1])
                    avg_value = np.mean(yellow_pixels[:, 2])
                    hue_data.append((timestamp, avg_hue, avg_saturation, avg_value))
                else:
                    hue_data.append((timestamp, 0, 0, 0))
            else:
                hue_data.append((timestamp, 0, 0, 0))
            
            for contour in contours:
                x, y, w, h = self._validate_rectangle(contour, x1, y1)
                if x is not None:
                    deviation = abs(x - x_fixed) + abs(y - y_fixed) + abs(w - w_fixed) + abs(h - h_fixed)
                    if deviation <= 60:
                        if yellow_ratio > 0.08:
                            high_yellow_found = True
                        if yellow_ratio < 0.02 and high_yellow_found:
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
                                
                                # Add text with frame number and timestamp
                                timestamp_seconds = frame_number / self.fps
                                minutes = int(timestamp_seconds // 60)
                                seconds = int(timestamp_seconds % 60)
                                ms = int((timestamp_seconds % 1) * 1000)
                                timestamp_text = f"Frame: {frame_number} | Time: {minutes:02}:{seconds:02}.{ms:03}"
                                cv2.putText(frame, timestamp_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                                
                                # Create visualization of stamina bar and its yellow detection
                                # Create a fixed size bottom panel that's big enough for our visualizations
                                bottom_panel_height = 150  # Fixed height for panel
                                
                                # Create a canvas with extra space at the bottom
                                frame_with_panel = np.zeros((frame.shape[0] + bottom_panel_height, frame.shape[1], 3), dtype=np.uint8)
                                frame_with_panel[:frame.shape[0], :] = frame  # Copy original frame
                                # Fill the bottom panel with a dark gray background
                                frame_with_panel[frame.shape[0]:, :] = [30, 30, 30]  # Dark gray background
                                
                                # Draw labels
                                cv2.putText(frame_with_panel, "Original Stamina Bar:", 
                                          (10, frame.shape[0] + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                                cv2.putText(frame_with_panel, "Yellow Detection Mask:", 
                                          (frame.shape[1]//2 + 10, frame.shape[0] + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                                
                                try:
                                    # Ensure coordinates are within frame boundaries
                                    y_fixed_safe = max(0, min(y_fixed, frame.shape[0]-1))
                                    x_fixed_safe = max(0, min(x_fixed, frame.shape[1]-1))
                                    height_safe = min(h_fixed, frame.shape[0] - y_fixed_safe)
                                    width_safe = min(w_fixed, frame.shape[1] - x_fixed_safe)
                                    
                                    # Get the stamina bar ROI with safety checks
                                    if height_safe > 0 and width_safe > 0:
                                        stable_rect = frame[y_fixed_safe:y_fixed_safe + height_safe, 
                                                           x_fixed_safe:x_fixed_safe + width_safe].copy()
                                        
                                        # Scale up stamina bar for better visibility (but keep it reasonable)
                                        scale_factor = 3.0
                                        scaled_width = int(width_safe * scale_factor)
                                        scaled_height = int(height_safe * scale_factor)
                                        
                                        # Make sure the scaled dimensions aren't too large
                                        max_width = frame.shape[1] // 2 - 20
                                        if scaled_width > max_width:
                                            scale_factor = max_width / width_safe
                                            scaled_width = int(width_safe * scale_factor)
                                            scaled_height = int(height_safe * scale_factor)
                                        
                                        # Resize with safety check
                                        if stable_rect.size > 0 and scaled_width > 0 and scaled_height > 0:
                                            scaled_roi = cv2.resize(stable_rect, (scaled_width, scaled_height))
                                            
                                            # Add a white border
                                            cv2.rectangle(scaled_roi, (0, 0), (scaled_width-1, scaled_height-1), (255, 255, 255), 1)
                                            
                                            # Calculate positions for ROIs in the bottom panel
                                            roi_y_pos = frame.shape[0] + 30
                                            roi_x_pos = 10
                                            
                                            # Copy the scaled ROI to the bottom panel - left side
                                            if roi_y_pos + scaled_height <= frame_with_panel.shape[0] and roi_x_pos + scaled_width <= frame_with_panel.shape[1]:
                                                frame_with_panel[roi_y_pos:roi_y_pos + scaled_height, 
                                                               roi_x_pos:roi_x_pos + scaled_width] = scaled_roi
                                            
                                            # Create mask visualization
                                            hsv = cv2.cvtColor(stable_rect, cv2.COLOR_BGR2HSV)
                                            mask = cv2.inRange(hsv, self.lower_yellow, self.upper_yellow)
                                            
                                            # Create colored mask (yellow on black)
                                            mask_colored = np.zeros_like(stable_rect)
                                            mask_colored[mask > 0] = [0, 255, 255]  # BGR for yellow
                                            
                                            # Scale up mask
                                            scaled_mask = cv2.resize(mask_colored, (scaled_width, scaled_height))
                                            
                                            # Add a white border to the mask
                                            cv2.rectangle(scaled_mask, (0, 0), (scaled_width-1, scaled_height-1), (255, 255, 255), 1)
                                            
                                            # Copy the mask to the bottom panel - right side
                                            roi_x_pos = frame.shape[1]//2 + 10
                                            if roi_y_pos + scaled_height <= frame_with_panel.shape[0] and roi_x_pos + scaled_width <= frame_with_panel.shape[1]:
                                                frame_with_panel[roi_y_pos:roi_y_pos + scaled_height, 
                                                               roi_x_pos:roi_x_pos + scaled_width] = scaled_mask
                                    else:
                                        # If we can't get a valid ROI, show an error message
                                        error_msg = "Error: Invalid stamina bar region"
                                        cv2.putText(frame_with_panel, error_msg, 
                                                  (10, frame.shape[0] + 70), 
                                                  cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                                except Exception as e:
                                    # Something went wrong with the visualization - let's show an error
                                    error_msg = f"Error: {str(e)}"
                                    cv2.putText(frame_with_panel, error_msg, 
                                              (10, frame.shape[0] + 70), 
                                              cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                                
                                # Draw yellow ratio text
                                yellow_ratio_text = f"Yellow Ratio: {yellow_ratio:.2%}"
                                cv2.putText(frame_with_panel, yellow_ratio_text, 
                                          (10, frame.shape[0] + bottom_panel_height - 20), 
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                                
                                # Save the enhanced debug image
                                cv2.imwrite(f"{self.output_dir}/{frame_number}.jpg", frame_with_panel)
            
        cap.release()
        print(f"Anzahl der Frames mit weniger als 5% Gelb: {low_yellow_frame_count}")
        return self.saved_timestamps, stamina_data, hue_data

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
