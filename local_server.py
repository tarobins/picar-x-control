from flask import Flask, render_template, request, jsonify
import requests
import picar_client
import sys
import os
import argparse
import time
import json

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
    data = request.json or {}
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
    data = request.json or {}
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
    data = request.json or {}
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
    data = request.json or {}
    activate = data.get("active", True)
    try:
        r = requests.post(f"{picar_client.BASE_URL}/api/camera_switch", json={"active": activate}, timeout=3)
        if r.status_code == 200:
            return jsonify(r.json())
    except Exception as e:
        pass
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    telemetry = picar_client.get_telemetry()
    if telemetry is not None:
        return jsonify({"status": "success", "telemetry": telemetry})
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

@app.route('/api/execute', methods=['POST'])
def execute_code():
    data = request.json or {}
    code = data.get("code", "")
    res = picar_client.run_remote(code)
    if res:
        return jsonify(res)
    return jsonify({"status": "error", "message": "Robot connection failed"}), 503

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
