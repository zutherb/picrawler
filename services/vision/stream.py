"""MJPEG over HTTP, local only. The web service proxies it with login."""
import http.server
import threading

BOUNDARY = b"frame"


def make_handler(buffer):

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if self.path == "/stream.mjpg":
                self._stream()
            elif self.path == "/snapshot.jpg":
                self._snapshot()
            else:
                self.send_error(404)

        def _snapshot(self):
            frame = buffer.latest()
            if frame is None:
                self.send_error(503, "no frame yet")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(frame)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=%s" % BOUNDARY.decode())
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seq = 0
            try:
                while True:
                    seq, frame = buffer.wait(seq)
                    if frame is None:
                        continue
                    self.wfile.write(b"--" + BOUNDARY + b"\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame))
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


class StreamServer:
    def __init__(self, buffer, host="127.0.0.1", port=8081):
        self._server = http.server.ThreadingHTTPServer((host, port), make_handler(buffer))
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self):
        return self._server.server_address[1]

    def start(self):
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
