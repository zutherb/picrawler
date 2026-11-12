"""HTTP-level tests of the web service. Skipped when aiohttp is not installed."""
import unittest

try:
    from aiohttp.test_utils import AioHTTPTestCase
except ImportError:  # pragma: no cover
    AioHTTPTestCase = None

if AioHTTPTestCase is not None:
    from services.web.app import Hub, WebApp
    from services.web.auth import RateLimiter, Sessions, Users, hash_password
    from services.web.bluetooth import FakeBluetooth
    from services.web.monitor import MqttMonitor
    from services.web.sightings import SightingTracker
    from services.web.state import LiveState
    from services.web.store import LogStore
    from services.web.system import FakeSystem

    PASSWORDS = {"anna": "admin-pass-123", "gus": "viewer-pass-123"}
    USERS = Users({
        "anna": {"role": "admin", "password_hash": hash_password(PASSWORDS["anna"])},
        "gus": {"role": "viewer", "password_hash": hash_password(PASSWORDS["gus"])},
    })

    class RecordingBus:
        prefix = "crawler/1/"

        def __init__(self):
            self.sent = []

        def publish(self, suffix, payload, qos=0, retain=False):
            self.sent.append((suffix, payload))

    @unittest.skipIf(AioHTTPTestCase is None, "aiohttp not installed")
    class WebAppTest(AioHTTPTestCase):

        async def get_application(self):
            self.bus = RecordingBus()
            self.system = FakeSystem(["picrawler-body.service"])
            store = LogStore(":memory:")
            self.addCleanup(store.close)
            hub = Hub()
            state = LiveState(store, SightingTracker(), hub.push)
            self.monitor = MqttMonitor()
            self.webapp = WebApp({"http": {"vision_stream": "http://127.0.0.1:9/none"}}, USERS, Sessions(),
                                 RateLimiter(limit=3), state, hub, self.bus, FakeBluetooth(delay=0),
                                 self.system, store, monitor=self.monitor)
            return self.webapp.build()

        async def login(self, user):
            self.client.session.cookie_jar.clear()
            resp = await self.client.post("/login", data={"user": user, "password": PASSWORDS[user]},
                                          allow_redirects=False)
            self.assertEqual(resp.status, 302)
            self.assertEqual(resp.headers["Location"], "/")
            me = await (await self.client.get("/api/me")).json()
            return me["csrf"]

        async def post(self, path, body, csrf):
            return await self.client.post(path, json=body, headers={"X-CSRF-Token": csrf} if csrf else {})

        async def test_requires_login(self):
            resp = await self.client.get("/", allow_redirects=False)
            self.assertEqual((resp.status, resp.headers["Location"]), (302, "/login"))
            self.assertEqual((await self.client.get("/api/log")).status, 401)
            self.assertEqual((await self.client.post("/api/stop", json={})).status, 401)
            self.assertEqual((await self.client.get("/login")).status, 200)
            self.assertEqual((await self.client.get("/static/app.css")).status, 200)

        async def test_wrong_password_and_rate_limit(self):
            for _ in range(3):
                resp = await self.client.post("/login", data={"user": "anna", "password": "x"}, allow_redirects=False)
                self.assertEqual(resp.headers["Location"], "/login?error=1")
            resp = await self.client.post("/login", data={"user": "anna", "password": PASSWORDS["anna"]},
                                          allow_redirects=False)
            self.assertEqual(resp.headers["Location"], "/login?error=rate")

        async def test_viewer_can_stop_but_not_admin(self):
            csrf = await self.login("gus")
            resp = await self.post("/api/stop", {"sit": True}, csrf)
            self.assertEqual(resp.status, 200)
            self.assertEqual(self.bus.sent[-1][0], "cmd/stop")
            self.assertEqual(self.bus.sent[-1][1]["sit"], True)
            for path, body in (("/api/vision/mode", {"mode": "off"}), ("/api/system/restart", {"unit": "picrawler-body.service"}),
                               ("/api/bluetooth/scan", {}), ("/api/agent/ask", {"text": "walk"}),
                               ("/api/mqtt/clear", {}), ("/api/remote", {"x": 0, "y": -1}),
                               ("/api/map/scan", {})):
                self.assertEqual((await self.post(path, body, csrf)).status, 403, path)
            self.assertEqual((await self.client.get("/api/system")).status, 403)
            self.assertEqual((await self.client.get("/api/mqtt/topics")).status, 403)
            self.assertEqual((await self.client.get("/api/mqtt/messages")).status, 403)
            self.assertEqual((await self.client.get("/mqtt", allow_redirects=False)).status, 302)
            resp = await self.client.get("/admin", allow_redirects=False)
            self.assertEqual(resp.status, 302)

        async def test_csrf_required(self):
            await self.login("anna")
            self.assertEqual((await self.post("/api/stop", {}, None)).status, 403)
            self.assertEqual((await self.post("/api/stop", {}, "forged")).status, 403)
            self.assertEqual(self.bus.sent, [])

        async def test_admin_actions(self):
            csrf = await self.login("anna")
            self.assertEqual((await self.post("/api/vision/mode", {"mode": "pose"}, csrf)).status, 200)
            self.assertEqual(self.bus.sent[-1][1]["mode"], "pose")
            self.assertEqual((await self.post("/api/vision/mode", {"mode": "x"}, csrf)).status, 400)
            self.assertEqual((await self.post("/api/agent/ask", {"text": "such die Katze"}, csrf)).status, 200)
            self.assertEqual(self.bus.sent[-1][0], "agent/ask")
            self.assertEqual(self.bus.sent[-1][1]["user"], "anna")
            self.assertEqual((await self.post("/api/agent/ask", {"text": "  "}, csrf)).status, 400)
            self.assertEqual((await self.post("/api/system/restart", {"unit": "picrawler-body.service"}, csrf)).status, 200)
            self.assertEqual((await self.post("/api/system/restart", {"unit": "ssh.service"}, csrf)).status, 400)
            self.assertEqual(self.system.restarted, ["picrawler-body.service"])
            data = await (await self.client.get("/api/bluetooth")).json()
            self.assertEqual(data["devices"][0]["name"], "SteelSeries Nimbus+")
            self.assertEqual((await self.post("/api/bluetooth/pair", {"mac": "not a mac"}, csrf)).status, 202)
            self.assertEqual((await self.post("/api/bluetooth/explode", {"mac": "AA:BB:CC:00:00:01"}, csrf)).status, 404)
            self.assertEqual((await self.post("/api/bluetooth/remove", {"mac": "zz"}, csrf)).status, 400)
            resp = await self.client.post("/api/stop", data="{", headers={"X-CSRF-Token": csrf,
                                                                          "Content-Type": "application/json"})
            self.assertEqual(resp.status, 400)

        async def test_log_api_and_security_headers(self):
            await self.login("gus")
            self.webapp.store.add_event("stop", "obstacle", 1000.0)
            rows = await (await self.client.get("/api/log?kind=event")).json()
            self.assertEqual(rows[0]["detail"], "obstacle")
            self.assertEqual((await self.client.get("/api/log?since=abc")).status, 400)
            resp = await self.client.get("/api/log.csv")
            self.assertIn("attachment", resp.headers["Content-Disposition"])
            page = await self.client.get("/")
            self.assertIn("script-src 'self'", page.headers["Content-Security-Policy"])
            self.assertEqual((await self.client.get("/static/app.js")).headers["Cache-Control"], "no-cache")
            self.assertEqual(page.headers["X-Frame-Options"], "DENY")

        async def test_display_mirror_api(self):
            await self.login("gus")              # viewers see it on the live page
            self.assertEqual((await self.client.get("/api/display")).status, 404)
            self.webapp.state.on_message("display/frame", {"w": 128, "h": 64, "data": "AA==", "ts": 1.0})
            self.assertEqual((await (await self.client.get("/api/display")).json())["w"], 128)

        async def test_remote_api(self):
            csrf = await self.login("anna")
            self.assertEqual((await self.post("/api/remote", {"x": 0.5, "y": -1, "speed": 60}, csrf)).status, 200)
            topic, payload = self.bus.sent[-1]
            self.assertEqual((topic, payload["x"], payload["y"], payload["speed"], payload["user"]),
                             ("remote/state", 0.5, -1.0, 60, "anna"))
            self.assertEqual((await self.post("/api/remote", {"action": "wave"}, csrf)).status, 200)
            self.assertEqual(self.bus.sent[-1][1]["action"], "wave")
            for bad in ({"x": 2}, {"y": "up"}, {"speed": 0}, {"speed": True}, {"action": "explode"}):
                self.assertEqual((await self.post("/api/remote", bad, csrf)).status, 400, bad)
            self.assertEqual((await self.client.get("/remote")).status, 200)

        async def test_map_scan_api(self):
            csrf = await self.login("anna")
            self.assertEqual((await self.post("/api/map/scan", {}, csrf)).status, 200)
            topic, payload = self.bus.sent[-1]
            self.assertEqual((topic, payload["user"]), ("map/scan", "anna"))

        async def test_mqtt_debug_api(self):
            await self.login("anna")
            self.monitor.record("cmd/stop", b'{"sit": false}', False, 1, 1.0)
            self.monitor.record("sensor/gamepad/state", b'{"x": 0}', False, 0, 2.0)
            data = await (await self.client.get("/api/mqtt/topics")).json()
            self.assertEqual(data["prefix"], "crawler/1/")
            self.assertEqual([t["topic"] for t in data["topics"]], ["cmd/stop", "sensor/gamepad/state"])
            msgs = await (await self.client.get("/api/mqtt/messages?topic=sensor")).json()
            self.assertEqual([m["topic"] for m in msgs], ["sensor/gamepad/state"])
            self.assertEqual((await self.client.get("/api/mqtt/messages?limit=x")).status, 400)
            self.assertEqual((await self.client.get("/mqtt")).status, 200)

        async def test_stream_unavailable_without_vision(self):
            await self.login("gus")
            self.assertEqual((await self.client.get("/stream.mjpg")).status, 503)

        async def test_logout(self):
            csrf = await self.login("gus")
            self.assertEqual((await self.post("/logout", {}, csrf)).status, 200)
            self.assertEqual((await self.client.get("/api/me")).status, 401)


if __name__ == "__main__":
    unittest.main()
