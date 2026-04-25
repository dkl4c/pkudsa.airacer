"""
Webots car controller for AI Racer platform.
Uses the Driver API. Launches student code in a sandboxed subprocess,
exchanges camera frames via stdin/stdout, and applies the returned
steering/speed commands to the vehicle.
"""

import os
import json
import sys
import struct
import subprocess
import threading
import math

import numpy as np
from vehicle import Driver

# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

driver  = Driver()
timestep = int(driver.getBasicTimeStep())  # 64 ms

# Self-identification — robot name in the scene tree must match car_node_id
my_node_id = driver.getName()

config_path = os.environ.get('RACE_CONFIG_PATH', 'race_config.json')
with open(config_path, encoding='utf-8') as f:
    config = json.load(f)

my_config = next((c for c in config['cars'] if c['car_node_id'] == my_node_id), None)

if my_config is None:
    # Unused car slot — idle forever
    while driver.step() != -1:
        driver.setSpeed(0)
        driver.setSteeringAngle(0)
    raise SystemExit(0)

team_id   = my_config['team_id']
code_path = my_config['code_path']

# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------

IMG_H, IMG_W = 480, 640

left_cam  = driver.getDevice('left_camera')
right_cam = driver.getDevice('right_camera')
left_cam.enable(timestep)
right_cam.enable(timestep)

# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------

controller_dir  = os.path.dirname(os.path.abspath(__file__))
sandbox_script  = os.path.join(controller_dir, 'sandbox_runner.py')

MAX_SPEED = 10.0  # m/s


def launch_sandbox():
    """Spawn sandbox_runner.py as a subprocess with piped stdin/stdout."""
    return subprocess.Popen(
        [sys.executable, sandbox_script, '--team-id', team_id, '--code-path', code_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def get_bgr(cam):
    """Convert Webots BGRA image bytes to a (H, W, 3) uint8 BGR array."""
    raw = cam.getImage()
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((IMG_H, IMG_W, 4))
    return arr[:, :, :3].copy()  # drop alpha channel


def send_frame(proc, left_bgr, right_bgr, timestamp):
    """Send a packed frame (left image, right image, timestamp) to sandbox stdin."""
    left_b  = left_bgr.tobytes()
    right_b = right_bgr.tobytes()
    msg = (struct.pack('<I', len(left_b))  + left_b +
           struct.pack('<I', len(right_b)) + right_b +
           struct.pack('<d', timestamp))
    proc.stdin.write(msg)
    proc.stdin.flush()


def read_line_timeout(pipe, timeout=0.020):
    """
    Read one line from pipe with a timeout (seconds).
    Returns bytes, b'' on closed pipe, or None on timeout.
    Windows select() does not work on pipes, so we use a daemon thread.
    """
    result = [None]

    def _reader():
        try:
            result[0] = pipe.readline()
        except Exception:
            result[0] = b''

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout)
    return result[0]  # None = timed out

# ---------------------------------------------------------------------------
# Main loop state
# ---------------------------------------------------------------------------

proc             = launch_sandbox()
last_steering    = 0.0
last_speed       = 0.5
warn_count       = 0
force_stopped    = False
stop_until       = 0.0
disqualified     = False
restart_stop_until = 0.0

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

while driver.step() != -1:
    current_time = driver.getTime()

    # --- Read IPC commands from Supervisor via customData ---
    custom_data = driver.getCustomData()
    if custom_data:
        try:
            cmd = json.loads(custom_data)
            if cmd.get('cmd') == 'stop' and not force_stopped:
                duration   = float(cmd.get('duration', 2.0))
                stop_until = current_time + duration
                force_stopped = True
            elif cmd.get('cmd') == 'disqualify':
                disqualified = True
        except (json.JSONDecodeError, ValueError):
            pass

    # --- Handle disqualified state ---
    if disqualified:
        driver.setSpeed(0)
        driver.setSteeringAngle(0)
        continue

    # --- Handle stop penalty ---
    if force_stopped:
        if current_time < stop_until:
            driver.setSpeed(0)
            driver.setSteeringAngle(0)
            continue
        else:
            force_stopped = False

    # --- Sandbox post-restart cooldown ---
    if current_time < restart_stop_until:
        driver.setSpeed(0)
        driver.setSteeringAngle(0)
        continue

    # --- Check sandbox process health ---
    if proc.poll() is not None:
        exit_code = proc.returncode
        if exit_code == 2:
            # Student code had a blocked import — disqualify this car
            disqualified = True
            driver.setSpeed(0)
            driver.setSteeringAngle(0)
            continue
        else:
            # Crashed for another reason — restart with a 2-second cooldown
            restart_stop_until = current_time + 2.0
            proc = launch_sandbox()
            driver.setSpeed(0)
            driver.setSteeringAngle(0)
            continue

    # --- Capture camera frames ---
    left_bgr  = get_bgr(left_cam)
    right_bgr = get_bgr(right_cam)

    # --- Send frame to sandbox ---
    try:
        send_frame(proc, left_bgr, right_bgr, current_time)
    except Exception:
        # Pipe broken — hold last command until next step restores it
        driver.setSpeed(last_speed * MAX_SPEED)
        driver.setSteeringAngle(last_steering * 0.5)
        continue

    # --- Read response (20 ms timeout) ---
    raw = read_line_timeout(proc.stdout, 0.020)

    if raw is None or raw == b'':
        # Timeout or closed pipe — coast on last command
        warn_count += 1
        steering = last_steering
        speed    = last_speed
    else:
        try:
            out      = json.loads(raw.decode().strip())
            steering = float(max(-1.0, min(1.0, out['steering'])))
            speed    = float(max(0.0,  min(1.0, out['speed'])))
            last_steering = steering
            last_speed    = speed
            warn_count    = 0
        except Exception:
            steering   = last_steering
            speed      = last_speed
            warn_count += 1

    # 3 consecutive timeouts → impose a 5-second stop penalty (lap void)
    if warn_count >= 3:
        warn_count = 0
        restart_stop_until = current_time + 5.0
        driver.setSpeed(0)
        driver.setSteeringAngle(0)
        continue

    # --- Apply to vehicle ---
    # steering in [-1, 1]  →  [-0.5, 0.5] rad
    # speed    in [ 0, 1]  →  [0, MAX_SPEED] m/s
    driver.setSteeringAngle(steering * 0.5)
    driver.setSpeed(speed * MAX_SPEED)
