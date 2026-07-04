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

# Simulation Engine Variables
sim_x = 100.0  # Center of 200x200 arena
sim_y = 150.0
sim_heading = -90.0  # Pointing North
sim_pan = 0
sim_tilt = 0

sim_target_x = 150.0
sim_target_y = 50.0

sim_obstacles = [
    {"type": "circle", "cx": 60, "cy": 100, "r": 20},
    {"type": "box", "x1": 120, "y1": 110, "x2": 150, "y2": 140},
    {"type": "box", "x1": 30, "y1": 40, "x2": 80, "y2": 60}
]

def cast_simulation_ray(x, y, heading, pan):
    import math
    angle = math.radians(heading + pan)
    for d in range(1, 250):
        rx = x + d * math.cos(angle)
        ry = y + d * math.sin(angle)
        if rx <= 0 or rx >= 200 or ry <= 0 or ry >= 200:
            return float(d)
        for obs in sim_obstacles:
            if obs["type"] == "circle":
                dx = rx - obs["cx"]
                dy = ry - obs["cy"]
                if dx*dx + dy*dy <= obs["r"]*obs["r"]:
                    return float(d)
            elif obs["type"] == "box":
                if obs["x1"] <= rx <= obs["x2"] and obs["y1"] <= ry <= obs["y2"]:
                    return float(d)
    return 250.0

def generate_simulation_frame(x, y, heading, pan, target_x, target_y, target_name):
    from PIL import Image, ImageDraw
    import math
    
    img = Image.new("RGB", (320, 240), "#1e1e24")
    draw = ImageDraw.Draw(img)
    
    ox, oy = 10, 20
    
    # Draw Arena boundary
    draw.rectangle([ox, oy, ox + 200, oy + 200], outline="#ffffff", width=2)
    
    # Draw Obstacles
    for obs in sim_obstacles:
        if obs["type"] == "circle":
            cx, cy, r = obs["cx"], obs["cy"], obs["r"]
            draw.ellipse([ox + cx - r, oy + cy - r, ox + cx + r, oy + cy + r], fill="#ff4757", outline="#ff6b81")
        elif obs["type"] == "box":
            draw.rectangle([ox + obs["x1"], oy + obs["y1"], ox + obs["x2"], oy + obs["y2"]], fill="#ff4757", outline="#ff6b81")
            
    # Draw Target
    tx, ty = target_x, target_y
    draw.ellipse([ox + tx - 6, oy + ty - 6, ox + tx + 6, oy + ty + 6], fill="#ffa502", outline="#ff7f50")
    draw.text((ox + tx + 8, oy + ty - 8), target_name.upper(), fill="#ffa502")
    
    # Draw Robot
    rad = math.radians(heading)
    size = 8
    p1 = (ox + x + size * math.cos(rad), oy + y + size * math.sin(rad))
    p2 = (ox + x + size * math.cos(rad + 2.5), oy + y + size * math.sin(rad + 2.5))
    p3 = (ox + x + size * math.cos(rad - 2.5), oy + y + size * math.sin(rad - 2.5))
    draw.polygon([p1, p2, p3], fill="#1e90ff", outline="#70a1ff")
    
    # Raycast
    ray_dist = cast_simulation_ray(x, y, heading, pan)
    ray_angle = math.radians(heading + pan)
    rx = x + ray_dist * math.cos(ray_angle)
    ry = y + ray_dist * math.sin(ray_angle)
    draw.line([ox + x, oy + y, ox + rx, oy + ry], fill="#2ed573", width=1)
    
    # Text Panel
    draw.text((220, 20), "SIM AUTOPILOT", fill="#2ed573")
    draw.text((220, 40), "MODE: ACTIVE", fill="#888888")
    draw.text((220, 65), f"Robot X: {x:.1f}", fill="#ffffff")
    draw.text((220, 80), f"Robot Y: {y:.1f}", fill="#ffffff")
    draw.text((220, 95), f"Heading: {heading:.0f} deg", fill="#ffffff")
    draw.text((220, 115), f"Target: {target_name.upper()}", fill="#ffa502")
    draw.text((220, 130), f"Dist: {ray_dist:.1f} cm", fill="#2ed573")
    draw.text((220, 150), f"Gimbal P: {pan}", fill="#1e90ff")
    
    import io
    output_bytes = io.BytesIO()
    img.save(output_bytes, format='JPEG')
    return output_bytes.getvalue()

def update_sim_coordinates(action, speed, steering_angle):
    global sim_x, sim_y, sim_heading
    import math
    dt = 0.5
    if action == "forward":
        rad = math.radians(sim_heading)
        sim_x += (speed * 0.15 * dt) * math.cos(rad)
        sim_y += (speed * 0.15 * dt) * math.sin(rad)
    elif action == "backward":
        rad = math.radians(sim_heading)
        sim_x -= (speed * 0.15 * dt) * math.cos(rad)
        sim_y -= (speed * 0.15 * dt) * math.sin(rad)
        
    if action in ("forward", "backward"):
        direction_multiplier = 1 if action == "forward" else -1
        sim_heading += steering_angle * 0.25 * direction_multiplier * dt
        sim_heading = (sim_heading + 180) % 360 - 180

app = Flask(__name__, template_folder='templates')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    global sim_pan, sim_tilt
    try:
        status = picar_client.get_status()
        if status:
            return jsonify(status)
    except:
        pass
    return jsonify({
        "status": "success",
        "state": {
            "speed": 0,
            "steering_angle": 0,
            "pan_angle": sim_pan,
            "tilt_angle": sim_tilt,
            "camera_active": True,
            "simulation_mode": True
        }
    })

@app.route('/api/move', methods=['POST'])
def move_car():
    t_recv = time.time() * 1000.0
    data = request.get_json(silent=True) or {}
    action = data.get("action", "stop")
    speed = data.get("speed", 50)
    steering_angle = data.get("steering_angle", 0)
    
    try:
        t_sent = time.time() * 1000.0
        res = picar_client.move(action, speed, steering_angle)
        t_back = time.time() * 1000.0
        if res and isinstance(res, dict):
            res["t_proxy_received"] = t_recv
            res["t_proxy_sent"] = t_sent
            res["t_proxy_back"] = t_back
            return jsonify(res)
    except:
        pass
        
    update_sim_coordinates(action, speed, steering_angle)
    return jsonify({
        "status": "success",
        "state": {
            "speed": speed,
            "steering_angle": steering_angle,
            "pan_angle": sim_pan,
            "tilt_angle": sim_tilt,
            "camera_active": True,
            "simulation_mode": True
        }
    })

@app.route('/api/camera', methods=['POST'])
def control_camera():
    global sim_pan, sim_tilt
    t_recv = time.time() * 1000.0
    data = request.get_json(silent=True) or {}
    pan = data.get("pan")
    tilt = data.get("tilt")
    
    try:
        t_sent = time.time() * 1000.0
        res = picar_client.set_camera(pan, tilt)
        t_back = time.time() * 1000.0
        if res and isinstance(res, dict):
            res["t_proxy_received"] = t_recv
            res["t_proxy_sent"] = t_sent
            res["t_proxy_back"] = t_back
            return jsonify(res)
    except:
        pass
        
    if pan is not None:
        sim_pan = int(pan)
    if tilt is not None:
        sim_tilt = int(tilt)
    return jsonify({
        "status": "success",
        "pan": sim_pan,
        "tilt": sim_tilt,
        "simulation_mode": True
    })

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
    return jsonify({"status": "success", "camera_active": activate, "simulation_mode": True})

@app.route('/api/camera/frame', methods=['GET'])
def get_camera_frame():
    try:
        r = requests.get(f"{picar_client.BASE_URL}/api/camera/frame", timeout=3)
        if r.status_code == 200:
            return r.content, 200, {'Content-Type': 'image/jpeg'}
    except Exception as e:
        pass
        
    try:
        frame_data = generate_simulation_frame(sim_x, sim_y, sim_heading, sim_pan, sim_target_x, sim_target_y, ai_target_objective)
        return frame_data, 200, {'Content-Type': 'image/jpeg'}
    except Exception as err:
        return jsonify({"status": "error", "message": "Simulation frame generation failed: " + str(err)}), 500

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
    return jsonify({"status": "success", "imu_enabled": activate, "simulation_mode": True})

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    try:
        r = requests.get(f"{picar_client.BASE_URL}/api/telemetry", timeout=3)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        pass
        
    ray_dist = cast_simulation_ray(sim_x, sim_y, sim_heading, sim_pan)
    return jsonify({
        "status": "success",
        "accel_x": 0.0,
        "accel_y": 0.0,
        "accel_z": 9.81,
        "imu_enabled": True,
        "state": "IDLE",
        "collision_active": False,
        "telemetry": {
            "distance": ray_dist,
            "camera_distance": ray_dist + 5.0,
            "grayscale": [1200, 1200, 1200],
            "battery_voltage": 7.8,
            "stall_triggered": False,
            "cpu_temp": 42,
            "cpu_usage": 15,
            "memory_usage": 32,
            "accel_x": 0.0,
            "accel_y": 0.0,
            "accel_z": 9.81,
            "state": "IDLE",
            "collision_active": False,
            "logs": [{"time": time.strftime("%H:%M:%S"), "message": "Autopilot running in simulator mode", "level": "info"}]
        }
    })

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
ai_decision_memory = []

def ai_driver_loop():
    global ai_drive_active, ai_drive_key, ai_logs, ai_decision_memory
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
            is_collision_active = tel_data.get('collision_active', False)
            sensor_prompt = (
                f"You are the high-end autonomous AI driver for the SunFounder PiCar-X robot.\n"
                f"Objective: '{ai_target_objective}'\n\n"
                f"Driving Logic:\n"
                f"- If target found: steer towards it, stop under 30cm, set speak to announce completion, stop.\n"
                f"- If target not visible: pan/tilt camera (gimbal_pan, gimbal_tilt) to scan, or drive to search.\n"
                f"- If 'explore': safely avoid obstacles and keep moving.\n\n"
                f"Safety Instructions (CRITICAL):\n"
                f"- Collision Active is {is_collision_active}. If true, a safety lock is active. You MUST stop ('stop') or reverse ('backward') to clear the lock! Do NOT drive forward or steer until cleared.\n"
                f"- If ultrasonic or camera distance is < 30cm, you are blocked! Immediately stop, steer away, or reverse.\n\n"
                f"History:\n"
                f"{json.dumps(ai_decision_memory, indent=2)}\n\n"
                f"Sensors:\n"
                f"- Collision Active: {is_collision_active}\n"
                f"- Ultrasonic: {tel_data.get('distance', -1)} cm\n"
                f"- Camera distance: {tel_data.get('camera_distance', -1)} cm\n"
                f"- Grayscale: {tel_data.get('grayscale', [0,0,0])}\n"
                f"- Battery: {tel_data.get('battery_voltage', 0.0)}V\n"
                f"- Accel: X={tel_data.get('accel_x', 0)}, Y={tel_data.get('accel_y', 0)}\n\n"
                f"LATENCY OPTIMIZATION: Keep 'reasoning' under 12 words. Keep 'speak' under 8 words. Brevity is critical for fast turns!"
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
                    "maxOutputTokens": 150,
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
                
                # Append to decision memory
                ai_decision_memory.append({
                    "time": time.strftime("%H:%M:%S"),
                    "action": action,
                    "speed": speed,
                    "steering_angle": steering_angle,
                    "reasoning": reasoning
                })
                if len(ai_decision_memory) > 5:
                    ai_decision_memory.pop(0)
                
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
                err_msg = f"Gemini API Error {resp.status_code}"
                backoff_time = 5.0
                
                if resp.status_code == 429:
                    err_msg = "Gemini API Quota Exceeded (429). Please wait for rate limit reset."
                    backoff_time = 15.0
                    try:
                        err_json = resp.json()
                        for detail in err_json.get("error", {}).get("details", []):
                            if "retryDelay" in detail:
                                delay_str = detail["retryDelay"]
                                backoff_time = float(delay_str.rstrip("s")) + 1.0
                                break
                    except:
                        pass
                
                print(f"[AI Driver] {err_msg}: {resp.text}")
                
                log_entry = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "action": "stop",
                    "speed": 0,
                    "steering_angle": 0,
                    "reasoning": f"ERROR: {err_msg} (Retrying in {int(backoff_time)}s)",
                    "speak": "Rate limit hit. Pausing.",
                    "latency_ms": latency,
                    "sensors": {
                        "ultrasonic": tel_data.get('distance', -1),
                        "camera_distance": tel_data.get('camera_distance', -1),
                        "cliff_grayscale": tel_data.get('grayscale', [0,0,0]),
                        "battery": tel_data.get('battery_voltage', 0.0)
                    }
                }
                ai_logs.append(log_entry)
                if len(ai_logs) > 50:
                    ai_logs.pop(0)
                
                # Stop car immediately on API failure
                try:
                    requests.post(f"{picar_client.BASE_URL}/api/move", json={"action": "stop"}, timeout=3)
                except:
                    pass
                
                time.sleep(backoff_time)
                
        except Exception as e:
            err_msg = f"Autopilot Error: {str(e)}"
            print(f"[AI Driver] {err_msg}")
            
            log_entry = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "action": "stop",
                "speed": 0,
                "steering_angle": 0,
                "reasoning": err_msg,
                "speak": "An error occurred.",
                "latency_ms": 0,
                "sensors": {}
            }
            ai_logs.append(log_entry)
            if len(ai_logs) > 50:
                ai_logs.pop(0)
                
            try:
                requests.post(f"{picar_client.BASE_URL}/api/move", json={"action": "stop"}, timeout=3)
            except:
                pass
                
            time.sleep(5.0)
            
        time.sleep(0.05)
        
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
