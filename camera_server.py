#!/usr/bin/env python3
"""
Single-camera TCP stream server for Jetson.
Streams JPEG frames over TCP with 4-byte length prefix (same protocol as Simon's server).
Usage: python3 camera_server.py
"""
import cv2
import numpy as np
import os
import socket
import struct
import threading
import time
from collections import deque

PORT         = 9999
SNAPSHOT_PORT = 9998
JPEG_QUALITY = 80

def gst_pipeline():
    return (
        "nvarguscamerasrc sensor-id=0 ! "
        "video/x-raw(memory:NVMM),width=3280,height=2464,framerate=21/1 ! "
        "nvvidconv ! video/x-raw,format=GRAY8 ! "
        "appsink drop=1"
    )
        # "videoscale ! video/x-raw,width=320,height=240 ! appsink drop=1"

cap = cv2.VideoCapture(gst_pipeline(), cv2.CAP_GSTREAMER)
if not cap.isOpened():
    raise RuntimeError("Could not open CSI camera sensor-id=0")
print("[CAM] Camera opened")

_lock  = threading.Lock()
# _frame = None
_preview_frame = None
_preview_ts_ns = None
_raw_history = deque(maxlen=120)

def capture_loop():
    global _preview_frame, _preview_ts_ns, _raw_history
    encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    fail_count = 0
    frame_count = 0
    last_log = time.time()
    while True:
        ret, img = cap.read()
        if ret:
            fail_count = 0
            frame_count += 1
            ts_ns = time.time_ns()
            
            # manually resize to avoid lag
            preview = cv2.resize(img, (320, 240))
            ok, buf = cv2.imencode('.jpg', preview, encode_params)
            if ok:
                with _lock:
                    _preview_frame = buf.tobytes()
                    _preview_ts_ns = ts_ns
                    _raw_history.append((ts_ns, img.copy())) # store raw grey frame in history
        else:
            fail_count += 1
            if fail_count == 1 or fail_count % 100 == 0:
                print(f"[CAM] cap.read() failed (count={fail_count})")
            time.sleep(0.01)
        now = time.time()
        if now - last_log >= 5.0:
            print(f"[CAM] frames captured in last 5s: {frame_count}")
            frame_count = 0
            last_log = now

def preview_client_loop(conn, addr):
    print(f"[NET] Client connected: {addr}")
    try:
        while True:
            with _lock:
                data = _preview_frame
                ts_ns = _preview_ts_ns
            if data is None:
                time.sleep(0.01)
                continue
            try:
                if ts_ns is None:
                    payload = data
                else:
                    payload = struct.pack('>Q', ts_ns) + data
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
                
            data = None
            with _lock:
                if not _raw_history:
                    conn.sendall(struct.pack('>I', 0))
                    return
                if requested_ts is None:
                    ts_ns, data = _raw_history[-1]
                else:
                    ts_ns, data = min(_raw_history, key=lambda item: abs(item[0] - requested_ts))

            # for sending png over
            # if data is not None:
            #     height, width = data.shape
            #     ok, png_buf = cv2.imencode(".png", data, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            #     if not ok:
            #         conn.sendall(struct.pack('>I', 0))
            #         return
            #     packet = struct.pack('>IIQ', width, height, ts_ns) + png_buf.tobytes()
            #     conn.sendall(struct.pack('>I', len(packet)) + packet)
            
            if data is not None:
                height, width = data.shape
                png_filename = f".images/png/snapshot_{ts_ns:6d}_{width}x{height}_gray.png"
                cv2.imwrite(png_filename, data)
                print(f"Saved PNG snapshot {png_filename}")
                conn.sendall(struct.pack('>?', True)) # success
            else:
                conn.sendall(struct.pack('>?', False)) # failure
        
        elif request.startswith(b'RECORD'):
            # save the last 120 frames (about 6 seconds) to a timestamped directory
            with _lock: 
                # will freeze the preview until all frames are saved to the jetson              
                if not _raw_history:
                    conn.sendall(struct.pack('>?', False)) # failure
                    return
                start_ts = _raw_history[0][0]
                end_ts = _raw_history[-1][0]
                save_dir = f"./recordings/record_{start_ts}_{end_ts}"
                os.makedirs(save_dir, exist_ok=True)
                
                count = 0
                while _raw_history:
                    ts_ns, data = _raw_history.popleft()
                    if data is None:
                        continue
                    height, width = data.shape
                    png_filename = f"{save_dir}/snapshot_{count:03d}_{ts_ns:6d}_{width}x{height}_gray.png"
                    cv2.imwrite(png_filename, data)
                    print(f"Saved PNG snapshot {png_filename}")
                    count += 1
                    
                conn.sendall(struct.pack('>?', True)) # success
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
    

threading.Thread(target=capture_loop, daemon=True).start()
threading.Thread(target=serve_preview, daemon=True).start()
threading.Thread(target=serve_snapshot, daemon=True).start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopped.")
finally:
    cap.release()
