#!/usr/bin/env python3
"""
Single-camera TCP stream server for Jetson.
Streams JPEG frames over TCP with 4-byte length prefix (same protocol as Simon's server).
Usage: python3 camera_server.py
"""
import cv2
import json
import numpy as np
import os
import socket
import struct
import threading
import time
from datetime import datetime
from collections import deque
import sys
import subprocess
import csv

PORT         = 9999
SNAPSHOT_PORT = 9998
JPEG_QUALITY = 80

def gst_pipeline(sensor_id):
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        "video/x-raw(memory:NVMM),width=3280,height=2464,framerate=21/1 ! "
        "nvvidconv ! video/x-raw,format=GRAY8 ! "
        "appsink drop=1 max-buffers=1 sync=false"
    )
        # "videoscale ! video/x-raw,width=320,height=240 ! appsink drop=1"

cap_left = cv2.VideoCapture(gst_pipeline(0), cv2.CAP_GSTREAMER)
cap_right = cv2.VideoCapture(gst_pipeline(1), cv2.CAP_GSTREAMER)
if not cap_left.isOpened():
    raise RuntimeError("Could not open CSI camera sensor-id=0")
if not cap_right.isOpened():
    raise RuntimeError("Could not open CSI camera sensor-id=1")
print("[CAM] Cameras opened")
    
_lock_left = threading.Lock()
_lock_right = threading.Lock()

_preview_left = None
_preview_left_ts = None
_raw_left_history = deque(maxlen=120)

_preview_right = None
_preview_right_ts = None
_raw_right_history = deque(maxlen=120)

def capture_loop(cap, lock, preview_key):
    global _preview_left, _preview_left_ts, _raw_left_history
    global _preview_right, _preview_right_ts, _raw_right_history
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    fail_count = 0
    frame_count = 0
    last_log = time.time()
    while True:
        ret, img = cap.read()
        if ret:
            fail_count = 0
            frame_count += 1
            ts_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            if ts_msec and ts_msec > 0:
                ts_ns = int(ts_msec * 1_000_000)
            else:
                ts_ns = time.time_ns()
            
            # manually resize to avoid lag
            preview = cv2.resize(img, (320, 240))
            ok, buf = cv2.imencode('.jpg', preview, encode_params)
            if ok:
                with lock:
                    if preview_key == "left":
                        _preview_left = buf.tobytes()
                        _preview_left_ts = ts_ns
                        _raw_left_history.append((ts_ns, img.copy()))
                    else:
                        _preview_right = buf.tobytes()
                        _preview_right_ts = ts_ns
                        _raw_right_history.append((ts_ns, img.copy()))
        else:
            fail_count += 1
            if fail_count == 1 or fail_count % 100 == 0:
                print(f"[CAM] cap.read() failed (count={fail_count})")
            time.sleep(0.01)
        now = time.time()
        if now - last_log >= 5.0:
            print(f"[CAM] {preview_key} frames captured in last 5s: {frame_count}")
            frame_count = 0
            last_log = now            
            

def preview_client_loop(conn, addr):
    print(f"[NET] Client connected: {addr}")
    try:
        while True:
            with _lock_left:
                left_data = _preview_left
                left_ts = _preview_left_ts
            with _lock_right:
                right_data = _preview_right
                right_ts = _preview_right_ts
            if left_data is None or right_data is None:
                time.sleep(0.01)
                continue
            try:
                payload = (
                    struct.pack('>QI', left_ts or 0, len(left_data)) + left_data +
                    struct.pack('>QI', right_ts or 0, len(right_data)) + right_data
                )
                conn.sendall(struct.pack('>I', len(payload)) + payload)
            except OSError:
                break
            time.sleep(1.0 / 30)
    finally:
        print(f"[NET] Client disconnected: {addr}")
        conn.close()


def snapshot_client_loop(conn, addr):
    print(f"[NET] Snapshot client connected: {addr}")
    try:
        request = conn.recv(1024)
        request = request.strip()

        if request.startswith(b'SAVE'):
            requested_ts = None
            if len(request) >= 4 + 8:
                requested_ts = struct.unpack('>Q', request[4:12])[0]
                
            with _lock_left:
                left_history = list(_raw_left_history)
                left_latest = _preview_left_ts
            with _lock_right:
                right_history = list(_raw_right_history)
                right_latest = _preview_right_ts

            if not left_history or not right_history:
                conn.sendall(struct.pack('>?', False))
                return

            if requested_ts is None:
                target_ts = min(left_latest or left_history[-1][0], right_latest or right_history[-1][0])
            else:
                target_ts = requested_ts

            left_ts, left_data = min(left_history, key=lambda item: abs(item[0] - target_ts))
            right_ts, right_data = min(right_history, key=lambda item: abs(item[0] - target_ts))

            # for sending png over
            # if data is not None:
            #     height, width = data.shape
            #     ok, png_buf = cv2.imencode(".png", data, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            #     if not ok:
            #         conn.sendall(struct.pack('>I', 0))
            #         return
            #     packet = struct.pack('>IIQ', width, height, ts_ns) + png_buf.tobytes()
            #     conn.sendall(struct.pack('>I', len(packet)) + packet)
            
            if left_data is None or right_data is None:
                conn.sendall(struct.pack('>?', False))
                return

            left_h, left_w = left_data.shape
            right_h, right_w = right_data.shape
            left_name = f"./images/png/snapshot_{left_ts:6d}_left_{left_w}x{left_h}_gray.png"
            right_name = f"./images/png/snapshot_{right_ts:6d}_right_{right_w}x{right_h}_gray.png"
            cv2.imwrite(left_name, left_data)
            cv2.imwrite(right_name, right_data)
            print(f"Saved PNG snapshot {left_name}")
            print(f"Saved PNG snapshot {right_name}")
            conn.sendall(struct.pack('>?', True))
        
        elif request.startswith(b'RECORD'):
            # save the last 120 frames (about 6 seconds) to a timestamped directory
            with _lock_left:
                left_history = list(_raw_left_history)
                _raw_left_history.clear()
            with _lock_right:
                right_history = list(_raw_right_history)
                _raw_right_history.clear()

            if not left_history or not right_history:
                conn.sendall(struct.pack('>?', False))
                return

            start_ts = left_history[0][0]
            end_ts = left_history[-1][0]
            save_dir = f"./recordings/record_{start_ts}_{end_ts}"
            os.makedirs(save_dir, exist_ok=True)

            for count, (ts_ns, left_data) in enumerate(left_history):
                if left_data is None:
                    continue
                right_ts, right_data = min(right_history, key=lambda item: abs(item[0] - ts_ns))
                if right_data is None:
                    continue
                left_h, left_w = left_data.shape
                right_h, right_w = right_data.shape
                left_name = f"{save_dir}/snapshot_{count:03d}_{ts_ns:6d}_left_{left_w}x{left_h}_gray.png"
                right_name = f"{save_dir}/snapshot_{count:03d}_{right_ts:6d}_right_{right_w}x{right_h}_gray.png"
                if not cv2.imwrite(left_name, left_data):
                    conn.sendall(struct.pack('>?', False))
                    return
                if not cv2.imwrite(right_name, right_data):
                    conn.sendall(struct.pack('>?', False))
                    return
                print(f"Saved PNG snapshot {left_name}")
                print(f"Saved PNG snapshot {right_name}")

            conn.sendall(struct.pack('>?', True))
            print(f"[NET] Recorded {count} frames to {save_dir}")
        else:
            print(f"[NET] Unknown snapshot request: {request}")
            conn.sendall(struct.pack('>?', False)) # failure
            
    except OSError:
        pass
    finally:
        print(f"[NET] Snapshot client disconnected: {addr}")
        conn.close()

def serve_preview():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', PORT))
    srv.listen(8)
    srv.settimeout(1.0)
    print(f"[NET] Preview server listening on port {PORT}")
    
    try:
        while True:
            try:
                conn, addr = srv.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                threading.Thread(target=preview_client_loop, args=(conn, addr), daemon=True).start()
            except socket.timeout:
                pass
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        srv.close()
    

def serve_snapshot():
    snap_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    snap_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    snap_srv.bind(('0.0.0.0', SNAPSHOT_PORT))
    snap_srv.listen(8)
    snap_srv.settimeout(1.0)
    print(f"[NET] Snapshot server listening on port {SNAPSHOT_PORT}")
    
    try:
        while True:
            try:
                conn, addr = snap_srv.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                threading.Thread(target=snapshot_client_loop, args=(conn, addr), daemon=True).start()
            except socket.timeout:
                pass
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        snap_srv.close()
    

def check_temp():
    os.makedirs("./temps", exist_ok=True)
    temps_filename = f"./temps/temps_{int(time.time())}.csv"
    
    with open(temps_filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp(jetson time)", "bus9_hex", "bus10_hex", "bus9_raw", "bus10_raw", "bus9_temp_c", "bus10_temp_c"])
    
    while True:
        result = subprocess.run([sys.executable, "read_temp.py"], capture_output=True, text=True)
        output = result.stdout.strip()

        if output:
            print(f"[TEMP] {output}")
            try:
                data = json.loads(output)
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

        bus9 = {"reg": "", "raw": "", "temp": ""}
        bus10 = {"reg": "", "raw": "", "temp": ""}

        # if the output is valid JSON, extract the temperature data for bus 9 and bus 10
        if isinstance(data, dict):
            b9 = data.get("9", {})
            b10 = data.get("10", {})
            bus9 = {
                "reg": b9.get("register", ""),
                "raw": b9.get("raw", ""),
                "temp": b9.get("temp_c", ""),
            }
            bus10 = {
                "reg": b10.get("register", ""),
                "raw": b10.get("raw", ""),
                "temp": b10.get("temp_c", ""),
            }

        with open(temps_filename, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime("%H:%M:%S"),
                bus9["reg"],
                bus10["reg"],
                bus9["raw"],
                bus10["raw"],
                bus9["temp"],
                bus10["temp"],
            ])

        print(f"[TEMP] Logged temperatures to {temps_filename}")
        time.sleep(10)
        
threading.Thread(target=capture_loop, args=(cap_left, _lock_left, "left"), daemon=True).start()
threading.Thread(target=capture_loop, args=(cap_right, _lock_right, "right"), daemon=True).start()
threading.Thread(target=serve_preview, daemon=True).start()
threading.Thread(target=serve_snapshot, daemon=True).start()
threading.Thread(target=check_temp, daemon=True).start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopped.")
finally:
    cap_left.release()
    cap_right.release()
