from flask import Flask, request, jsonify
from picarx import Picarx
from vilib import Vilib
import cv2
import vilib.vilib
import time

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

# Initialize Vilib camera streaming (disabled by default for low latency)
camera_started = False

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
    "grayscale": [0, 0, 0]
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
    while True:
        time.sleep(0.05) # Check every 50ms for collision / heartbeat
        if px:
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
                
            sensor_data["distance"] = distance
            sensor_data["grayscale"] = grayscale

            # 2. Collision prevention (Auto-Brake)
            if state["direction"] == "forward" and distance > 0:
                stop_dist, slow_dist = get_safety_thresholds(state["speed"])
                if distance < stop_dist:
                    print(f"Watchdog Auto-Brake: Obstacle at {distance:.1f}cm. Stopping!")
                    with i2c_lock:
                        px.stop()
                    state["speed"] = 0
                    state["direction"] = "stop"
                elif distance < slow_dist:
                    target_speed = state["speed"]
                    min_speed = 25
                    if target_speed > min_speed:
                        scaled = min_speed + (target_speed - min_speed) * (distance - stop_dist) / (slow_dist - stop_dist)
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
exec_globals = {
    "px": px,
    "Vilib": Vilib,
    "time": time,
    "sleep": time.sleep,
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
    
    data = request.json or {}
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
        distance = sensor_data["distance"]
            
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
        "t_robot_received": t_recv,
        "t_robot_done": t_done
    })

@app.route('/api/camera', methods=['POST'])
def control_camera():
    t_recv = time.time() * 1000.0
    if not px:
        return jsonify({"status": "error", "message": "Picarx not initialized"}), 500

    data = request.json or {}
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
        "t_robot_received": t_recv,
        "t_robot_done": t_done
    })

@app.route('/api/camera_switch', methods=['POST'])
def camera_switch():
    global camera_started
    data = request.json or {}
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
            "grayscale": sensor_data["grayscale"],
            "cpu_temp": cpu_temp,
            "cpu_usage": cpu_usage,
            "memory_usage": mem_usage
        }
    })

@app.route('/api/execute', methods=['POST'])
def execute_code():
    data = request.json or {}
    code = data.get("code", "")
    
    if not code.strip():
        return jsonify({"status": "success", "output": ""})
        
    # Redirect stdout to capture print statements
    old_stdout = sys.stdout
    redirected_output = sys.stdout = StringIO()
    
    success = True
    error_msg = ""
    
    try:
        # We use exec to run the code within our global namespace
        exec(code, exec_globals)
    except Exception as e:
        success = False
        import traceback
        traceback.print_exc(file=sys.stdout)
    finally:
        sys.stdout = old_stdout
        
    output = redirected_output.getvalue()
    
    return jsonify({
        "status": "success" if success else "error",
        "output": output
    })

if __name__ == '__main__':
    # Bind to all interfaces on port 5000
    app.run(host='0.0.0.0', port=5000, debug=False)
