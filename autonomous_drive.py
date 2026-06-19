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
        
        # Initialize IMU (GY-521 Default Address is 0x68)
        try:
            from mpu6050 import mpu6050
            with self.i2c_lock:
                self.imu = mpu6050(0x68)
        except Exception as e:
            print(f"Error initializing IMU: {e}")
            self.imu = None
            
        # Raw Live Acceleration values
        self.accel_x, self.accel_y, self.accel_z = 0.0, 0.0, 0.0
        
        # Dynamic Orientation Map Defaults
        self.axis_map = {"x": "x", "y": "y", "z": "z"}
        self.axis_signs = {"x": 1, "y": 1, "z": 1}
        self.axis_offsets = {"x": 0.0, "y": 0.0, "z": 0.0}
        self.collision_threshold = 5.0
        self.collision_detected = False
        self.collision_active = False
        self.collision_direction = "stop"
        
        # Calibration defaults (overwritten by config files)
        self.cliff_threshold = 1000
        self.steering_offset = 0
        self.speed = 15.0  # Measured cm/s speed
        self.load_calibration_config()
        
        # State Tracking (Odometry)
        self.x, self.y, self.heading_deg = 0.0, 0.0, 0.0
        self.us_dist, self.cam_dist = 999, 999
        
        # Start a background thread to update live telemetry
        threading.Thread(target=self._telemetry_stream_loop, daemon=True).start()

    def _telemetry_stream_loop(self):
        """Continuously reads raw IMU values and maps them using calibrated profiles."""
        self._last_accel_x = 0.0
        self._last_accel_y = 0.0
        self._last_accel_z = 0.0
        
        while True:
            if self.imu:
                try:
                    with self.i2c_lock:
                        raw_accel = self.imu.get_accel_data()
                    # Apply mappings and zero-g offsets dynamically
                    mapped = {
                        "x": raw_accel[self.axis_map["x"]] * self.axis_signs["x"],
                        "y": raw_accel[self.axis_map["y"]] * self.axis_signs["y"],
                        "z": raw_accel[self.axis_map["z"]] * self.axis_signs["z"]
                    }
                    self.accel_x = mapped["x"] - self.axis_offsets.get("x", 0.0)
                    self.accel_y = mapped["y"] - self.axis_offsets.get("y", 0.0)
                    self.accel_z = mapped["z"] - self.axis_offsets.get("z", 0.0)
                    
                    # Calculate difference (jerk/shock) from previous sample
                    dx = self.accel_x - self._last_accel_x
                    dy = self.accel_y - self._last_accel_y
                    dz = self.accel_z - self._last_accel_z
                    shock = math.sqrt(dx*dx + dy*dy + dz*dz)
                    
                    # Skip initial/settling comparison spikes if previous sample was zero
                    if self._last_accel_x != 0.0 or self._last_accel_y != 0.0:
                        if shock > self.collision_threshold:
                            print(f"[IMU SHOCK] Shock detected: {shock:.2f} m/s^2!")
                            if self.state == "EXPLORING":
                                self.collision_detected = True
                            else:
                                self.collision_active = True
                                if hasattr(self, 'state_dict') and self.state_dict:
                                    self.collision_direction = self.state_dict.get("direction", "stop")
                                else:
                                    self.collision_direction = "stop"
                                # Stop car immediately in manual mode
                                with self.i2c_lock:
                                    self.px.stop()
                                    
                    self._last_accel_x = self.accel_x
                    self._last_accel_y = self.accel_y
                    self._last_accel_z = self.accel_z
                except Exception:
                    pass
            time.sleep(0.1) # 10 Hz updates to balance responsiveness and bus load

    def load_calibration_config(self):
        try:
            config_path = "data/calibration_config.json"
            if not os.path.exists(config_path):
                config_path = os.path.expanduser("~/data/calibration_config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    config = json.load(f)
                    self.cliff_threshold = config.get("cliff_threshold", 1000)
                    self.steering_offset = config.get("steering_offset", 0)
                    self.vision.focal_length = config.get("focal_length", 350.0)
                    self.floor_sample = config.get("floor_sample", None)
                    self.air_sample = config.get("air_sample", None)
                    
                    # Load IMU configurations if they exist
                    if "imu_axis_map" in config:
                        self.axis_map = config["imu_axis_map"]
                        self.axis_signs = config["imu_axis_signs"]
                    if "imu_axis_offsets" in config:
                        self.axis_offsets = config["imu_axis_offsets"]
                    if "collision_threshold" in config:
                        self.collision_threshold = config["collision_threshold"]
            else:
                self.floor_sample = None
                self.air_sample = None
        except Exception as e:
            print(f"Error loading calibration config: {e}")
            self.floor_sample = None
            self.air_sample = None

    def check_cliff(self):
        if not self.px:
            return False
        try:
            with self.i2c_lock:
                data = self.px.get_grayscale_data()
            
            # Detect direction of cliff readings dynamically
            is_cliff_low = True
            if hasattr(self, 'floor_sample') and hasattr(self, 'air_sample') and self.floor_sample is not None and self.air_sample is not None:
                is_cliff_low = self.air_sample < self.floor_sample
                
            if is_cliff_low:
                return all(val < self.cliff_threshold for val in data)
            else:
                return all(val > self.cliff_threshold for val in data)
        except Exception as e:
            print(f"Error reading grayscale sensors: {e}")
            return False

    def explore_loop(self):
        last_time = time.time()
        while self.state == "EXPLORING":
            if getattr(self, 'collision_detected', False):
                print("[Collision Override] Impact detected! Halting, backing away, and turning.")
                self.collision_detected = False
                try:
                    with self.i2c_lock:
                        self.px.stop()
                        self.px.backward(30)
                    time.sleep(0.6)
                    # Turn sharp in a different direction (left 45 degrees) to clear obstacle
                    with self.i2c_lock:
                        self.px.set_dir_servo_angle(30 + self.steering_offset)
                        self.px.forward(20)
                    time.sleep(0.5)
                    self.heading_deg += 45.0
                    with self.i2c_lock:
                        self.px.set_dir_servo_angle(self.steering_offset)
                except Exception as e:
                    print(f"Error executing collision avoidance: {e}")
                last_time = time.time()
                continue
                
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

    def run_automated_imu_calibration(self):
        """Executes a physical movement sequence to map axes dynamically."""
        if self.state == "EXPLORING" or not self.imu:
            return False
            
        self.state = "CALIBRATING"
        try:
            with self.i2c_lock:
                self.px.stop()
            time.sleep(1.0) # Settle down car completely
            
            # --- STEP 1: Find Z (Vertical Gravity Vector) ---
            with self.i2c_lock:
                raw_start = self.imu.get_accel_data()
            best_axis = "z"
            max_val = 0
            for axis in ['x', 'y', 'z']:
                if abs(raw_start[axis]) > max_val:
                    max_val = abs(raw_start[axis])
                    best_axis = axis
                    
            self.axis_map["z"] = best_axis
            self.axis_signs["z"] = 1 if raw_start[best_axis] > 0 else -1
            
            remaining_axes = ['x', 'y', 'z']
            remaining_axes.remove(best_axis)
            
            # --- STEP 2: Find X (Forward Linear Acceleration Surge) ---
            # Pulse forward rapidly
            with self.i2c_lock:
                self.px.set_dir_servo_angle(self.steering_offset)
                self.px.forward(50)
            time.sleep(0.4)
            with self.i2c_lock:
                raw_motion = self.imu.get_accel_data()
                self.px.stop()
            
            # Evaluate variance spike
            spike_axis = remaining_axes[0]
            diff_0 = raw_motion[remaining_axes[0]] - raw_start[remaining_axes[0]]
            diff_1 = raw_motion[remaining_axes[1]] - raw_start[remaining_axes[1]]
            
            if abs(diff_1) > abs(diff_0):
                spike_axis = remaining_axes[1]
                forward_diff = diff_1
            else:
                forward_diff = diff_0
                
            self.axis_map["x"] = spike_axis
            self.axis_signs["x"] = 1 if forward_diff > 0 else -1
            
            # --- STEP 3: Assign Y (Lateral Vector Coordination) ---
            remaining_axes.remove(spike_axis)
            self.axis_map["y"] = remaining_axes[0]
            
            # Turn sharp left to establish Y sign conventions via centripetal force
            with self.i2c_lock:
                self.px.set_dir_servo_angle(-30 + self.steering_offset)
                self.px.forward(30)
            time.sleep(0.5)
            with self.i2c_lock:
                raw_turn = self.imu.get_accel_data()
                self.px.stop()
                self.px.set_dir_servo_angle(self.steering_offset)
            
            turn_diff = raw_turn[self.axis_map["y"]] - raw_start[self.axis_map["y"]]
            self.axis_signs["y"] = 1 if turn_diff > 0 else -1
            
            # --- STEP 4: Calculate Zero-G compensation offsets ---
            # Sample mapped acceleration from the initial stationary state raw_start
            mapped_start = {
                "x": raw_start[self.axis_map["x"]] * self.axis_signs["x"],
                "y": raw_start[self.axis_map["y"]] * self.axis_signs["y"],
                "z": raw_start[self.axis_map["z"]] * self.axis_signs["z"]
            }
            # X and Y should resolve to 0.0, Z should resolve to 9.81 m/s²
            self.axis_offsets["x"] = mapped_start["x"]
            self.axis_offsets["y"] = mapped_start["y"]
            self.axis_offsets["z"] = mapped_start["z"] - 9.81
            
            # Save structural adjustments safely to flash config file
            self.save_imu_calibration()
        except Exception as e:
            print(f"Error during IMU calibration: {e}")
            self.state = "IDLE"
            return False
            
        self.state = "IDLE"
        return True

    def save_imu_calibration(self):
        config_path = "data/calibration_config.json"
        if not os.path.exists("data") and not os.path.exists(config_path):
            home_config = os.path.expanduser("~/data/calibration_config.json")
            if os.path.exists(os.path.dirname(home_config)) or os.path.exists(home_config):
                config_path = home_config
                
        config = {}
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except FileNotFoundError:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
        config["imu_axis_map"] = self.axis_map
        config["imu_axis_signs"] = self.axis_signs
        config["imu_axis_offsets"] = self.axis_offsets
        
        with open(config_path, "w") as f:
            json.dump(config, f)
