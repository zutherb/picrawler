"""Thin wrapper around paho-mqtt shared by all services.

Services never touch paho directly: they get a Bus with publish() and a
message callback, which makes their logic testable without a broker.
"""
import json
import time

from . import config


def now():
    return time.time()


def encode(payload):
    return json.dumps(payload, separators=(",", ":"))


class Bus:
    """MQTT connection with topic prefix, JSON payloads and a status LWT."""

    def __init__(self, cfg, client_id, status_topic="status"):
        import paho.mqtt.client as paho  # imported lazily: tests run without paho

        self.prefix = config.topic_prefix(cfg)
        self._status_topic = self.prefix + status_topic
        mqtt_cfg = cfg.get("mqtt", {})
        self._host = mqtt_cfg.get("host", "localhost")
        self._port = int(mqtt_cfg.get("port", 1883))

        self.on_message = None      # fn(suffix, payload_dict, retain)
        self.on_connect = None      # fn()
        self.on_disconnect = None   # fn()
        self._subscriptions = []

        self._client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=client_id)
        username = mqtt_cfg.get("username")
        if username:
            self._client.username_pw_set(username, config.read_password(mqtt_cfg))
        if mqtt_cfg.get("tls"):
            self._client.tls_set()
        self._client.will_set(self._status_topic, encode({"status": "offline"}), qos=1, retain=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message

    def subscribe(self, suffix, qos=1):
        self._subscriptions.append((self.prefix + suffix, qos))

    def publish(self, suffix, payload, qos=0, retain=False):
        self._client.publish(self.prefix + suffix, encode(payload), qos=qos, retain=retain)

    def publish_status(self, status, **extra):
        payload = {"status": status, "ts": now(), **extra}
        return self._client.publish(self._status_topic, encode(payload), qos=1, retain=True)

    def start(self):
        self._client.connect_async(self._host, self._port, keepalive=10)
        self._client.loop_start()

    def stop(self, status="offline"):
        self.on_disconnect = None   # leaving on purpose, not a connection loss
        try:
            info = self.publish_status(status)
            info.wait_for_publish(timeout=2)
            # Some brokers still process in-flight messages after the PUBACK;
            # disconnecting at once can drop the final retained status.
            time.sleep(0.2)
        except Exception:
            pass
        self._client.disconnect()
        self._client.loop_stop()

    # --- paho callbacks (network thread) ---

    def _handle_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print("MQTT connect failed: %s" % reason_code)
            return
        for topic, qos in self._subscriptions:
            client.subscribe(topic, qos)
        if self.on_connect:
            self.on_connect()

    def _handle_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.on_disconnect:
            self.on_disconnect()

    def _handle_message(self, client, userdata, msg):
        if self.on_message is None or not msg.topic.startswith(self.prefix):
            return
        suffix = msg.topic[len(self.prefix):]
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = None
        self.on_message(suffix, payload, bool(msg.retain))
