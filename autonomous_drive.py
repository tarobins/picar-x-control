import time
import math
import threading
import json
import os

class AutonomousExplorer:
    def __init__(self, grid, vision, px, i2c_lock):
        self.px = px
        self.i2c_lock = i2c_lock
        self.grid = grid
        self.vision = vision
        self.state = "IDLE" # Modes: IDLE, CALIBRATING, EXPLORING
        
        # Calibration defaults (overwritten by config files)
        self.cliff_threshold = 1000
        self.steering_offset = 0
        self.speed = 15.0  # Measured cm/s speed
        self.load_calibration_config()
        
        # State Tracking (Odometry)
        self.x, self.y, self.heading_deg = 0.0, 0.0, 0.0
        self.us_dist, self.cam_dist = 999, 999

    def load_calibration_config(self):
        try:
            config_path = "data/calibration_config.json"
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    config = json.load(f)
                    self.cliff_threshold = config.get("cliff_threshold", 1000)
                    self.steering_offset = config.get("steering_offset", 0)
                    self.vision.focal_length = config.get("focal_length", 350.0)
        except Exception as e:
            print(f"Error loading calibration config: {e}")

    def check_cliff(self):
        if not self.px:
            return False
        try:
            with self.i2c_lock:
                data = self.px.get_grayscale_data()
            return all(val > self.cliff_threshold for val in data)
        except Exception as e:
            print(f"Error reading grayscale sensors: {e}")
            return False

    def explore_loop(self):
        last_time = time.time()
        while self.state == "EXPLORING":
            if self.check_cliff():
                print("[Cliff Override] Cliff detected! Halting and backing away.")
                try:
                    with self.i2c_lock:
                        self.px.stop()
                        self.px.backward(30)
                    time.sleep(0.5)
                    with self.i2c_lock:
                        self.px.set_dir_servo_angle(30 + self.steering_offset)
                    self.heading_deg += 30.0
                except Exception as e:
                    print(f"Error executing cliff avoidance: {e}")
                last_time = time.time()
                continue
                
            try:
                with self.i2c_lock:
                    self.us_dist = self.px.get_distance()
            except Exception as e:
                self.us_dist = 999
                
            from vilib import Vilib
            frame = None
            if Vilib.flask_img is not None:
                try:
                    import numpy as np
                    frame = np.array(Vilib.flask_img, dtype=np.uint8)
                except Exception as e:
                    print(f"Error converting Vilib frame to numpy array in explore: {e}")
                    frame = None
                
            if frame is not None:
                est = self.vision.estimate_distance(frame)
                self.cam_dist = est if est else 999
            else:
                self.cam_dist = 999
            
            min_dist = min(self.us_dist, self.cam_dist)
            if min_dist < 25: 
                print(f"[Obstacle Avoidance] Obstacle at {min_dist:.1f}cm (US: {self.us_dist:.1f}, Cam: {self.cam_dist:.1f}). Turning.")
                try:
                    with self.i2c_lock:
                        self.px.stop()
                    heading_rad = math.radians(self.heading_deg)
                    obs_x = self.x + (min_dist * math.cos(heading_rad))
                    obs_y = self.y + (min_dist * math.sin(heading_rad))
                    self.grid.update_cell(obs_x, obs_y, -1)
                    
                    with self.i2c_lock:
                        self.px.set_dir_servo_angle(-30 + self.steering_offset)
                        self.px.forward(20)
                    time.sleep(0.5)
                    self.heading_deg -= 30.0
                    with self.i2c_lock:
                        self.px.set_dir_servo_angle(self.steering_offset)
                except Exception as e:
                    print(f"Error executing obstacle avoidance: {e}")
                last_time = time.time()
            else:
                try:
                    with self.i2c_lock:
                        self.px.forward(20)
                except Exception as e:
                    print(f"Error driving forward: {e}")
                dt = time.time() - last_time
                last_time = time.time()
                
                heading_rad = math.radians(self.heading_deg)
                self.x += (self.speed * dt) * math.cos(heading_rad)
                self.y += (self.speed * dt) * math.sin(heading_rad)
                self.grid.update_cell(self.x, self.y, 1)
                
            time.sleep(0.1)

    def start_exploration(self):
        if self.state != "EXPLORING":
            self.state = "EXPLORING"
            self.load_calibration_config()
            threading.Thread(target=self.explore_loop, daemon=True).start()
            threading.Thread(target=self._persistence_loop, daemon=True).start()

    def stop_exploration(self):
        self.state = "IDLE"
        if self.px:
            try:
                with self.i2c_lock:
                    self.px.stop()
            except Exception as e:
                print(f"Error stopping robot: {e}")
        self.grid.save_map()

    def _persistence_loop(self):
        while self.state == "EXPLORING":
            time.sleep(5)
            self.grid.save_map()
