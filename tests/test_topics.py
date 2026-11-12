"""The broker ACL (services/common/topics.py) against what the code does."""
import ast
import pathlib
import unittest

from services.common import topics

ROOT = pathlib.Path(__file__).resolve().parent.parent
VARS = ROOT / "deploy/ansible/roles/mosquitto/vars/main.yml"

# service package -> (broker role, sensor name)
SERVICES = {
    "body": ("body", None),
    "arbiter": ("arbiter", None),
    "agent": ("agent", None),
    "web": ("ui", None),
    "voice": ("voice", None),
    "display": ("display", None),
    "vision": ("sensor", "vision"),
    "sensor_speech": ("sensor", "speech"),
    "sensor_gamepad": ("sensor", "gamepad"),
    "sensor_ultrasonic": ("sensor", "ultrasonic"),
}


# Subscriptions built at run time, checked by a dedicated test instead.
DYNAMIC = {("body", "__main__.py")}         # adapter.topics, see test_body_subscriptions


def constants(tree):
    """NAME = "str" or ("str", ...) at module level and in class bodies."""
    found = {}
    bodies = [tree.body] + [n.body for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    for node in (n for body in bodies for n in body):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found[node.targets[0].id] = (value.value,)
            elif isinstance(value, ast.Tuple) and all(isinstance(e, ast.Constant) for e in value.elts):
                found[node.targets[0].id] = tuple(e.value for e in value.elts)
    return found


def resolve(node, consts, loops):
    """Topics an argument can stand for; None if not statically known."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.Name):
        if node.id in consts:
            return consts[node.id]
        if node.id in loops:
            return loops[node.id]
    if isinstance(node, ast.Attribute) and node.attr in consts:
        return consts[node.attr]                 # self.topic / Scan.map_topic (class constants)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and isinstance(node.left, ast.Constant):
        return (node.left.value + "+",)        # "event/" + kind -> event/+
    return None


def scan_service(package):
    """(writes, reads, unresolved) found in services/<package>/*.py."""
    writes, reads, unresolved = set(), set(), []
    for path in sorted((ROOT / "services" / package).glob("*.py")):
        tree = ast.parse(path.read_text())
        consts = constants(tree)
        loops = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                values = resolve(node.iter, consts, {})
                if values:
                    loops[node.target.id] = values
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name in ("publish", "subscribe") and node.args:
                values = resolve(node.args[0], consts, loops)
                if values is None:
                    if (package, path.name) not in DYNAMIC:
                        unresolved.append("%s:%d" % (path.name, node.lineno))
                    continue
                (writes if name == "publish" else reads).update(values)
            elif name in ("Bus", "make_bus", "NullBus"):
                status = [k.value for k in node.keywords if k.arg == "status_topic"]
                if len(node.args) >= 3:
                    status.append(node.args[2])
                topic = resolve(status[0], consts, loops) if status else ("status",)
                writes.update(topic or ())
    return writes, reads, unresolved


class AclTest(unittest.TestCase):

    def test_generated_vars_are_current(self):
        self.assertEqual(VARS.read_text(), topics.ansible_vars(),
                         "run: python -m services.common.topics > %s" % VARS.relative_to(ROOT))

    def test_covers(self):
        self.assertTrue(topics.covers("sensor/#", "sensor/ultrasonic/distance"))
        self.assertTrue(topics.covers("sensor/#", "sensor/#"))
        self.assertTrue(topics.covers("#", "anything/at/all"))
        self.assertTrue(topics.covers("event/#", "event/+"))
        self.assertFalse(topics.covers("cmd/stop", "cmd/#"))
        self.assertFalse(topics.covers("sensor/vision/#", "sensor/ultrasonic/distance"))
        self.assertFalse(topics.covers("state/motion", "state"))

    def test_services_stay_within_their_role(self):
        for package, (role, sensor) in SERVICES.items():
            writes, reads, unresolved = scan_service(package)
            with self.subTest(service=package):
                self.assertEqual(unresolved, [], "topic not statically known")
                for topic in writes:
                    self.assertTrue(topics.allowed(role, "write", topic, sensor), "%s may not publish %s" % (package, topic))
                for topic in reads:
                    self.assertTrue(topics.allowed(role, "read", topic, sensor), "%s may not read %s" % (package, topic))

    def test_body_subscriptions(self):
        from services.body.adapter import BodyAdapter
        body = BodyAdapter(executor=None, publish=lambda *m: None)
        for topic in body.topics:
            self.assertTrue(topics.allowed("body", "read", topic), topic)

    def test_only_the_arbiter_may_move_the_body(self):
        movers = [role for role in topics.ACL if topics.allowed(role, "write", "cmd/action", "x")]
        self.assertEqual(movers, ["arbiter"])
        self.assertTrue(all(topics.allowed(role, "write", "cmd/stop") for role in ("arbiter", "ui", "agent")))
        self.assertFalse(topics.allowed("sensor", "write", "cmd/stop", "gamepad"))

    def test_scanner_sees_the_known_topics(self):
        writes, reads, _ = scan_service("agent")
        self.assertIn("agent/goal", writes)
        self.assertIn("cmd/stop", writes)
        self.assertIn("arbiter/rejected", reads)
        writes, _, _ = scan_service("arbiter")
        self.assertLessEqual({"cmd/action", "cmd/heartbeat", "arbiter/rejected", "map/local", "arbiter/status"}, writes)


if __name__ == "__main__":
    unittest.main()
