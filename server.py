from flask import Flask, request, jsonify, render_template
from picarx import Picarx
from vilib import Vilib
import time
import sys
import os
import psutil
import threading
from io import StringIO

app = Flask(__name__)

# Initialize PiCar-X
try:
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

# Safety Watchdog variables
last_move_time = time.time()

def safety_watchdog():
    global last_move_time, state
    while True:
        time.sleep(0.05) # Check every 50ms for collision / heartbeat
        if px:
            # 1. Collision prevention (Auto-Brake)
            try:
                distance = px.get_distance()
            except:
                distance = -1

            if state["direction"] == "forward" and distance > 0:
                if distance < 10:
                    print(f"Watchdog Auto-Brake: Obstacle at {distance:.1f}cm. Stopping!")
                    px.stop()
                    state["speed"] = 0
                    state["direction"] = "stop"
                elif distance < 40:
                    target_speed = state["speed"]
                    min_speed = 25
                    if target_speed > min_speed:
                        scaled = min_speed + (target_speed - min_speed) * (distance - 10) / (40 - 10)
                        scaled = int(max(min_speed, min(target_speed, scaled)))
                        px.forward(scaled)

            # 2. Watchdog timeout check (Heartbeat)
            if state["speed"] > 0:
                if time.time() - last_move_time > 1.0:
                    print("WATCHDOG TRIGGERED: No heartbeat received for 1s. Halting robot!")
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        "status": "success",
        "state": state
    })

@app.route('/api/move', methods=['POST'])
def move_car():
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
    
    px.set_dir_servo_angle(steering_angle)
    
    # Reset watchdog timer for movement actions
    if action == "forward":
        try:
            distance = px.get_distance()
        except:
            distance = -1
            
        if 0 < distance < 10:
            px.stop()
            state["speed"] = 0
            state["direction"] = "stop"
            return jsonify({"status": "blocked", "message": "Obstacle in front!", "state": state})
            
        last_move_time = time.time()
        state["direction"] = "forward"
        # If very close, scale speed immediately
        if 10 <= distance < 40:
            min_speed = 25
            speed = int(max(min_speed, min(speed, min_speed + (speed - min_speed) * (distance - 10) / (40 - 10))))
        px.forward(speed)
        
    elif action == "backward":
        last_move_time = time.time()
        state["direction"] = "backward"
        px.backward(speed)
        
    elif action == "stop":
        px.stop()
        state["speed"] = 0
        state["direction"] = "stop"
        
    else:
        # Just update steering or speed, still update watchdog to prevent stop if dragging sliders
        last_move_time = time.time()
        
    return jsonify({"status": "success", "state": state})

@app.route('/api/camera', methods=['POST'])
def control_camera():
    if not px:
        return jsonify({"status": "error", "message": "Picarx not initialized"}), 500

    data = request.json or {}
    pan = data.get("pan")
    tilt = data.get("tilt")
    
    if pan is not None:
        pan = max(-90, min(90, int(pan)))
        px.set_cam_pan_angle(pan)
        state["pan_angle"] = pan
        
    if tilt is not None:
        tilt = max(-35, min(65, int(tilt)))
        px.set_cam_tilt_angle(tilt)
        state["tilt_angle"] = tilt
        
    return jsonify({"status": "success", "state": state})

@app.route('/api/camera_switch', methods=['POST'])
def camera_switch():
    global camera_started
    data = request.json or {}
    activate = data.get("active", True)
    
    try:
        if activate and not camera_started:
            Vilib.camera_start(vflip=False, hflip=False)
            Vilib.display(local=False, web=True)
            camera_started = True
        elif not activate and camera_started:
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
        
    try:
        distance = round(px.get_distance(), 1)
    except Exception as e:
        distance = -1.0
        
    try:
        grayscale = px.get_grayscale_data()
    except Exception as e:
        grayscale = [0, 0, 0]
        
    # Read system info
    cpu_temp = get_cpu_temp()
    cpu_usage = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    mem_usage = mem.percent
    
    return jsonify({
        "status": "success",
        "telemetry": {
            "distance": distance,
            "grayscale": grayscale,
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
