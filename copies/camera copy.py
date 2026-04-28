import socket
import struct
import threading
import time
import numpy as np
import cv2

JETSON_IP = '100.107.122.126'
PORT = 9999


class CameraReceiver:
    def __init__(self, host=JETSON_IP, port=PORT):
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._frame = None
        self.connected = False
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def read(self):
        with self._lock:
            return self._frame

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
                        
                    jpg, buf = buf[:length], buf[length:]
                    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        with self._lock:
                            self._frame = img.copy()
                    
                    # grab raw image and encode it back
                    # packet = buf[:length]
                    # buf = buf[length:]
                    
                    # recieve gray raw image
                    width, height = struct.unpack('>II', packet[:8])
                    raw = packet[8:]
                    
                    # img = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, channels))
                    img = np.frombuffer(raw, dtype=np.uint8).reshape((height, width))
                    
                    with self._lock:
                        self._frame = img.copy()
                        
                            
            except Exception as e:
                self.connected = False
                with self._lock:
                    self._frame = None
                print(f"[CAM] Disconnected ({e}) — retrying in 3s…")
                time.sleep(3.0)
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
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    frame_id = 0
    
    while True:
        frame = cam.read()
        if frame is None:
            display = blank.copy()
            cv2.putText(display, "NO SIGNAL", (180, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 2)
        else:
            display = frame
        cv2.imshow(window, display)
        
        if cv2.waitKey(1) & 0xFF == ord('s') and frame is not None:
            h, w = frame.shape[:2]
            filename = f"frame_{frame_id:06d}_{w}x{h}_gray.raw"
            frame.tofile(f"./raw/{filename}")
            print(f"Saved {filename}")
            frame_id += 1
            
        if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
            break

    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
