from flask import Flask, request, jsonify
from picarx import Picarx
from vilib import Vilib
import cv2
import vilib.vilib
import time
import json

# Monkeypatch get_frame to optimize JPEG compression (quality=50) and handle None frames safely
vilib.vilib.get_frame = lambda: cv2.imencode('.jpg', Vilib.flask_img, [int(cv2.IMWRITE_JPEG_QUALITY), 50])[1].tobytes() if Vilib.flask_img is not None else b''
import sys
import os
import psutil
import threading
from io import StringIO

app = Flask(__name__)

i2c_lock = threading.Lock()

# Initialize PiCar-X
try:
    with i2c_lock:
        px = Picarx()
        if px:
            px.stop()
            print("PiCar-X hardware initialized and stopped.")
except Exception as e:
    print(f"Error initializing Picarx: {e}")
    px = None

from mapping import OccupancyGrid
from vision_distance import VisionSensor
from autonomous_drive import AutonomousExplorer

grid = OccupancyGrid()
vision = VisionSensor()
explorer = AutonomousExplorer(grid, vision, px, i2c_lock)

# Initialize Vilib camera streaming (enabled by default)
camera_started = False
try:
    try:
        with i2c_lock:
            Vilib.picam2.close()
    except Exception as e:
        pass
    from picamera2 import Picamera2
    with i2c_lock:
        Vilib.picam2 = Picamera2()
        Vilib.camera_start(vflip=False, hflip=False, size=(320, 240))
        Vilib.display(local=False, web=True)
    camera_started = True
    print("Vilib camera started automatically on server startup.")
except Exception as e:
    print(f"Failed to auto-start camera: {e}")

# Keep track of current state
state = {
    "speed": 0,
    "steering_angle": 0,
    "pan_angle": 0,
    "tilt_angle": 0,
    "camera_active": camera_started,
    "direction": "stop"
}

# Shared sensor readings
sensor_data = {
    "distance": -1.0,
    "camera_distance": -1.0,
    "grayscale": [0, 0, 0],
    "battery_voltage": -1.0
}

# Safety Watchdog variables
last_move_time = time.time()

def get_safety_thresholds(current_speed):
    # Stop distance scales from 10cm (at speed 25) to 25cm (at speed 100)
    stop_dist = 10
    if current_speed > 25:
        stop_dist += (current_speed - 25) * 0.20 # 10 + 15 = 25cm
    stop_dist = int(stop_dist)
    
    # Slow distance scales from 35cm (at speed 25) to 65cm (at speed 100)
    slow_dist = 35
    if current_speed > 25:
        slow_dist += (current_speed - 25) * 0.40 # 35 + 30 = 65cm
    slow_dist = int(slow_dist)
    
    return stop_dist, slow_dist

def safety_watchdog():
    global last_move_time, state, sensor_data
    idle_voltage = None
    consecutive_sags = 0
    STALL_VOLTAGE_SAG = 0.25 # in Volts
    
    while True:
        time.sleep(0.05) # Check every 50ms for collision / heartbeat
        if px:
            # Read battery voltage from A4
            try:
                from robot_hat import ADC
                global _watchdog_battery_adc
                if '_watchdog_battery_adc' not in globals():
                    _watchdog_battery_adc = ADC('A4')
                raw_v = _watchdog_battery_adc.read_voltage()
                voltage = round(raw_v * 3.0, 2)
            except Exception as e:
                voltage = -1.0

            # 1. Read sensors under lock
            try:
                with i2c_lock:
                    distance = round(px.get_distance(), 1)
            except:
                distance = -1.0
                
            try:
                with i2c_lock:
                    grayscale = px.get_grayscale_data()
            except:
                grayscale = [0, 0, 0]
                
            cam_dist = -1.0
            from vilib import Vilib
            frame = None
            if Vilib.flask_img is not None:
                try:
                    import numpy as np
                    frame = np.array(Vilib.flask_img, dtype=np.uint8)
                except:
                    frame = None
            if frame is not None:
                try:
                    est = vision.estimate_distance(frame)
                    if est:
                        cam_dist = round(est, 1)
                except Exception as e:
                    print(f"Error in watchdog camera estimate: {e}")
                
            sensor_data["distance"] = distance
            sensor_data["camera_distance"] = cam_dist
            sensor_data["grayscale"] = grayscale
            sensor_data["battery_voltage"] = voltage

            # Stall detection logic
            if voltage > 0:
                if state["speed"] == 0:
                    if idle_voltage is None:
                        idle_voltage = voltage
                    else:
                        idle_voltage = 0.9 * idle_voltage + 0.1 * voltage
                    consecutive_sags = 0
                else:
                    if idle_voltage is not None:
                        sag = idle_voltage - voltage
                        if sag > STALL_VOLTAGE_SAG:
                            consecutive_sags += 1
                        else:
                            consecutive_sags = max(0, consecutive_sags - 1)
                        
                        if consecutive_sags >= 5: # 250ms of sustained sag
                            print(f"Watchdog Stall Protection: Stall detected! Sag={sag:.2f}V (Idle: {idle_voltage:.2f}V, Current: {voltage:.2f}V). Stopping!")
                            with i2c_lock:
                                px.stop()
                            state["speed"] = 0
                            state["direction"] = "stop"
                            if explorer.state == "EXPLORING":
                                explorer.stop_exploration()
                            consecutive_sags = 0

            # Check cliff
            cliff_triggered = False
            if grayscale:
                is_cliff_low = True
                if explorer.floor_sample is not None and explorer.air_sample is not None:
                    is_cliff_low = explorer.air_sample < explorer.floor_sample
                if is_cliff_low:
                    cliff_triggered = all(val < explorer.cliff_threshold for val in grayscale)
                else:
                    cliff_triggered = all(val > explorer.cliff_threshold for val in grayscale)

            # 2. Cliff detection override
            if cliff_triggered and state["direction"] != "stop":
                print(f"Watchdog Cliff Override: Cliff detected! Halting and backing away.")
                with i2c_lock:
                    px.stop()
                    px.backward(30)
                time.sleep(0.5)
                with i2c_lock:
                    px.stop()
                state["speed"] = 0
                state["direction"] = "stop"
                continue

            # Use the minimum of ultrasonic and camera (if valid) for collision check
            valid_dists = [d for d in [distance, cam_dist] if d > 0]
            min_dist = min(valid_dists) if valid_dists else -1.0

            # 3. Collision prevention (Auto-Brake)
            if state["direction"] == "forward" and min_dist > 0:
                stop_dist, slow_dist = get_safety_thresholds(state["speed"])
                if min_dist < stop_dist:
                    print(f"Watchdog Auto-Brake: Obstacle at {min_dist:.1f}cm (US: {distance:.1f}, Cam: {cam_dist:.1f}). Stopping!")
                    with i2c_lock:
                        px.stop()
                    state["speed"] = 0
                    state["direction"] = "stop"
                elif min_dist < slow_dist:
                    target_speed = state["speed"]
                    min_speed = 25
                    if target_speed > min_speed:
                        scaled = min_speed + (target_speed - min_speed) * (min_dist - stop_dist) / (slow_dist - stop_dist)
                        scaled = int(max(min_speed, min(target_speed, scaled)))
                        with i2c_lock:
                            px.forward(scaled)

            # 3. Watchdog timeout check (Heartbeat)
            if state["speed"] > 0:
                if time.time() - last_move_time > 1.0:
                    print("WATCHDOG TRIGGERED: No heartbeat received for 1s. Halting robot!")
                    with i2c_lock:
                        px.stop()
                    state["speed"] = 0
                    state["direction"] = "stop"

watchdog_thread = threading.Thread(target=safety_watchdog, daemon=True)
watchdog_thread.start()

# Namespace for interactive code execution
thread_local = threading.local()
original_sleep = time.sleep

def custom_sleep(seconds):
    if not getattr(thread_local, "is_script_thread", False):
        original_sleep(seconds)
        return
    start_time = time.time()
    while time.time() - start_time < seconds:
        if state.get("abort_scripts", False):
            # Disable script thread mode before raising so cleanup calls to time.sleep don't raise again
            thread_local.is_script_thread = False
            raise InterruptedError("Script execution aborted.")
        original_sleep(0.01)

# Globally patch time.sleep to use our thread-aware cancelable sleep
time.sleep = custom_sleep

exec_globals = {
    "px": px,
    "Vilib": Vilib,
    "time": time,
    "sleep": custom_sleep,
    "state": state
}

def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = float(f.read()) / 1000.0
            return round(temp, 1)
    except:
        return 0.0



@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        "status": "success",
        "state": state
    })

@app.route('/api/move', methods=['POST'])
def move_car():
    t_recv = time.time() * 1000.0
    global last_move_time
    if not px:
        return jsonify({"status": "error", "message": "Picarx not initialized"}), 500
    
    data = request.get_json(silent=True) or {}
    action = data.get("action", "stop")
    speed = int(data.get("speed", state["speed"]))
    steering_angle = int(data.get("steering_angle", state["steering_angle"]))
    
    # Constrain speed and steering
    speed = max(0, min(100, speed))
    steering_angle = max(-30, min(30, steering_angle))
    
    state["speed"] = speed
    state["steering_angle"] = steering_angle
    
    with i2c_lock:
        px.set_dir_servo_angle(steering_angle)
    
    # Reset watchdog timer for movement actions
    if action == "forward":
        # Check cliff first
        with i2c_lock:
            grayscale = px.get_grayscale_data() if px else [0, 0, 0]
        is_cliff_low = True
        if explorer.floor_sample is not None and explorer.air_sample is not None:
            is_cliff_low = explorer.air_sample < explorer.floor_sample
            
        cliff_triggered = False
        if grayscale:
            if is_cliff_low:
                cliff_triggered = all(val < explorer.cliff_threshold for val in grayscale)
            else:
                cliff_triggered = all(val > explorer.cliff_threshold for val in grayscale)
                
        if cliff_triggered:
            with i2c_lock:
                px.stop()
            state["speed"] = 0
            state["direction"] = "stop"
            t_done = time.time() * 1000.0
            return jsonify({
                "status": "blocked",
                "message": "Cliff detected in front!",
                "state": state,
                "telemetry": {
                    "distance": sensor_data["distance"],
                    "camera_distance": sensor_data["camera_distance"],
                    "grayscale": grayscale
                },
                "t_robot_received": t_recv,
                "t_robot_done": t_done
            })

        valid_dists = [d for d in [sensor_data["distance"], sensor_data["camera_distance"]] if d > 0]
        distance = min(valid_dists) if valid_dists else -1.0
            
        stop_dist, slow_dist = get_safety_thresholds(speed)
        
        if 0 < distance < stop_dist:
            with i2c_lock:
                px.stop()
            state["speed"] = 0
            state["direction"] = "stop"
            t_done = time.time() * 1000.0
            return jsonify({
                "status": "blocked",
                "message": "Obstacle in front!",
                "state": state,
                "telemetry": {
                    "distance": sensor_data["distance"],
                    "camera_distance": sensor_data["camera_distance"],
                    "grayscale": sensor_data["grayscale"]
                },
                "t_robot_received": t_recv,
                "t_robot_done": t_done
            })
            
        last_move_time = time.time()
        state["direction"] = "forward"
        # If very close, scale speed immediately
        if stop_dist <= distance < slow_dist:
            min_speed = 25
            speed = int(max(min_speed, min(speed, min_speed + (speed - min_speed) * (distance - stop_dist) / (slow_dist - stop_dist))))
        with i2c_lock:
            px.forward(speed)
        
    elif action == "backward":
        last_move_time = time.time()
        state["direction"] = "backward"
        with i2c_lock:
            px.backward(speed)
        
    elif action == "stop":
        state["abort_scripts"] = True
        with i2c_lock:
            px.stop()
        state["speed"] = 0
        state["direction"] = "stop"
        
    else:
        # Just update steering or speed, still update watchdog to prevent stop if dragging sliders
        last_move_time = time.time()
        
    t_done = time.time() * 1000.0
    return jsonify({
        "status": "success",
        "state": state,
        "telemetry": {
            "distance": sensor_data["distance"],
            "grayscale": sensor_data["grayscale"]
        },
        "t_robot_received": t_recv,
        "t_robot_done": t_done
    })

@app.route('/api/camera', methods=['POST'])
def control_camera():
    t_recv = time.time() * 1000.0
    if not px:
        return jsonify({"status": "error", "message": "Picarx not initialized"}), 500

    data = request.get_json(silent=True) or {}
    pan = data.get("pan")
    tilt = data.get("tilt")
    
    if pan is not None:
        pan = max(-90, min(90, int(pan)))
        with i2c_lock:
            px.set_cam_pan_angle(pan)
        state["pan_angle"] = pan
        
    if tilt is not None:
        tilt = max(-35, min(65, int(tilt)))
        with i2c_lock:
            px.set_cam_tilt_angle(tilt)
        state["tilt_angle"] = tilt
        
    t_done = time.time() * 1000.0
    return jsonify({
        "status": "success",
        "state": state,
        "telemetry": {
            "distance": sensor_data["distance"],
            "grayscale": sensor_data["grayscale"]
        },
        "t_robot_received": t_recv,
        "t_robot_done": t_done
    })

@app.route('/api/camera_switch', methods=['POST'])
def camera_switch():
    global camera_started
    data = request.get_json(silent=True) or {}
    activate = data.get("active", True)
    
    try:
        if activate and not camera_started:
            try:
                # Run camera close under lock or let picamera handle it
                # Vilib has its own internal setup, but let's make sure it doesn't collide
                with i2c_lock:
                    Vilib.picam2.close()
            except Exception as e:
                pass
            from picamera2 import Picamera2
            with i2c_lock:
                Vilib.picam2 = Picamera2()
                Vilib.camera_start(vflip=False, hflip=False, size=(320, 240))
                Vilib.display(local=False, web=True)
            camera_started = True
        elif not activate and camera_started:
            with i2c_lock:
                Vilib.camera_close()
            camera_started = False
        state["camera_active"] = camera_started
        return jsonify({"status": "success", "camera_active": camera_started})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    if not px:
        return jsonify({"status": "error", "message": "Picarx not initialized"}), 500
        
    # Read system info (non-blocking)
    cpu_temp = get_cpu_temp()
    cpu_usage = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    mem_usage = mem.percent
    
    return jsonify({
        "status": "success",
        "telemetry": {
            "distance": sensor_data["distance"],
            "camera_distance": sensor_data["camera_distance"],
            "grayscale": sensor_data["grayscale"],
            "battery_voltage": sensor_data["battery_voltage"],
            "cpu_temp": cpu_temp,
            "cpu_usage": cpu_usage,
            "memory_usage": mem_usage
        }
    })

@app.route('/api/execute', methods=['POST'])
def execute_code():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    
    if not code.strip():
        return jsonify({"status": "success", "output": ""})
        
    thread_local.is_script_thread = True
    state["abort_scripts"] = False
        
    # Redirect stdout to capture print statements
    old_stdout = sys.stdout
    redirected_output = sys.stdout = StringIO()
    
    success = True
    error_msg = ""
    
    try:
        # We use exec to run the code within our global namespace
        exec(code, exec_globals)
    except InterruptedError:
        success = True
        print("Script execution aborted by user.")
    except Exception as e:
        success = False
        import traceback
        traceback.print_exc(file=sys.stdout)
    finally:
        thread_local.is_script_thread = False
        sys.stdout = old_stdout
        
    output = redirected_output.getvalue()
    
    return jsonify({
        "status": "success" if success else "error",
        "output": output
    })

@app.route('/api/map/data', methods=['GET'])
def get_map_telemetry():
    return jsonify({
        "map": grid.get_payload(),
        "ultrasound_distance": explorer.us_dist,
        "camera_distance": explorer.cam_dist,
        "state": explorer.state,
        "x": explorer.x,
        "y": explorer.y,
        "heading": explorer.heading_deg
    })

@app.route('/api/explore/start', methods=['POST'])
def start_explore():
    explorer.start_exploration()
    return jsonify({"status": "success", "state": explorer.state})

@app.route('/api/explore/stop', methods=['POST'])
def stop_explore():
    explorer.stop_exploration()
    return jsonify({"status": "success", "state": explorer.state})

@app.route('/api/map/reset', methods=['POST'])
def reset_map():
    grid.reset_map()
    explorer.x = 0.0
    explorer.y = 0.0
    explorer.heading_deg = 0.0
    return jsonify({"status": "success", "message": "SLAM map and robot position reset successfully."})


@app.route('/api/calibrate/steering', methods=['POST'])
def calibrate_steering():
    explorer.state = "CALIBRATING"
    with i2c_lock:
        px.stop()
    data = request.get_json(silent=True) or {}
    angle = int(data.get('angle', 0))
    with i2c_lock:
        px.set_dir_servo_angle(angle)
    return jsonify({"status": "steering_adjusted", "angle": angle})

@app.route('/api/calibrate/read_sensors', methods=['GET'])
def read_sensors_for_calibration():
    with i2c_lock:
        grayscale = px.get_grayscale_data() if px else [0, 0, 0]
    return jsonify({
        "grayscale": grayscale
    })

@app.route('/api/calibrate/camera', methods=['POST'])
def calibrate_camera():
    data = request.get_json(silent=True) or {}
    target_dist = float(data.get('target_distance', 20.0))
    
    # Read frame from Vilib if available
    from vilib import Vilib
    frame = None
    if Vilib.flask_img is not None:
        try:
            import numpy as np
            frame = np.array(Vilib.flask_img, dtype=np.uint8)
        except Exception as e:
            print(f"Error converting Vilib frame to numpy array: {e}")
            frame = None
        
    if frame is None:
        return jsonify({"status": "error", "message": "Camera offline / Vilib not started"}), 500
    
    lowest_y = vision.find_lowest_obstacle_pixel(frame)
    if lowest_y is None:
        return jsonify({"status": "error", "message": "No object contours detected. Ensure a distinct, high-contrast obstacle is placed in front of the camera."}), 400
        
    c_y = frame.shape[0] / 2.0
    if lowest_y <= c_y:
        return jsonify({"status": "error", "message": f"Obstacle baseline (y={lowest_y}) must be below the horizon center line (y={c_y}). Try tilting the camera down or positioning the obstacle closer."}), 400
        
    computed_f = (target_dist * (lowest_y - c_y)) / vision.cam_height
    vision.focal_length = computed_f
    return jsonify({"status": "success", "focal_length": computed_f})

@app.route('/api/calibrate/save', methods=['POST'])
def save_calibration():
    config_payload = request.get_json(silent=True) or {}
    os.makedirs("data", exist_ok=True)
    with open("data/calibration_config.json", "w") as f:
        json.dump(config_payload, f)
    explorer.state = "IDLE"
    explorer.load_calibration_config()
    return jsonify({"status": "saved"})

@app.route('/api/calibrate/config', methods=['GET'])
def get_calibration_config():
    config_path = "data/calibration_config.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                return jsonify(json.load(f))
        except Exception as e:
            print(f"Error reading config: {e}")
    return jsonify({
        "steering_offset": 0,
        "cliff_threshold": 1000,
        "focal_length": 350.0,
        "floor_sample": None,
        "air_sample": None
    })

if __name__ == '__main__':
    # Bind to all interfaces on port 5000
    app.run(host='0.0.0.0', port=5000, debug=False)
