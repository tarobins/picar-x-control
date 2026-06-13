#!/usr/bin/env bash

# Robust startup/management script for PiCar-X Control Center.
# Deploys the code, stops/starts the remote server, manages the local SSH tunnel,
# and verifies the connection.

set -euo pipefail

# Determine script directory to resolve paths
WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT_HOST="robot"
ROBOT_DIR="~/picar-x"
LOCAL_PORT_DASHBOARD=5000
LOCAL_PORT_API=5001
LOCAL_PORT_STREAM=9000
TUNNEL_LOG="${WORKSPACE_DIR}/ssh_tunnel.log"
SERVER_LOG="${WORKSPACE_DIR}/local_server.log"

log() {
    echo -e "\033[1;34m[PiCar-X]\033[0m $1"
}

error() {
    echo -e "\033[1;31m[ERROR]\033[0m $1" >&2
}

check_robot_connection() {
    log "Checking connection to robot ($ROBOT_HOST)..."
    if ! ssh -o ConnectTimeout=3 -q "$ROBOT_HOST" exit; then
        error "Cannot connect to $ROBOT_HOST over SSH. Is it online and configured in your SSH config?"
        exit 1
    fi
    log "Robot connection OK."
}

check_already_running() {
    # Check if we can connect to the local Dashboard
    if curl -s -f --max-time 2 "http://127.0.0.1:${LOCAL_PORT_DASHBOARD}/" >/dev/null 2>&1; then
        # Check if the tunnel process is running
        if pgrep -f "ssh -o ExitOnForwardFailure=yes -L .*${LOCAL_PORT_API}:" >/dev/null || pgrep -f "ssh -L .*${LOCAL_PORT_API}:" >/dev/null; then
            # Check if the remote process is running
            if ssh -o ConnectTimeout=3 -q "$ROBOT_HOST" "pgrep -f server.py" >/dev/null; then
                # Check if local webserver is running
                if pgrep -f "local_server.py" >/dev/null; then
                    return 0 # Yes, it is fully running!
                fi
            fi
        fi
    fi
    return 1 # No, not running or only partially running
}

stop_services() {
    log "Stopping local webserver..."
    LOCAL_SERVER_PIDS=$(pgrep -f "local_server.py") || true
    if [ -n "$LOCAL_SERVER_PIDS" ]; then
        for pid in $LOCAL_SERVER_PIDS; do
            log "Killing local webserver process $pid..."
            kill "$pid" || true
        done
        sleep 0.5
    fi

    log "Stopping local SSH tunnel..."
    # Find PIDs of ssh tunnels forwarding the API port
    TUNNEL_PIDS=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -L .*${LOCAL_PORT_API}:") || true
    if [ -z "$TUNNEL_PIDS" ]; then
        # Backwards compatibility/fallback for older tunnel format
        TUNNEL_PIDS=$(pgrep -f "ssh -L .*${LOCAL_PORT_API}:") || true
    fi
    
    if [ -n "$TUNNEL_PIDS" ]; then
        for pid in $TUNNEL_PIDS; do
            log "Killing tunnel process $pid..."
            kill "$pid" || true
        done
        sleep 0.5
    else
        log "No active local SSH tunnels found."
    fi

    log "Stopping Flask server on the robot..."
    ssh "$ROBOT_HOST" "pkill -f server.py" || true
    
    # Give the robot OS a moment to free the camera device and socket bindings
    log "Waiting 1.0s for remote resources to release..."
    sleep 1.0
    log "All services stopped."
}

sync_code() {
    log "Deploying code to robot ($ROBOT_HOST:$ROBOT_DIR)..."
    rsync -avz --no-perms --no-owner --no-group \
        --exclude='.lgd-nfy0' \
        --exclude='build' \
        --exclude='picar_x.egg-info' \
        --exclude='.git' \
        --exclude='*.sh' \
        --exclude='templates/' \
        --exclude='local_server.py' \
        --exclude='local_server.log' \
        --exclude='venv/' \
        "$WORKSPACE_DIR/" "$ROBOT_HOST:$ROBOT_DIR/"
    log "Sync complete."
}

start_services() {
    log "Starting remote server on the robot..."
    # Run the server in the background on the robot using nohup
    ssh "$ROBOT_HOST" "nohup python3 ${ROBOT_DIR}/server.py > ${ROBOT_DIR}/server.log 2>&1 &"
    
    # Wait briefly for process setup before launching tunnel
    sleep 1.0

    log "Checking local port availability..."
    local ports_busy=false
    if lsof -i :${LOCAL_PORT_DASHBOARD} -t >/dev/null 2>&1; then
        error "Local port ${LOCAL_PORT_DASHBOARD} is already in use (Dashboard)."
        ports_busy=true
    fi
    if lsof -i :${LOCAL_PORT_API} -t >/dev/null 2>&1; then
        error "Local port ${LOCAL_PORT_API} is already in use (API Tunnel)."
        ports_busy=true
    fi
    if lsof -i :${LOCAL_PORT_STREAM} -t >/dev/null 2>&1; then
        error "Local port ${LOCAL_PORT_STREAM} is already in use (Stream Tunnel)."
        ports_busy=true
    fi
    if [ "$ports_busy" = true ]; then
        exit 1
    fi

    log "Opening SSH tunnel for ports ${LOCAL_PORT_API} and ${LOCAL_PORT_STREAM}..."
    # Start SSH tunnel in the background as a daemon
    ssh -o ExitOnForwardFailure=yes -L 127.0.0.1:${LOCAL_PORT_API}:127.0.0.1:5000 -L 127.0.0.1:${LOCAL_PORT_STREAM}:127.0.0.1:${LOCAL_PORT_STREAM} -f -N "$ROBOT_HOST" > "$TUNNEL_LOG" 2>&1

    # Check if the SSH tunnel started successfully
    sleep 1.5
    local ssh_pid
    ssh_pid=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -L 127.0.0.1:${LOCAL_PORT_API}:127.0.0.1:") || true
    if [ -z "$ssh_pid" ]; then
        error "SSH tunnel failed to start. Tunnel log contents:"
        cat "$TUNNEL_LOG"
        exit 1
    fi
    log "SSH tunnel started (PID: $ssh_pid)."

    # Verification loop
    log "Verifying API connection (polling up to 35s)..."
    local max_attempts=35
    local attempt=1
    local success=false
    local curl_err=""
    
    while [ $attempt -le $max_attempts ]; do
        # Capture curl error message for troubleshooting if needed
        curl_err=$(curl -S -s -f "http://127.0.0.1:${LOCAL_PORT_API}/api/status" 2>&1 >/dev/null) && curl_status=$? || curl_status=$?
        if [ $curl_status -eq 0 ]; then
            success=true
            break
        fi
        log "API not ready yet. Retrying in 1s... (attempt $attempt/$max_attempts - curl error code $curl_status)"
        sleep 1
        attempt=$((attempt + 1))
    done

    if [ "$success" = true ]; then
        log "Starting local dashboard webserver (local_server.py)..."
        "${WORKSPACE_DIR}/venv/bin/python3" "${WORKSPACE_DIR}/local_server.py" --daemon --log-file "$SERVER_LOG"
        sleep 1.5

        if ! pgrep -f "local_server.py" >/dev/null; then
            error "Local webserver failed to start. Webserver log contents:"
            cat "$SERVER_LOG"
            exit 1
        fi

        log "Success! Control Center Dashboard is running."
        log "Access Dashboard at: http://127.0.0.1:${LOCAL_PORT_DASHBOARD}"
    else
        error "Failed to connect to the Control Center API. Last curl error: $curl_err"
        log "Fetching last 20 lines of server log from the robot:"
        ssh "$ROBOT_HOST" "tail -n 20 ${ROBOT_DIR}/server.log" || true
        exit 1
    fi
}

check_status() {
    # Check local webserver
    LOCAL_SERVER_PIDS=$(pgrep -f "local_server.py") || true
    if [ -n "$LOCAL_SERVER_PIDS" ]; then
        log "Local Webserver: RUNNING (PIDs: $LOCAL_SERVER_PIDS)"
    else
        log "Local Webserver: STOPPED"
    fi

    # Check local tunnel
    TUNNEL_PIDS=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -L .*${LOCAL_PORT_API}:") || true
    if [ -z "$TUNNEL_PIDS" ]; then
        TUNNEL_PIDS=$(pgrep -f "ssh -L .*${LOCAL_PORT_API}:") || true
    fi
    if [ -n "$TUNNEL_PIDS" ]; then
        log "Local SSH tunnel: RUNNING (PIDs: $TUNNEL_PIDS)"
    else
        log "Local SSH tunnel: STOPPED"
    fi

    # Check remote process
    if ssh -q "$ROBOT_HOST" exit; then
        REMOTE_PIDS=$(ssh "$ROBOT_HOST" "pgrep -f server.py" || true)
        if [ -n "$REMOTE_PIDS" ]; then
            log "Remote Server: RUNNING (PIDs: $REMOTE_PIDS)"
        else
            log "Remote Server: STOPPED"
        fi
    else
        log "Remote Server: UNREACHABLE (Robot offline)"
    fi
}

usage() {
    cat <<EOF
Usage: $0 [COMMAND]

Commands:
  start     Deploy code, restart remote server, and open local tunnel (default). Skips if already running.
  stop      Close local tunnel and stop remote server
  restart   Stop services, deploy code, and start services (forces restart)
  status    Show running status of local tunnel and remote server
  sync      Sync current workspace code to the robot
  help      Show this help message

Options:
  --skip-sync  Skip code deployment during start/restart
EOF
}

# Parse options
SKIP_SYNC=false
COMMAND="start"

# Simple command parser
if [ $# -gt 0 ]; then
    case "$1" in
        start|stop|restart|status|sync|help)
            COMMAND="$1"
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
    esac
fi

# Parse remaining options
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-sync)
            SKIP_SYNC=true
            shift
            ;;
        *)
            usage
            exit 1
            ;;
    esac
done

case "$COMMAND" in
    help)
        usage
        ;;
    status)
        check_status
        ;;
    stop)
        check_robot_connection
        stop_services
        ;;
    sync)
        check_robot_connection
        sync_code
        ;;
    start)
        check_robot_connection
        if check_already_running; then
            log "Control Center is already running and accessible."
            log "Access Dashboard at: http://127.0.0.1:${LOCAL_PORT_API}"
            exit 0
        fi
        if [ "$SKIP_SYNC" = false ]; then
            sync_code
        fi
        stop_services # clean state first
        start_services
        ;;
    restart)
        check_robot_connection
        stop_services
        if [ "$SKIP_SYNC" = false ]; then
            sync_code
        fi
        start_services
        ;;
esac
