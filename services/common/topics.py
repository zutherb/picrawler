"""Who may publish and subscribe what: the single source for the broker ACL.

Topics are relative to crawler/<robot_id>/. Sensors get their own subtree
via "{sensor}". The Ansible role reads the generated vars file

    python -m services.common.topics > deploy/ansible/roles/mosquitto/vars/main.yml

and tests/test_topics.py fails when that file is stale or when a service
subscribes or publishes something its role may not.
"""
import sys

READ, WRITE = "read", "write"

ACL = {
    # only the body owns state/status; it also watches the ultrasonic
    # sensor for its own obstacle interlock
    "body": [
        (READ, "cmd/#"),
        (READ, "sensor/ultrasonic/distance"),
        (WRITE, "state/#"),
        (WRITE, "status"),
        (WRITE, "event/#"),
    ],
    # the one place that turns facts and requests into commands
    "arbiter": [
        (READ, "sensor/#"),
        (READ, "state/#"),
        (READ, "status"),
        (READ, "event/#"),
        (READ, "agent/status"),
        (READ, "agent/goal"),
        (READ, "remote/state"),
        (READ, "map/scan"),
        (WRITE, "cmd/#"),
        (WRITE, "vision/mode"),
        (WRITE, "arbiter/#"),
        (WRITE, "map/local"),
    ],
    # sensors publish facts in their own subtree, never commands
    "sensor": [
        (WRITE, "sensor/{sensor}/#"),
    ],
    # web UI: reads everything (MQTT debug page), writes requests and stop
    "ui": [
        (READ, "#"),
        (WRITE, "cmd/stop"),
        (WRITE, "vision/mode"),
        (WRITE, "web/status"),
        (WRITE, "agent/ask"),
        (WRITE, "remote/state"),
        (WRITE, "map/scan"),
    ],
    # asks the arbiter for movement (agent/goal) and may always stop
    "agent": [
        (READ, "status"),
        (READ, "arbiter/status"),
        (READ, "arbiter/rejected"),
        (READ, "state/motion"),
        (READ, "event/#"),
        (READ, "sensor/#"),
        (READ, "agent/ask"),
        (WRITE, "cmd/stop"),
        (WRITE, "agent/#"),
    ],
    "voice": [
        (READ, "agent/reply"),
        (WRITE, "voice/#"),
    ],
    "display": [
        (READ, "state/battery"),
        (WRITE, "display/status"),
        (WRITE, "display/frame"),
    ],
}

# Extra rights for single sensors, on top of their own subtree.
SENSOR_EXTRA = {
    "vision": [(READ, "vision/mode")],
    "speech": [(READ, "voice/speaking")],
}


def rules(role, sensor=None):
    """(access, topic) pairs for one broker user."""
    found = list(ACL[role])
    if role == "sensor":
        found = [(access, topic.replace("{sensor}", sensor)) for access, topic in found]
        found += SENSOR_EXTRA.get(sensor, [])
    return found


def covers(pattern, topic):
    """True if ACL pattern covers topic, which may be a subscription filter."""
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t):
            return False
        if part == "+":
            if t[i] == "#":
                return False
            continue
        if part != t[i]:
            return False
    return len(p) == len(t)


def allowed(role, access, topic, sensor=None):
    return any(a == access and covers(pattern, topic) for a, pattern in rules(role, sensor))


def ansible_vars():
    """YAML for deploy/ansible/roles/mosquitto/vars/main.yml (no PyYAML needed)."""
    lines = ["---",
             "# Generated from services/common/topics.py, do not edit. Regenerate with:",
             "#   python -m services.common.topics > deploy/ansible/roles/mosquitto/vars/main.yml",
             "mosquitto_acl:"]
    for role, entries in ACL.items():
        lines.append("  %s:" % role)
        lines += ['    - {access: %s, topic: "%s"}' % (access, topic) for access, topic in entries]
    lines.append("mosquitto_acl_sensor:")
    for sensor, entries in SENSOR_EXTRA.items():
        lines.append("  %s:" % sensor)
        lines += ['    - {access: %s, topic: "%s"}' % (access, topic) for access, topic in entries]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.write(ansible_vars())
