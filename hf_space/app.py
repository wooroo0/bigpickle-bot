import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 7860


def start_bot():
    from bot import main
    main()


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"SteamOsint is alive")

    def log_message(self, *args):
        pass


class SafeHTTPServer(HTTPServer):
    def server_bind(self):
        if self.allow_reuse_address:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = self.server_address


if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    httpd = SafeHTTPServer(("0.0.0.0", PORT), Health)
    print("health server on", PORT, flush=True)
    httpd.serve_forever()