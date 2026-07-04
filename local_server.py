from flask import Flask, render_template, request, jsonify
import requests
import picar_client
import sys
import os
import argparse
import time
import json
import socket
import urllib3
import threading
import base64

# Optimize TCP connection socket settings (Disable Nagle's algorithm)
urllib3.connection.HTTPConnection.default_socket_options += [
    (socket.SOL_TCP, socket.TCP_NODELAY, 1)
]

app = Flask(__name__, template_folder='templates')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    status = picar_client.get_status()
    if status:
        return jsonify(status)
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

@app.route('/api/move', methods=['POST'])
def move_car():
    t_recv = time.time() * 1000.0
    data = request.get_json(silent=True) or {}
    action = data.get("action", "stop")
    speed = data.get("speed", 50)
    steering_angle = data.get("steering_angle", 0)
    
    t_sent = time.time() * 1000.0
    res = picar_client.move(action, speed, steering_angle)
    t_back = time.time() * 1000.0
    
    if res and isinstance(res, dict):
        res["t_proxy_received"] = t_recv
        res["t_proxy_sent"] = t_sent
        res["t_proxy_back"] = t_back
        return jsonify(res)
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

@app.route('/api/camera', methods=['POST'])
def control_camera():
    t_recv = time.time() * 1000.0
    data = request.get_json(silent=True) or {}
    pan = data.get("pan")
    tilt = data.get("tilt")
    
    t_sent = time.time() * 1000.0
    res = picar_client.set_camera(pan, tilt)
    t_back = time.time() * 1000.0
    
    if res and isinstance(res, dict):
        res["t_proxy_received"] = t_recv
        res["t_proxy_sent"] = t_sent
        res["t_proxy_back"] = t_back
        return jsonify(res)
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

@app.route('/api/trace', methods=['POST'])
def log_trace():
    data = request.get_json(silent=True) or {}
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'latency_trace.log')
    try:
        with open(log_path, 'a') as f:
            f.write(json.dumps(data) + '\n')
        return jsonify({"status": "success"})
    except Exception as e:
        sys.stderr.write(f"Error writing to trace log: {e}\n")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/camera_switch', methods=['POST'])
def camera_switch():
    data = request.get_json(silent=True) or {}
    activate = data.get("active", True)
    try:
        r = requests.post(f"{picar_client.BASE_URL}/api/camera_switch", json={"active": activate}, timeout=3)
        if r.status_code == 200:
            return jsonify(r.json())
    except Exception as e:
        pass
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

@app.route('/api/camera/frame', methods=['GET'])
def get_camera_frame():
    try:
        r = requests.get(f"{picar_client.BASE_URL}/api/camera/frame", timeout=3)
        if r.status_code == 200:
            return r.content, 200, {'Content-Type': 'image/jpeg'}
    except Exception as e:
        pass
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

@app.route('/api/imu_switch', methods=['POST'])
def imu_switch():
    data = request.get_json(silent=True) or {}
    activate = data.get("active", True)
    try:
        r = requests.post(f"{picar_client.BASE_URL}/api/imu_switch", json={"active": activate}, timeout=3)
        if r.status_code == 200:
            return jsonify(r.json())
    except Exception as e:
        pass
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    try:
        r = requests.get(f"{picar_client.BASE_URL}/api/telemetry", timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": "Robot connection failed: " + str(e)}), 503

@app.route('/api/execute', methods=['POST'])
def execute_code():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    res = picar_client.run_remote(code)
    if res:
        return jsonify(res)
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

@app.route('/api/map/data', methods=['GET'])
def get_map_telemetry():
    try:
        r = requests.get(f"{picar_client.BASE_URL}/api/map/data", params=request.args, timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

@app.route('/api/explore/start', methods=['POST'])
def start_explore():
    try:
        r = requests.post(f"{picar_client.BASE_URL}/api/explore/start", json=request.get_json(silent=True) or {}, timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

@app.route('/api/explore/stop', methods=['POST'])
def stop_explore():
    try:
        r = requests.post(f"{picar_client.BASE_URL}/api/explore/stop", json=request.get_json(silent=True) or {}, timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

@app.route('/api/map/reset', methods=['POST'])
def reset_map():
    try:
        r = requests.post(f"{picar_client.BASE_URL}/api/map/reset", json=request.get_json(silent=True) or {}, timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503


@app.route('/api/calibrate/steering', methods=['POST'])
def calibrate_steering():
    try:
        r = requests.post(f"{picar_client.BASE_URL}/api/calibrate/steering", json=request.get_json(silent=True) or {}, timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

@app.route('/api/calibrate/read_sensors', methods=['GET'])
def read_sensors_for_calibration():
    try:
        r = requests.get(f"{picar_client.BASE_URL}/api/calibrate/read_sensors", timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

@app.route('/api/calibrate/camera', methods=['POST'])
def calibrate_camera():
    try:
        r = requests.post(f"{picar_client.BASE_URL}/api/calibrate/camera", json=request.get_json(silent=True) or {}, timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

@app.route('/api/calibrate/save', methods=['POST'])
def save_calibration():
    try:
        r = requests.post(f"{picar_client.BASE_URL}/api/calibrate/save", json=request.get_json(silent=True) or {}, timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

@app.route('/api/calibrate/config', methods=['GET'])
def get_calibration_config():
    try:
        r = requests.get(f"{picar_client.BASE_URL}/api/calibrate/config", timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

@app.route('/api/calibrate/imu_auto', methods=['POST'])
def trigger_auto_imu_calibration():
    try:
        # Increase timeout because physical calibration drives and turns (about 2.5 seconds total runtime)
        r = requests.post(f"{picar_client.BASE_URL}/api/calibrate/imu_auto", timeout=10)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503

ai_drive_active = False
ai_drive_key = ""
ai_drive_thread = None
ai_logs = []
ai_target_objective = "explore and look around"

def ai_driver_loop():
    global ai_drive_active, ai_drive_key, ai_logs
    print("[AI Driver] Started background driving thread.")
    
    while ai_drive_active:
        try:
            # 1. Grab telemetry
            telemetry_url = f"{picar_client.BASE_URL}/api/telemetry"
            r_tel = requests.get(telemetry_url, timeout=3)
            tel_data = {}
            if r_tel.status_code == 200:
                tel_data = r_tel.json().get("telemetry", {})
            
            # 2. Grab frame
            frame_url = f"{picar_client.BASE_URL}/api/camera/frame"
            r_frame = requests.get(frame_url, timeout=3)
            if r_frame.status_code != 200:
                print("[AI Driver] Could not fetch camera frame. Retrying...")
                time.sleep(1.0)
                continue
                
            frame_b64 = base64.b64encode(r_frame.content).decode('utf-8')
            
            # 3. Formulate the prompt
            sensor_prompt = (
                f"You are the high-end autonomous AI driver for the SunFounder PiCar-X robot.\n"
                f"Your CURRENT Target Search Objective is: '{ai_target_objective}'\n\n"
                f"Guidelines for target search:\n"
                f"- If your target is found, steer towards it and stop when close (under 30cm), then set the speak field to announce 'Objective Complete! Found the [Target Name]!' and stop the car.\n"
                f"- If the target is NOT visible, use the camera gimbal (gimbal_pan, gimbal_tilt) to look left/right/up/down to search, or drive around to find it.\n"
                f"- If the target is 'explore and look around', just safely explore the room, avoid obstacles, identify objects, and keep moving.\n\n"
                f"Current Sensor Data:\n"
                f"- Ultrasonic Distance: {tel_data.get('distance', -1)} cm\n"
                f"- Camera Obstacle Distance: {tel_data.get('camera_distance', -1)} cm\n"
                f"- Grayscale cliff sensors: {tel_data.get('grayscale', [0,0,0])}\n"
                f"- Battery Voltage: {tel_data.get('battery_voltage', 0.0)}V\n"
                f"- IMU Acceleration: X={tel_data.get('accel_x', 0)}, Y={tel_data.get('accel_y', 0)}, Z={tel_data.get('accel_z', 0)}\n"
                f"- Current State: {tel_data.get('state', 'IDLE')}\n\n"
                f"Analyze the camera image and sensor readings, then respond with your driving decision.\n"
                f"Be smart: if an obstacle is close (under 30cm), turn or reverse. Do not drive into walls.\n"
                f"Provide a fun, commentary-style voice narration in the 'speak' field describing what you see and are doing (e.g. 'I see a wall, backing up now' or 'Searching for the cup')."
            )
            
            api_key = ai_drive_key or os.environ.get("GEMINI_API_KEY", "")
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": sensor_prompt},
                            {
                                "inlineData": {
                                    "mimeType": "image/jpeg",
                                    "data": frame_b64
                                }
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": {
                        "type": "OBJECT",
                        "properties": {
                            "action": {
                                "type": "STRING",
                                "enum": ["forward", "backward", "stop", "steer"]
                            },
                            "speed": {
                                "type": "INTEGER"
                            },
                            "steering_angle": {
                                "type": "INTEGER"
                            },
                            "gimbal_pan": {
                                "type": "INTEGER"
                            },
                            "gimbal_tilt": {
                                "type": "INTEGER"
                            },
                            "reasoning": {
                                "type": "STRING"
                            },
                            "speak": {
                                "type": "STRING"
                            }
                        },
                        "required": ["action", "speed", "steering_angle", "reasoning", "speak"]
                    }
                }
            }
            
            headers = {"Content-Type": "application/json"}
            t_start = time.time()
            resp = requests.post(gemini_url, json=payload, headers=headers, timeout=10)
            latency = int((time.time() - t_start) * 1000)
            
            if resp.status_code == 200:
                result = resp.json()
                text_response = result["candidates"][0]["content"]["parts"][0]["text"]
                decision = json.loads(text_response)
                
                action = decision.get("action", "stop")
                speed = min(80, max(0, decision.get("speed", 0)))
                steering_angle = min(30, max(-30, decision.get("steering_angle", 0)))
                gimbal_pan = decision.get("gimbal_pan")
                gimbal_tilt = decision.get("gimbal_tilt")
                reasoning = decision.get("reasoning", "")
                speak = decision.get("speak", "")
                
                # Execute drive
                move_url = f"{picar_client.BASE_URL}/api/move"
                requests.post(move_url, json={
                    "action": action,
                    "speed": speed,
                    "steering_angle": steering_angle
                }, timeout=3)
                
                # Execute camera adjustment
                if gimbal_pan is not None or gimbal_tilt is not None:
                    cam_url = f"{picar_client.BASE_URL}/api/camera"
                    requests.post(cam_url, json={
                        "pan": gimbal_pan,
                        "tilt": gimbal_tilt
                    }, timeout=3)
                
                log_entry = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "action": action,
                    "speed": speed,
                    "steering_angle": steering_angle,
                    "reasoning": reasoning,
                    "speak": speak,
                    "latency_ms": latency,
                    "sensors": {
                        "ultrasonic": tel_data.get('distance', -1),
                        "camera_distance": tel_data.get('camera_distance', -1),
                        "cliff_grayscale": tel_data.get('grayscale', [0,0,0]),
                        "battery": tel_data.get('battery_voltage', 0.0)
                    }
                }
                
                # Write to persistent log file
                ai_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_driver.log')
                try:
                    with open(ai_log_path, 'a') as f:
                        f.write(json.dumps(log_entry) + '\n')
                except Exception as log_err:
                    print(f"[AI Driver] Error writing log: {log_err}")

                ai_logs.append(log_entry)
                if len(ai_logs) > 50:
                    ai_logs.pop(0)
            else:
                print(f"[AI Driver] Gemini API error: {resp.status_code} - {resp.text}")
                time.sleep(2.0)
                
        except Exception as e:
            print(f"[AI Driver] Error in loop: {e}")
            time.sleep(2.0)
            
        time.sleep(1.0)
        
    try:
        requests.post(f"{picar_client.BASE_URL}/api/move", json={"action": "stop"}, timeout=3)
    except:
        pass
    print("[AI Driver] Stopped background driving thread.")

@app.route('/api/ai_drive/toggle', methods=['POST'])
def toggle_ai_drive():
    global ai_drive_active, ai_drive_key, ai_drive_thread
    data = request.get_json(silent=True) or {}
    active = data.get("active", False)
    key = data.get("api_key", "").strip()
    
    if active:
        if not key and not os.environ.get("GEMINI_API_KEY"):
            return jsonify({"status": "error", "message": "Gemini API Key is required"}), 400
        ai_drive_key = key
        ai_drive_active = True
        if ai_drive_thread is None or not ai_drive_thread.is_alive():
            ai_drive_thread = threading.Thread(target=ai_driver_loop, daemon=True)
            ai_drive_thread.start()
        return jsonify({"status": "success", "ai_drive_active": True})
    else:
        ai_drive_active = False
        return jsonify({"status": "success", "ai_drive_active": False})

@app.route('/api/ai_drive/target', methods=['POST'])
def set_ai_target():
    global ai_target_objective
    data = request.get_json(silent=True) or {}
    ai_target_objective = data.get("target", "explore and look around").strip()
    return jsonify({"status": "success", "target": ai_target_objective})

@app.route('/api/ai_drive/status', methods=['GET'])
def ai_drive_status():
    global ai_drive_active, ai_logs, ai_target_objective
    return jsonify({
        "status": "success",
        "ai_drive_active": ai_drive_active,
        "target": ai_target_objective,
        "logs": ai_logs
    })

def daemonize(log_file=None):
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        sys.stderr.write(f"Fork #1 failed: {e}\n")
        sys.exit(1)

    os.setsid()
    
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        sys.stderr.write(f"Fork #2 failed: {e}\n")
        sys.exit(1)

    sys.stdout.flush()
    sys.stderr.flush()

    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        so = open(log_file, 'a+')
        se = open(log_file, 'a+')
    else:
        so = open(os.devnull, 'a+')
        se = open(os.devnull, 'a+')

    si = open(os.devnull, 'r')
    os.dup2(si.fileno(), sys.stdin.fileno())
    os.dup2(so.fileno(), sys.stdout.fileno())
    os.dup2(se.fileno(), sys.stderr.fileno())

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--daemon', action='store_true', help='Run as daemon')
    parser.add_argument('--log-file', type=str, default=None, help='Log file for daemon mode')
    args = parser.parse_args()

    if args.daemon:
        daemonize(args.log_file)

    # Local webserver runs on 127.0.0.1:5000
    app.run(host='127.0.0.1', port=5000, debug=False)
