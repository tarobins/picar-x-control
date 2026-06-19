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

---

## 7. Recent Upgrades & Safety Guardrails (June 17, 2026)

To improve navigation reliability, resolve API edge-case exceptions, and safeguard the robot from mechanical stresses under stall conditions:

### A. Dynamic Cliff Calibration Direction
* **Problem**: Reflective floor grayscale readings vary, and cliff triggers can be either lower-than-threshold or higher-than-threshold depending on hardware calibrations.
* **Fix**: The watchdog compares `air_sample < floor_sample` from `~/data/calibration_config.json` on startup. 
  - If true, a cliff triggers when readings drop *below* the threshold (`all(val < cliff_threshold)`).
  - If false, it triggers when readings go *above* the threshold (`all(val > cliff_threshold)`).
  - Active in the autonomous explorer loop, safety watchdog, and `/api/move` input verification.

### B. Safe JSON HTTP Parsing (415 Fix)
* **Problem**: Accessing `request.json` inside Flask POST routes threw a `415 Unsupported Media Type` HTTP exception when clients sent requests without explicitly setting the `application/json` Content-Type header (e.g. clicking "Start SLAM" on the dashboard).
* **Fix**: Replaced all `request.json` references with `request.get_json(silent=True) or {}` across `local_server.py` and `server.py`.

### C. Speed-Adaptive Voltage Sag Stall Watchdog & I2C Glitch Filter
* **Problem**: Stalling the DC motors draws high current, which sags battery voltage, but the sag varies based on PWM duty cycle. Furthermore, motor running introduced electromagnetic interference (EMI) on the I2C bus, creating random reading spikes (e.g., `0.6V` or `113V`).
* **Fix**:
  - **I2C Noise Filter**: Filters out extreme out-of-bounds readings (`<6.0V` or `>9.0V`) and ignores sudden transient jumps/drops (`>0.15V` in 50ms), reusing the last valid voltage reading.
  - **Adaptive Threshold**: Dynamically scales the stall sag threshold based on throttle speed:
    ```python
    stall_sag_threshold = 0.05 + speed_factor * 0.07
    ```
    (At default speed `50`, the threshold is `0.076V` of sag; at speed `100`, it is `0.12V` of sag).
  - **Stall Action**: If the sag exceeds the threshold for 5 consecutive loops (250ms), the watchdog executes an emergency stop (`px.stop()`), stops autonomous SLAM paths, sets speed to `0`, and registers a global `stall_triggered = True` flag.

### D. Drive Deck Stall Warnings
* **Features**: Added a pulsing red `MOTOR STALLED` warning badge next to the Drive Deck title in the Web UI.
* **UX Alert**: When `stall_triggered` is true, the entire Drive Deck card borders in red and emits a pulsing red shadow glow to alert the operator. The state automatically clears once a new manual command or calibration instruction is sent.

### E. SLAM Map & Odometry Reset
* **Features**: Added a `Reset` button to the SLAM control grid in `templates/index.html`.
* **Flow**: Triggers `/api/map/reset` which calls `grid.reset_map()` (erasing the occupancy grid array in `mapping.py` and writing a blank file) and resets the robot's odometry tracking coordinates (`x`, `y`, `heading_deg`) back to `0`.

---

## 8. GY-521 IMU Real-Time Telemetry & Kinematic Auto-Orientation (June 18, 2026)

To support real-time 3-axis acceleration monitoring and auto-calibration:

### A. Core Library Installation
- Installs `mpu6050-raspberrypi` and its prerequisite hardware communication driver `smbus` on the robot's system Python runtime.
- Command to install manually on the robot:
  ```bash
  ssh robot "pip3 install mpu6050-raspberrypi smbus --break-system-packages"
  ```

### B. Auto-Orientation Kinematic Logic
Because the sensor can be physically mounted in any arbitrary layout, the calibration sequence dynamically determines the coordinate mapping:
1. **Vertical ($Z$)**: Samples stationary baseline; the axis closest to $9.81 \text{ m/s}^2$ is vertical ($Z$), resolving sign based on positive/negative direction.
2. **Longitudinal ($X$)**: Sudden straight forward pulse detects the axis with the largest variance spike (mapped as forward $X$).
3. **Lateral ($Y$)**: Designated as the remaining axis, verifying sign direction via centripetal acceleration during a sharp counter-clockwise turn.
- Configuration maps and signs are persistently saved to `data/calibration_config.json` (`imu_axis_map` and `imu_axis_signs`).
- Telemetry variables `accel_x`, `accel_y`, and `accel_z` are exposed at the top level of `/api/telemetry` along with robot `state`.
