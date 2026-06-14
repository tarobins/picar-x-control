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
LOCAL_PORT_JUNIOR=5002
LOCAL_PORT_STREAM=9000
TUNNEL_LOG="${WORKSPACE_DIR}/ssh_tunnel.log"
SERVER_LOG="${WORKSPACE_DIR}/local_server.log"
JUNIOR_LOG="${WORKSPACE_DIR}/junior_server.log"

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
        # Check if the tunnel process is running (checks new lowdelay/no compression format or older formats)
        if pgrep -f "ssh -o ExitOnForwardFailure=yes -o IPQoS=lowdelay" >/dev/null || pgrep -f "ssh -o ExitOnForwardFailure=yes -L .*${LOCAL_PORT_API}:" >/dev/null || pgrep -f "ssh -L .*${LOCAL_PORT_API}:" >/dev/null; then
            # Check if the remote process is running
            if ssh -o ConnectTimeout=3 -q "$ROBOT_HOST" "pgrep -f server.py" >/dev/null; then
                # Check if local webservers are running
                if pgrep -f "local_server.py" >/dev/null && pgrep -f "junior_server.py" >/dev/null; then
                    return 0 # Yes, they are fully running!
                fi
            fi
        fi
    fi
    return 1 # No, not running or only partially running
}

stop_services() {
    log "Stopping local webservers (main & junior)..."
    LOCAL_SERVER_PIDS=$(pgrep -f "local_server.py") || true
    JUNIOR_SERVER_PIDS=$(pgrep -f "junior_server.py") || true
    for pid in $LOCAL_SERVER_PIDS $JUNIOR_SERVER_PIDS; do
        if [ -n "$pid" ]; then
            log "Killing local webserver process $pid..."
            kill "$pid" || true
        fi
    done
    sleep 0.5

    log "Stopping local SSH tunnels..."
    # Find PIDs of any ssh tunnel started by this script
    TUNNEL_PIDS=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -o IPQoS=lowdelay") || true
    TUNNEL_PIDS_STREAM=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -L 127.0.0.1:${LOCAL_PORT_STREAM}:") || true
    
    # Backwards compatibility/fallback for older combined tunnel format
    TUNNEL_PIDS_OLD=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -L 127.0.0.1:${LOCAL_PORT_API}:") || true
    TUNNEL_PIDS_OLD_2=$(pgrep -f "ssh -L .*${LOCAL_PORT_API}:") || true
    
    ALL_PIDS=""
    for pid in $TUNNEL_PIDS $TUNNEL_PIDS_STREAM $TUNNEL_PIDS_OLD $TUNNEL_PIDS_OLD_2; do
        if [ -n "$pid" ] && [[ ! " $ALL_PIDS " =~ " $pid " ]]; then
            ALL_PIDS="$ALL_PIDS $pid"
        fi
    done
    
    if [ -n "$ALL_PIDS" ]; then
        for pid in $ALL_PIDS; do
            log "Killing tunnel process $pid..."
            kill "$pid" || true
        done
        sleep 0.5
    else
        log "No active local SSH tunnels found."
    fi

    log "Stopping Flask server on the robot..."
    ssh "$ROBOT_HOST" "pkill -9 -f server.py" || true
    
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
    if lsof -i :${LOCAL_PORT_JUNIOR} -t >/dev/null 2>&1; then
        error "Local port ${LOCAL_PORT_JUNIOR} is already in use (Junior Dashboard)."
        ports_busy=true
    fi
    if lsof -i :${LOCAL_PORT_STREAM} -t >/dev/null 2>&1; then
        error "Local port ${LOCAL_PORT_STREAM} is already in use (Stream Tunnel)."
        ports_busy=true
    fi
    if [ "$ports_busy" = true ]; then
        exit 1
    fi

    log "Opening independent SSH tunnels (Control API & Camera Stream)..."
    # 1. API tunnel with lowdelay QoS and no compression
    ssh -o ExitOnForwardFailure=yes -o IPQoS=lowdelay -o Compression=no -L 127.0.0.1:${LOCAL_PORT_API}:127.0.0.1:5000 -f -N "$ROBOT_HOST" > "$TUNNEL_LOG" 2>&1
    # 2. Camera Stream tunnel
    ssh -o ExitOnForwardFailure=yes -L 127.0.0.1:${LOCAL_PORT_STREAM}:127.0.0.1:${LOCAL_PORT_STREAM} -f -N "$ROBOT_HOST" >> "$TUNNEL_LOG" 2>&1

    # Check if both SSH tunnels started successfully
    sleep 1.5
    local api_pid stream_pid
    api_pid=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -o IPQoS=lowdelay -o Compression=no -L 127.0.0.1:${LOCAL_PORT_API}:") || true
    stream_pid=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -L 127.0.0.1:${LOCAL_PORT_STREAM}:") || true
    
    if [ -z "$api_pid" ] || [ -z "$stream_pid" ]; then
        error "One or both SSH tunnels failed to start. Tunnel log contents:"
        cat "$TUNNEL_LOG"
        exit 1
    fi
    log "SSH tunnels started successfully (API PID: $api_pid, Stream PID: $stream_pid)."

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
        
        log "Starting local junior dashboard webserver (junior_server.py)..."
        "${WORKSPACE_DIR}/venv/bin/python3" "${WORKSPACE_DIR}/junior_server.py" --daemon --log-file "$JUNIOR_LOG"
        sleep 1.5

        if ! pgrep -f "local_server.py" >/dev/null; then
            error "Local webserver failed to start. Webserver log contents:"
            cat "$SERVER_LOG"
            exit 1
        fi
        if ! pgrep -f "junior_server.py" >/dev/null; then
            error "Junior webserver failed to start. Junior log contents:"
            cat "$JUNIOR_LOG"
            exit 1
        fi

        log "Success! Control Center Dashboards are running."
        log "Access Main Dashboard at: http://127.0.0.1:${LOCAL_PORT_DASHBOARD}"
        log "Access Junior Space Commander at: http://127.0.0.1:${LOCAL_PORT_JUNIOR}"
    else
        error "Failed to connect to the Control Center API. Last curl error: $curl_err"
        log "Fetching last 20 lines of server log from the robot:"
        ssh "$ROBOT_HOST" "tail -n 20 ${ROBOT_DIR}/server.log" || true
        exit 1
    fi
}

check_status() {
    # Check local webservers
    LOCAL_SERVER_PIDS=$(pgrep -f "local_server.py") || true
    if [ -n "$LOCAL_SERVER_PIDS" ]; then
        log "Local Webserver (Main): RUNNING (PIDs: $LOCAL_SERVER_PIDS)"
    else
        log "Local Webserver (Main): STOPPED"
    fi

    JUNIOR_SERVER_PIDS=$(pgrep -f "junior_server.py") || true
    if [ -n "$JUNIOR_SERVER_PIDS" ]; then
        log "Local Webserver (Junior): RUNNING (PIDs: $JUNIOR_SERVER_PIDS)"
    else
        log "Local Webserver (Junior): STOPPED"
    fi

    # Check local tunnels
    TUNNEL_PIDS_API=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -o IPQoS=lowdelay") || true
    TUNNEL_PIDS_STREAM=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -L 127.0.0.1:${LOCAL_PORT_STREAM}:") || true
    TUNNEL_PIDS_OLD=$(pgrep -f "ssh -o ExitOnForwardFailure=yes -L .*${LOCAL_PORT_API}:") || true
    TUNNEL_PIDS_OLD_2=$(pgrep -f "ssh -L .*${LOCAL_PORT_API}:") || true
    
    ALL_TUNNELS=""
    for pid in $TUNNEL_PIDS_API $TUNNEL_PIDS_STREAM $TUNNEL_PIDS_OLD $TUNNEL_PIDS_OLD_2; do
        if [ -n "$pid" ] && [[ ! " $ALL_TUNNELS " =~ " $pid " ]]; then
            ALL_TUNNELS="$ALL_TUNNELS $pid"
        fi
    done
    
    if [ -n "$ALL_TUNNELS" ]; then
        log "Local SSH tunnels: RUNNING (PIDs: $ALL_TUNNELS)"
    else
        log "Local SSH tunnels: STOPPED"
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
