import socket
import struct
import threading
import time
import numpy as np
import os
import cv2

JETSON_IP = '100.107.122.126'
PORT = 9999
CAPTURE_PORT = 9998


class CameraReceiver:
    def __init__(self, host=JETSON_IP, port=PORT):
        self._host = host
        self._port = port
        self._capture_port = CAPTURE_PORT
        self._connected_capture = False
        self._lock = threading.Lock()
        self._left_frame = None
        self._right_frame = None
        self._left_ts_ns = None
        self._right_ts_ns = None
        self.connected = False
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def read(self):
        with self._lock:
            return self._left_frame, self._right_frame

    def read_timestamp(self):
        with self._lock:
            return self._left_ts_ns, self._right_ts_ns

    def _recv_loop(self):
        while True:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((self._host, self._port))
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.connected = True
                print(f"[CAM] Connected to Jetson at {self._host}:{self._port}")
                buf = b''
                while True:
                    while len(buf) < 4:
                        chunk = sock.recv(4096)
                        if not chunk:
                            raise ConnectionError("server closed")
                        buf += chunk
                    length = struct.unpack('>I', buf[:4])[0]
                    buf = buf[4:]
                    while len(buf) < length:
                        chunk = sock.recv(65536)
                        if not chunk:
                            raise ConnectionError("server closed")
                        buf += chunk

                    payload, buf = buf[:length], buf[length:]

                    if len(payload) < 24:
                        continue

                    left_ts, left_len = struct.unpack('>QI', payload[:12])
                    if len(payload) < 12 + left_len + 12:
                        continue
                    left_jpg = payload[12:12 + left_len]
                    right_ts, right_len = struct.unpack('>QI', payload[12 + left_len:12 + left_len + 12])
                    if len(payload) < 12 + left_len + 12 + right_len:
                        continue
                    right_jpg = payload[12 + left_len + 12:12 + left_len + 12 + right_len]

                    left_img = cv2.imdecode(np.frombuffer(left_jpg, np.uint8), cv2.IMREAD_GRAYSCALE)
                    right_img = cv2.imdecode(np.frombuffer(right_jpg, np.uint8), cv2.IMREAD_GRAYSCALE)

                    if left_img is not None and right_img is not None:
                        with self._lock:
                            self._left_frame = left_img.copy()
                            self._right_frame = right_img.copy()
                            self._left_ts_ns = left_ts
                            self._right_ts_ns = right_ts
                        
                            
            except Exception as e:
                self.connected = False
                with self._lock:
                    self._left_frame = None
                    self._right_frame = None
                print(f"[CAM] Disconnected ({e}) — retrying in 3s…")
                time.sleep(3.0)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
    
    def read_exact(self, sock, length):
        buf = b''
        while len(buf) < length:
            chunk = sock.recv(length - len(buf))
            if not chunk:
                raise ConnectionError("server closed")
            buf += chunk
        return buf
    
    def save_raw_snapshot(self, frame_id=0, cmd=None, save_dir="./images"):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect((self._host, self._capture_port))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._connected_capture = True
            
            if cmd == ord('s'):
                # send SAVE request to camera server on the jetson
                left_ts, right_ts = self.read_timestamp()
                target_ts = None
                if left_ts is not None and right_ts is not None:
                    target_ts = min(left_ts, right_ts)
                if target_ts is None:
                    sock.sendall(b"SAVE")
                else:
                    sock.sendall(b"SAVE" + struct.pack('>Q', target_ts))
                
                rsp = self.read_exact(sock, 1)
                success = struct.unpack('>?', rsp)[0]
                if not success:
                    print("[CAM] Snapshot request failed on server")
                    return False
                print("[CAM] Snapshot request succeeded on server")
                return True
                
            elif cmd == ord('r'):
                # send RAW request to camera server on the jetson
                sock.sendall(b"RECORD")
                rsp = self.read_exact(sock, 1)
                success = struct.unpack('>?', rsp)[0]
                if not success:
                    print("[CAM] Record request failed on server")
                    return False
                print("[CAM] Record request succeeded on server")
                return True
                
                
            
            # read the response packet
            # length_buf = self.read_exact(sock, 1)
            # length = struct.unpack('>I', length_buf)[0]
            
            # if length == 0:
            #     print("[CAM] No frame available for snapshot")
            #     return False
            
            # packet = self.read_exact(sock, length)
            # width, height, frame_ts_ns = struct.unpack('>IIQ', packet[:16])
            # png_bytes = packet[16:]
            
            # for raw, but raw isnt needed for MTF analysis
            # expected_size = width * height
            # if len(raw) != expected_size:
            #     print(f"[CAM] Warning: expected raw size {expected_size}, got {len(raw)}")
            #     return False
            
            # reshape for raw, but raw isnt needed for pngs
            # gray = np.frombuffer(png_bytes, dtype=np.uint8).reshape((height, width))
            # png_array = np.frombuffer(png_bytes, dtype=np.uint8)
            # grey = cv2.imdecode(png_array, cv2.IMREAD_GRAYSCALE)
            # if grey is None:
            #     print("[CAM] Failed to decode PNG snapshot")
            #     return False
            
            # if grey.shape != (height, width):
            #     print(f"[CAM] Warning: expected PNG shape {(height, width)}, got {grey.shape}")
            #     return False
            
            # raw_filename = f"{save_dir}/raw/snapshot_{frame_id:03d}_{width}x{height}_gray.raw"
            # gray.tofile(raw_filename)
            # print(f"Saved raw snapshot {raw_filename}")

            # FOR PNG
            # png_filename = f"{save_dir}/png/snapshot_{frame_id:03d}_{width}x{height}_gray.png"
            # cv2.imwrite(png_filename, grey)
            # print(f"Saved PNG snapshot {png_filename}")
            
            return True
        
        except Exception as e:
            print(f"[CAM] Failed to save snapshot: {e}")
            return False
        
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
    
            
            
            

def main():
    cam = CameraReceiver()
    window = "Jetson Camera"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    blank = np.zeros((240, 320), dtype=np.uint8)
    frame_id = 0
    
    while True:
        left_frame, right_frame = cam.read()
        if left_frame is None or right_frame is None:
            display = np.hstack([blank.copy(), blank.copy()])
            cv2.putText(display, "NO SIGNAL", (120, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        else:
            display = np.hstack([left_frame, right_frame])
        cv2.imshow(window, display)
        
        key = cv2.waitKey(1) & 0xFF
        
        if (key == ord('s') or key == ord('r')) and left_frame is not None and right_frame is not None:
            if cam.save_raw_snapshot(frame_id=frame_id, cmd=key):
                if key == ord('s'):
                    frame_id += 1
        if key in (ord('q'), 27):
            break

    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
