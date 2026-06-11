# Antigravity (agy) Project Memory: PiCar-X Control Center

This file provides system information, setup instructions, and design patterns for future Antigravity sessions working on this codebase.

---

## 1. Project Overview
A custom, low-latency, web-based control center and client scripting library for the **SunFounder PiCar-X** robot (Raspberry Pi-based RC car).

```
[Local Browser / Client Scripts] <--- (SSH Tunnel: 5000/9000) ---> [PiCar-X Robot]
      - Dashboard UI                                                - Flask Server (5000)
      - Python scripts (picar_client.py)                             - Vilib Stream (9000)
```

---

## 2. Device Profile
* **Hostname / IP**: `robot` (`192.168.69.141.lan` on GL.iNet router network)
* **OS**: Debian GNU/Linux 13 (trixie) on Raspberry Pi (`aarch64`)
* **SSH Access**: Passwordless SSH configured as user `tarobins`
* **Python Environment**: Dependencies `flask`, `psutil`, and `requests` are globally installed on the robot.

---

## 3. Project Structure
The root of the Git repository contains ONLY our custom command center code, while the SunFounder library is ignored in a subdirectory:
* **`server.py`**: The Flask application running on the robot. Manages hardware commands, reads sensors, and hosts the persistent python evaluation API.
* **`templates/index.html`**: A glassmorphic web dashboard (WASD controls, drag-to-aim gimbal, terminal console, and live telemetry).
* **`picar_client.py`**: A local Python API client library to write scripts that control the car over HTTP.
* **`picar-x-lib/`**: Ignored by Git. Contains the original SunFounder library code and example scripts.

---

## 4. How to Set Up & Run the Project

### On the Robot (Device Deployment)
The project code is synchronized to the robot at `~/picar-x/` (`/home/tarobins/picar-x/`).

1. **Deploying changes**: Run `rsync` from the local workspace:
   ```bash
   rsync -avz --no-perms --no-owner --no-group --exclude='.lgd-nfy0' --exclude='build' --exclude='picar_x.egg-info' --exclude='.git' /path/to/local/workspace/ robot:~/picar-x/
   ```
2. **Starting the server**: Run standard Python without sudo (the `picarx` library handles GPIO privileges internally):
   ```bash
   ssh robot "python3 ~/picar-x/server.py"
   ```
3. **Restarting the server**: Flask caches templates in memory because `debug=False` is set. If template or python code changes, you must kill the old process first:
   ```bash
   ssh robot "pkill -f server.py"
   ssh robot "python3 ~/picar-x/server.py"
   ```

### On the Development Machine (Antigravity Environment)
Since the agent container/VM network doesn't map port `5000` or `9000` directly to the robot's local subnet, you **must set up SSH port forwarding**:

1. **Open the tunnel** in the background:
   ```bash
   ssh -L 5000:localhost:5000 -L 9000:localhost:9000 -f -N robot
   ```
2. **Test connection**:
   - Status API check: `curl http://localhost:5000/api/status`
   - Client check: `python3 picar_client.py` (performs connection, telemetry query, and camera wiggle test)
   - Browser check: Open **[http://localhost:5000](http://localhost:5000)** on the host computer.

---

## 5. Failsafes and Design Patterns (CRITICAL)

To protect the physical robot from crashing into walls, falling off desks, or overheating:

* **Startup Auto-Stop**: `server.py` calls `px.stop()` immediately during `Picarx()` initialization so the motors start powered down.
* **Safety Watchdog Daemon**: A background thread runs on the server. If the car is driving (`speed > 0`) and doesn't receive a movement API call or heartbeat for **1.0 second**, it immediately halts the motors.
* **Auto-Braking & Collision Prevention (Front)**: 
  - **Capped Deceleration**: If the car is moving forward and the ultrasonic sensor detects an obstacle between **10cm and 40cm**, the watchdog dynamically scales down the speed to a safe minimum of 25.
  - **Obstacle Halt**: If the distance drops below **10cm**, the watchdog cuts motor power immediately.
  - **Forward Command Block**: If the distance in front of the car is under **10cm**, the `/api/move` endpoint blocks any new forward commands, returning a blocked warning, while still allowing steering and reverse movements.


* **Web Client Heartbeats**: The web client dashboard runs a 250ms interval loop to send periodic drive heartbeats while keys are held down.
* **Window Focus Loss Safe-Stop**: If the browser tab loses focus or is minimized, a window `blur` listener automatically clears active key states and tells the server to `stop`.
* **On-Demand Camera**: Camera streaming is disabled by default (`"camera_active": false`) to prevent latency and CPU starvation. It is toggled on/off dynamically using Vilib's `camera_start()` / `camera_close()` over the `/api/camera_switch` endpoint.
