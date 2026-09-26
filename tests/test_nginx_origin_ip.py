"""Exercise the actual host Nginx config with an isolated loopback upstream."""
import http.client
import http.server
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest


REPO = Path(__file__).resolve().parents[1]
KEY = "a" * 64  # Synthetic test-only credential.


class HeaderEcho(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({
            "ip": self.headers.get("X-Real-IP"),
            "chain": self.headers.get("X-Forwarded-For"),
            "key": self.headers.get("X-Easy-SWU-Origin-Key"),
            "upgrade": self.headers.get("Upgrade"),
            "connection": self.headers.get("Connection"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class NginxOriginIpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nginx = shutil.which("nginx")
        if not nginx:
            raise RuntimeError("Nginx is required for the origin-IP regression tests")
        cls.workspace = tempfile.TemporaryDirectory(prefix="easy-swu-nginx-test-")
        cls.root = Path(cls.workspace.name)
        cls.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HeaderEcho)
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        key_dir = cls.root / "keys"
        key_dir.mkdir()
        (key_dir / "active.conf").write_text('"~^' + KEY + '$" 1;\n')
        site = (REPO / "host/nginx/easy-swu").read_text()
        site = site.replace("listen 80;", f"listen 127.0.0.1:{cls.port};")
        site = site.replace("listen [::]:80;", "")
        site = site.replace("127.0.0.1:32080", f"127.0.0.1:{cls.upstream.server_port}")
        site = site.replace("/etc/nginx/private/easy-swu-origin-keys", str(key_dir))
        cls.config = cls.root / "nginx.conf"
        cls.config.write_text(
            f"pid {cls.root}/nginx.pid;\nerror_log {cls.root}/error.log;\n"
            "events {}\nhttp { access_log off;\n" + site + "\n}\n"
        )
        cls.process = subprocess.Popen(
            [nginx, "-p", str(cls.root), "-c", str(cls.config), "-g", "daemon off;"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for _ in range(100):
            if cls.process.poll() is not None:
                raise RuntimeError(cls.process.stderr.read().decode())
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.02)
        raise RuntimeError("Isolated Nginx did not start")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.process.stderr.close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.thread.join(timeout=5)
        cls.workspace.cleanup()

    def request(self, host="easy-api.lazycampus.com", key=KEY, ip="203.0.113.10", extra=()):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest("GET", "/api/v1/system/health", skip_host=True)
        connection.putheader("Host", host)
        if key is not None:
            connection.putheader("X-Easy-SWU-Origin-Key", key)
        if ip is not None:
            connection.putheader("EO-Connecting-IP", ip)
        connection.putheader("X-Forwarded-For", "192.0.2.123, 198.51.100.123")
        connection.putheader("X-Real-IP", "192.0.2.124")
        for name, value in extra:
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        data = response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        return json.loads(data)

    def assert_origin(self, result, expected):
        self.assertEqual({k: result[k] for k in ("ip", "chain", "key")}, {"ip": expected, "chain": expected, "key": None})

    def test_websocket_upgrade_reaches_api(self):
        result = self.request(extra=(("Upgrade", "websocket"), ("Connection", "Upgrade")))
        self.assertEqual(result["upgrade"], "websocket")
        self.assertEqual(result["connection"], "upgrade")
        self.assertEqual(self.request()["connection"], "close")

    def test_authenticated_ipv4_and_ipv6_replace_forged_chains_for_both_hosts(self):
        for host in ("easy-api.lazycampus.com", "easy-admin.lazycampus.com"):
            for ip in ("203.0.113.10", "2001:db8::42", "::ffff:203.0.113.10"):
                with self.subTest(host=host, ip=ip):
                    self.assert_origin(self.request(host=host, ip=ip), ip)

    def test_direct_requests_cannot_forge_origin_ip(self):
        for key in (None, "wrong", KEY.upper(), KEY + "x"):
            with self.subTest(key=key):
                self.assert_origin(self.request(key=key), "127.0.0.1")

    def test_missing_invalid_or_multiple_ips_fall_back_to_peer(self):
        for ip in (None, "", "not-an-ip", "999.1.2.3", "::::", "203.0.113.10, 192.0.2.1",
                   "203.0.113.10:8080", "[2001:db8::42]:8080", "255.255.255.255"):
            with self.subTest(ip=ip):
                self.assert_origin(self.request(ip=ip), "127.0.0.1")

    def test_later_headers_cannot_override_untrusted_first_values(self):
        # Nginx 1.18 reads the first instance; newer versions may combine them.
        # EdgeOne must SET a single header, never append a key to caller input.
        result = self.request(key="wrong", extra=(("X-Easy-SWU-Origin-Key", KEY),))
        self.assert_origin(result, "127.0.0.1")
        result = self.request(ip="invalid", extra=(("EO-Connecting-IP", "203.0.113.10"),))
        self.assert_origin(result, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
