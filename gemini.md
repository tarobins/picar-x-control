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

For the most efficient and robust startup, a management script `start_picar.sh` is provided in the repository root. This script automates code deployment, remote server lifecycle management, and local SSH tunneling.

### Using the Management Script (`start_picar.sh`)

1. **Start/Run (Recommended)**:
   This command checks if the server is already running and the tunnel is open. If so, it exits instantly. Otherwise, it syncs the code, restarts the remote server, establishes the SSH tunnel, and verifies the connection (polling for up to 35 seconds to allow the camera to initialize).
   ```bash
   ./start_picar.sh start
   # or simply
   ./start_picar.sh
   ```
   *To skip code deployment and only start/verify services, use:*
   ```bash
   ./start_picar.sh start --skip-sync
   ```

2. **Restart**:
   Forces a clean deployment and restart of all services (remote server and local tunnel).
   ```bash
   ./start_picar.sh restart
   ```

3. **Status Check**:
   Checks the status of the local SSH tunnel and the remote server process.
   ```bash
   ./start_picar.sh status
   ```

4. **Stop**:
   Gracefully stops the local SSH tunnel and the remote server.
   ```bash
   ./start_picar.sh stop
   ```

5. **Manual Code Sync**:
   Syncs the local workspace to the robot without restarting services.
   ```bash
   ./start_picar.sh sync
   ```

### Manual Setup (Reference)

If you need to perform the steps manually:

1. **Deploying changes**: Run `rsync` from the local workspace:
   ```bash
   rsync -avz --no-perms --no-owner --no-group --exclude='.lgd-nfy0' --exclude='build' --exclude='picar_x.egg-info' --exclude='.git' /path/to/local/workspace/ robot:~/picar-x/
   ```
2. **Starting/Restarting the server**:
   On the robot, ensure any existing server is stopped, wait a moment for the camera device to be freed by the kernel, and start the new server:
   ```bash
   ssh robot "pkill -f server.py"
   sleep 1
   ssh robot "python3 ~/picar-x/server.py"
   ```
3. **Opening the SSH tunnel**:
   On the development machine, forward ports 5000 and 9000:
   ```bash
   ssh -L 127.0.0.1:5000:127.0.0.1:5000 -L 127.0.0.1:9000:127.0.0.1:9000 -f -N robot
   ```
   *Note: Using 127.0.0.1 both locally and remotely prevents potential IPv6 binding and resolution issues on the robot (where localhost might resolve to ::1 but Flask only listens on IPv4).*

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

---

## 6. Recent Optimizations & Tuning (June 12, 2026)

To fix command backlog and lag spikes over congested 2.4 GHz Wi-Fi, the following system-wide upgrades were made:

### A. Network Tunnel Splitting (Head-of-Line Blocking Fix)
* **Problem**: Forwarding the heavy MJPEG Camera Stream (port 9000) and the Control API (port 5001) over the *same* SSH connection caused TCP head-of-line blocking. Video packets saturated the TCP buffer, locking driving commands behind them.
* **Fix**: Separated the tunnels in `start_picar.sh` into two independent SSH processes:
  - **API Tunnel**: Started with `-o IPQoS=lowdelay` (router packet prioritization) and `-o Compression=no` (drops compression lag).
  - **Video Stream Tunnel**: Transports video data independently.
  - *Result*: Slashed worst-case roundtrip delay from **8.9 seconds down to 82ms**.

### B. Single-Threaded I2C Locking & Sensor Caching
* **Problem**: Simultaneous I2C/GPIO calls to `px.get_distance()` and motors/servos across multiple Flask threads caused resource conflicts (Remote I/O errors) and blocked execution paths.
* **Fix**:
  - Centralized all physical sensor reads (`get_distance` and `get_grayscale`) inside the background `safety_watchdog` thread running at a steady 50ms interval.
  - Stored readings in a thread-safe cache (`sensor_data`).
  - Wrapped all `px` hardware interactions in `server.py` inside a global mutex `i2c_lock = threading.Lock()`.
  - *Result*: Average robot hardware execution time dropped to just **6ms**.

### C. Inline Telemetry & Polling Back-off
* **Problem**: Background telemetry polling from the client every 350ms clogged the network channel.
* **Fix**:
  - Telemetry is now returned inline in the JSON responses of `/api/move` and `/api/camera`.
  - The client's background telemetry polling interval was backed off from 350ms to **1500ms** (a 76% traffic reduction).
  - *Result*: The Control API channel remains idle until a command is sent, giving commands immediate priority.

### D. Latency Tracing & Lost-Request Watchdog
* **Timeout Watchdog**: The client now enforces a `1.5-second` timeout on driving/gimbal fetches. If a command is lost or hung, the client aborts it and logs a `timeout` trace.
* **`latency_trace.log`**: Every completed, timed-out, or failed command logs a timing trace record (client sent/recv, proxy recv/sent/back, robot recv/done).
* **`analyze_latency.py`**: A local analysis utility tool parses the trace logs and outputs average, max, min, and percentage breakdowns for segment latencies, highlighting delayed/lost commands. Run it locally via:
  ```bash
  ./analyze_latency.py
  ```

### E. Camera Gimbal Zero-Calibration (Tare)
* **Features**: Added "Set Zero" and "Reset" buttons to the camera gimbal UI panel.
* **Client-Side Offset System**:
  - Clicking **Set Zero** establishes the current physical angles as the relative `0°` origin (stored in `localStorage` for session persistence).
  - The UI trackpad and arrow keys compute angles relative to this zero point while translating them to correct absolute physical coordinates sent to the robot.
  - A dashed purple circular target (`gimbal-zero-indicator`) appears on the pad to visually mark the calibrated zero coordinate relative to the physical servo center.
  - **Hardware boundary clipping**: The UI dot accurately bounds itself within the actual physical limits (`[-90, 90]` for Pan, `[-35, 65]` for Tilt) regardless of the calibration offset.
