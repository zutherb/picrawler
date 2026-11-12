"""aiohttp application: pages, login, SSE, stream proxy and JSON API."""
import asyncio
import functools
import json
import os
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from services.common.mqtt import encode

from .bluetooth import BluetoothError
from .system import SystemActionError

STATIC = os.path.join(os.path.dirname(__file__), "static")
SESSION = web.RequestKey("session", dict) if hasattr(web, "RequestKey") else "session"
COOKIE = "picrawler_session"
PUBLIC_PATHS = ("/login",)
VISION_MODES = ("detect", "pose", "imprint", "off")
BT_ACTIONS = ("pair", "connect", "disconnect", "remove")
SIT_BEFORE_POWEROFF_S = 3.0
SSE_KEEPALIVE_S = 15.0
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; form-action 'self'")


class Hub:
    """Fan-out of server-sent events to all connected browsers."""

    def __init__(self):
        self.clients = set()

    def push(self, event, data):
        message = "event: %s\ndata: %s\n\n" % (event, encode(data))
        for queue in list(self.clients):
            if queue.full():
                continue   # a slow browser misses updates instead of blocking everyone
            queue.put_nowait(message)


def json_error(status, message):
    return web.json_response({"error": message}, status=status)


def requires_admin(handler):
    @functools.wraps(handler)
    async def wrapper(self, request):
        if request[SESSION]["role"] != "admin":
            return json_error(403, "admin only")
        return await handler(self, request)
    return wrapper


async def read_json(request):
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise web.HTTPBadRequest(text="invalid JSON")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="expected a JSON object")
    return body


class WebApp:

    def __init__(self, cfg, users, sessions, limiter, state, hub, bus, bluetooth, system, store, fake=False):
        self.cfg = cfg
        self.http_cfg = cfg.get("http", {})
        self.users = users
        self.sessions = sessions
        self.limiter = limiter
        self.state = state
        self.hub = hub
        self.bus = bus
        self.bluetooth = bluetooth
        self.system = system
        self.store = store
        self.fake = fake
        self.secure_cookie = bool(self.http_cfg.get("cert"))
        self.vision_stream = self.http_cfg.get("vision_stream", "http://127.0.0.1:8081/stream.mjpg")
        self._tasks = set()

    def build(self):
        app = web.Application(middlewares=[self.security_headers, self.authenticate], client_max_size=64 * 1024)
        r = app.router
        r.add_get("/login", self.login_page)
        r.add_post("/login", self.login)
        r.add_post("/logout", self.logout)
        r.add_get("/", self.page("live.html"))
        r.add_get("/log", self.page("log.html"))
        r.add_get("/admin", self.page("admin.html", admin=True))
        r.add_get("/stream.mjpg", self.stream)
        r.add_get("/events", self.events)
        r.add_get("/api/me", self.me)
        r.add_get("/api/log", self.log_json)
        r.add_get("/api/log.csv", self.log_csv)
        r.add_get("/api/log/labels", self.log_labels)
        r.add_post("/api/stop", self.stop)
        r.add_post("/api/vision/mode", self.vision_mode)
        r.add_post("/api/agent/ask", self.agent_ask)
        r.add_get("/api/bluetooth", self.bt_devices)
        r.add_post("/api/bluetooth/scan", self.bt_scan)
        r.add_post("/api/bluetooth/{action}", self.bt_action)
        r.add_get("/api/system", self.system_info)
        r.add_get("/api/system/journal", self.system_journal)
        r.add_post("/api/system/restart", self.system_restart)
        r.add_post("/api/system/power", self.system_power)
        r.add_static("/static/", STATIC)
        return app

    # --- middleware ---

    @web.middleware
    async def security_headers(self, request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as e:
            response = e
        if not response.prepared:
            response.headers.setdefault("Content-Security-Policy", CSP)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault("X-Frame-Options", "DENY")
        if isinstance(response, web.HTTPException):
            raise response
        return response

    @web.middleware
    async def authenticate(self, request, handler):
        path = request.path
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return await handler(request)
        session = self.sessions.get(request.cookies.get(COOKIE))
        if session is None:
            if path.startswith("/api/") or path in ("/events", "/stream.mjpg") or request.method != "GET":
                return json_error(401, "login required")
            raise web.HTTPFound("/login")
        if request.method == "POST" and request.headers.get("X-CSRF-Token") != session["csrf"]:
            return json_error(403, "missing or wrong CSRF token")
        request[SESSION] = session
        return await handler(request)

    # --- login ---

    async def login_page(self, request):
        return web.FileResponse(os.path.join(STATIC, "login.html"))

    async def login(self, request):
        if not self.limiter.allow(request.remote or "?"):
            raise web.HTTPFound("/login?error=rate")
        form = await request.post()
        user, password = str(form.get("user", "")), str(form.get("password", ""))
        role = await asyncio.get_running_loop().run_in_executor(None, self.users.check, user, password)
        if role is None:
            raise web.HTTPFound("/login?error=1")
        token = self.sessions.create(user, role)
        response = web.HTTPFound("/")
        response.set_cookie(COOKIE, token, httponly=True, samesite="Strict", secure=self.secure_cookie, path="/")
        raise response

    async def logout(self, request):
        self.sessions.drop(request.cookies.get(COOKIE))
        response = web.json_response({"ok": True})
        response.del_cookie(COOKIE, path="/")
        return response

    async def me(self, request):
        s = request[SESSION]
        return web.json_response({"user": s["user"], "role": s["role"], "csrf": s["csrf"]})

    # --- pages ---

    def page(self, name, admin=False):
        async def handler(request):
            if admin and request[SESSION]["role"] != "admin":
                raise web.HTTPFound("/")
            return web.FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-store"})
        return handler

    # --- live data ---

    async def events(self, request):
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
        await response.prepare(request)
        queue = asyncio.Queue(maxsize=200)
        self.hub.clients.add(queue)
        try:
            await response.write(("event: status\ndata: %s\n\n" % encode(self.state.snapshot)).encode())
            recent = list(reversed(self.store.query(limit=30)))
            await response.write(("event: history\ndata: %s\n\n" % encode(recent)).encode())
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), SSE_KEEPALIVE_S)
                except asyncio.TimeoutError:
                    message = ": keepalive\n\n"
                await response.write(message.encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.hub.clients.discard(queue)
        return response

    async def stream(self, request):
        response = None
        try:
            async with ClientSession(timeout=ClientTimeout(total=None, sock_connect=3)) as client:
                async with client.get(self.vision_stream) as upstream:
                    if upstream.status != 200:
                        return json_error(503, "vision stream unavailable")
                    response = web.StreamResponse(headers={
                        "Content-Type": upstream.headers.get("Content-Type", "multipart/x-mixed-replace"),
                        "Cache-Control": "no-store"})
                    await response.prepare(request)
                    async for chunk in upstream.content.iter_any():
                        await response.write(chunk)
        except (ClientError, OSError, asyncio.TimeoutError):
            # Before streaming started: the vision service is not reachable.
            # After that: the browser went away, nothing to do.
            if response is None:
                if self.fake:
                    return await self._fake_stream(request)
                return json_error(503, "vision stream unavailable")
        return response

    async def _fake_stream(self, request):
        from services.vision.camera import TESTCARD
        with open(TESTCARD, "rb") as f:
            frame = f.read()
        response = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"})
        await response.prepare(request)
        try:
            while True:
                await response.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n"
                                     % (len(frame), frame))
                await asyncio.sleep(0.2)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    # --- log ---

    def _log_query(self, request):
        q = request.query

        def number(name):
            try:
                return float(q[name]) if q.get(name) else None
            except ValueError:
                raise web.HTTPBadRequest(text="%s must be a number" % name)

        try:
            limit = int(q.get("limit", 500))
        except ValueError:
            raise web.HTTPBadRequest(text="limit must be an integer")
        kind = q.get("kind") if q.get("kind") in ("sighting", "event") else None
        return self.store.query(label=q.get("label") or None, since=number("since"), until=number("until"),
                                min_score=number("min_score"), kind=kind, limit=limit)

    async def log_json(self, request):
        return web.json_response(self._log_query(request))

    async def log_csv(self, request):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return web.Response(text=self.store.to_csv(self._log_query(request)), content_type="text/csv",
                            headers={"Content-Disposition": 'attachment; filename="picrawler-log-%s.csv"' % stamp})

    async def log_labels(self, request):
        return web.json_response(self.store.labels())

    # --- commands ---

    async def stop(self, request):
        body = await read_json(request)
        self.send_stop(sit=body.get("sit") is True, reason="web:%s" % request[SESSION]["user"])
        return web.json_response({"ok": True})

    def send_stop(self, sit, reason):
        self.bus.publish("cmd/stop", {"id": os.urandom(8).hex(), "sit": sit, "reason": reason[:64],
                                      "ts": time.time()}, 1, False)

    @requires_admin
    async def vision_mode(self, request):
        mode = (await read_json(request)).get("mode")
        if mode not in VISION_MODES:
            return json_error(400, "unknown mode")
        self.bus.publish("vision/mode", {"id": os.urandom(8).hex(), "mode": mode, "ts": time.time()}, 1, False)
        return web.json_response({"ok": True})

    @requires_admin
    async def agent_ask(self, request):
        text = (await read_json(request)).get("text")
        if not isinstance(text, str) or not text.strip():
            return json_error(400, "text required")
        self.bus.publish("agent/ask", {"id": os.urandom(8).hex(), "text": text.strip()[:500],
                                       "user": request[SESSION]["user"], "ts": time.time()}, 1, False)
        return web.json_response({"ok": True})

    # --- bluetooth ---

    @requires_admin
    async def bt_devices(self, request):
        try:
            devices = await self.bluetooth.devices()
        except (BluetoothError, OSError) as e:
            return json_error(502, str(e))
        return web.json_response({"scanning": self.bluetooth.scanning, "devices": devices})

    @requires_admin
    async def bt_scan(self, request):
        if self.bluetooth.scanning:
            return json_error(409, "a scan is already running")
        seconds = (await read_json(request)).get("seconds", 20)
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            return json_error(400, "seconds must be an integer")
        self._background("scan", None, self.bluetooth.scan(seconds))
        return web.json_response({"ok": True, "scanning": True}, status=202)

    @requires_admin
    async def bt_action(self, request):
        action = request.match_info["action"]
        if action not in BT_ACTIONS:
            return json_error(404, "unknown action")
        mac = (await read_json(request)).get("mac")
        if not isinstance(mac, str):
            return json_error(400, "mac required")
        if action in ("pair", "connect"):
            # can take up to a minute: answer now, report via SSE
            self._background(action, mac, getattr(self.bluetooth, action)(mac))
            return web.json_response({"ok": True, "pending": True}, status=202)
        try:
            result = await getattr(self.bluetooth, action)(mac)
        except (BluetoothError, OSError) as e:
            return json_error(400, str(e))
        return web.json_response(result)

    def _background(self, action, mac, coro):
        async def run():
            try:
                result = await coro
            except (BluetoothError, OSError) as e:
                result = {"ok": False, "error": str(e)}
            result = result or {"ok": True}
            try:
                devices = await self.bluetooth.devices()
            except (BluetoothError, OSError):
                devices = None
            self.hub.push("bluetooth", {"action": action, "mac": mac, "result": result, "devices": devices})

        task = asyncio.ensure_future(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- system ---

    @requires_admin
    async def system_info(self, request):
        return web.json_response({"services": await self.system.services(), "info": await self.system.info()})

    @requires_admin
    async def system_journal(self, request):
        try:
            text = await self.system.journal(request.query.get("unit", ""), request.query.get("lines", 100))
        except (SystemActionError, ValueError) as e:
            return json_error(400, str(e))
        return web.Response(text=text)

    @requires_admin
    async def system_restart(self, request):
        unit = (await read_json(request)).get("unit")
        try:
            return web.json_response(await self.system.restart(unit))
        except SystemActionError as e:
            return json_error(400, str(e))

    @requires_admin
    async def system_power(self, request):
        action = (await read_json(request)).get("action")
        if action not in ("poweroff", "reboot"):
            return json_error(400, "unknown power action")
        self.send_stop(sit=True, reason="web:%s" % action)
        await asyncio.sleep(SIT_BEFORE_POWEROFF_S)
        try:
            return web.json_response(await self.system.power(action))
        except SystemActionError as e:
            return json_error(500, str(e))
