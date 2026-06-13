import requests
import time

BASE_URL = "http://127.0.0.1:5001"

def get_status():
    """Get the current state of the robot."""
    try:
        r = requests.get(f"{BASE_URL}/api/status")
        return r.json()
    except Exception as e:
        print(f"Error connecting to robot: {e}")
        return None

def move(action, speed=50, steering_angle=0):
    """
    Control movement of the robot.
    action: 'forward', 'backward', 'stop', or 'steer'
    """
    try:
        r = requests.post(f"{BASE_URL}/api/move", json={
            "action": action,
            "speed": speed,
            "steering_angle": steering_angle
        })
        return r.json()
    except Exception as e:
        print(f"Error sending move command: {e}")
        return None

def forward(speed=50, steering_angle=0):
    """Drive forward."""
    return move("forward", speed, steering_angle)

def backward(speed=50, steering_angle=0):
    """Drive backward."""
    return move("backward", speed, steering_angle)

def steer(angle):
    """Set the front wheels steering angle (-30 to 30)."""
    return move("steer", steering_angle=angle)

def stop():
    """Stop the robot motors."""
    return move("stop")

def set_camera(pan=None, tilt=None):
    """Set the camera pan (-90 to 90) and tilt (-35 to 65) angles."""
    payload = {}
    if pan is not None:
        payload["pan"] = pan
    if tilt is not None:
        payload["tilt"] = tilt
    try:
        r = requests.post(f"{BASE_URL}/api/camera", json=payload)
        return r.json()
    except Exception as e:
        print(f"Error sending camera command: {e}")
        return None

def get_telemetry():
    """Read ultrasonic, IR grayscale, and system diagnostics."""
    try:
        r = requests.get(f"{BASE_URL}/api/telemetry")
        if r.status_code == 200:
            return r.json().get("telemetry", {})
        return None
    except Exception as e:
        print(f"Error reading telemetry: {e}")
        return None

def run_remote(code):
    """
    Send python code to run directly on the robot.
    Returns the captured stdout/stderr output.
    """
    try:
        r = requests.post(f"{BASE_URL}/api/execute", json={"code": code})
        if r.status_code == 200:
            res = r.json()
            if res.get("status") == "success":
                print(res.get("output", ""))
            else:
                print("Error in remote execution:")
                print(res.get("output", ""))
            return res
        else:
            print(f"Failed to execute code. Server returned status code: {r.status_code}")
            return None
    except Exception as e:
        print(f"Error executing remote code: {e}")
        return None

if __name__ == "__main__":
    print("Testing connection to PiCar-X...")
    status = get_status()
    if status:
        print("Connected successfully!")
        print("Current State:", status.get("state"))
        
        print("\nReading telemetry...")
        tel = get_telemetry()
        if tel:
            print(f"  Ultrasonic distance: {tel.get('distance')} cm")
            print(f"  Grayscale readings: {tel.get('grayscale')}")
            print(f"  CPU Temp: {tel.get('cpu_temp')} °C")
            print(f"  CPU Usage: {tel.get('cpu_usage')}%")
            
        print("\nWiggling camera as a test...")
        set_camera(pan=20, tilt=10)
        time.sleep(0.5)
        set_camera(pan=-20, tilt=-10)
        time.sleep(0.5)
        set_camera(pan=0, tilt=0)
        print("Gimbal calibrated back to center.")
    else:
        print("Could not connect. Make sure the server and SSH tunnel are running.")
