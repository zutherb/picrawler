import http.server
import json
import threading
import unittest

from services.agent.agent import Agent
from services.agent.brains import BrainUnavailable, KeywordBrain, OpenAiBrain
from services.agent.tools import TOOL_SCHEMAS, build_tools
from services.agent.world import World


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeBody:
    """Answers commands like the body would, synchronously."""

    def __init__(self):
        self.world = None
        self.sent = []
        self.outcome = "idle"           # idle | rejected | silent
        self.on_turn = None             # hook to change the world after a turn

    def publish(self, suffix, payload, qos=0, retain=False):
        self.sent.append((suffix, payload))
        if suffix != "agent/goal" or self.outcome == "silent":
            return
        if self.on_turn and payload["name"].startswith("turn"):
            self.on_turn()
        if self.outcome == "rejected":
            self.world.on_message("event/rejected", {"id": payload["id"], "reason": "locked out"})
        else:
            self.world.on_message("state/motion", {"status": "idle", "command_id": payload["id"]})

    def actions(self):
        return [(p["name"], p["steps"]) for s, p in self.sent if s == "agent/goal"]


def make_world():
    body = FakeBody()
    mono = Clock(100.0)
    world = World(body.publish, mono=mono)
    body.world = world
    world.on_message("status", {"status": "online"})
    world.on_message("arbiter/status", {"status": "online"})
    return body, world, mono


class WorldAndToolsTest(unittest.TestCase):

    def setUp(self):
        self.body, self.world, self.mono = make_world()
        self.tools = {fn.__name__: fn for fn in build_tools(self.world)}

    def test_act_waits_for_its_command(self):
        self.assertEqual(self.world.act("forward", 2), ("done", None))
        self.body.outcome = "rejected"
        self.assertEqual(self.world.act("forward", 1), ("rejected", "locked out"))
        self.body.outcome = "silent"
        self.assertEqual(self.world.act("forward", 1, timeout=0.05)[0], "timeout")

    def test_body_offline(self):
        self.world.on_message("status", {"status": "offline"})
        self.assertEqual(self.world.act("forward")[0], "failed")
        self.assertEqual(self.body.sent, [])

    def test_arbiter_offline_or_refusing(self):
        self.world.on_message("arbiter/status", {"status": "offline"})
        self.assertEqual(self.world.act("forward"), ("failed", "the arbiter is offline"))
        self.assertEqual(self.body.sent, [])
        self.world.on_message("arbiter/status", {"status": "online"})
        self.body.outcome = "silent"
        timer = threading.Timer(0.05, lambda: self.world.on_message(
            "arbiter/rejected", {"id": self.body.sent[-1][1]["id"], "reason": "manual control active"}))
        timer.start()
        self.assertEqual(self.world.act("forward", 1, timeout=2), ("rejected", "manual control active"))

    def test_stop_event_resolves_waiter(self):
        self.body.outcome = "silent"
        timer = threading.Timer(0.05, lambda: self.world.on_message("event/stop", {"reason": "obstacle"}))
        timer.start()
        self.assertEqual(self.world.act("forward", 1, timeout=2), ("stopped", "obstacle"))

    def test_walk_clamps_and_respects_obstacle(self):
        self.assertIn("Walked forward 5", self.tools["walk"]("forward", 50))
        self.world.on_message("sensor/ultrasonic/distance", {"cm": 12})
        self.assertIn("obstacle", self.tools["walk"]("forward", 1))
        self.assertIn("Walked backward", self.tools["walk"]("backward", 1))
        self.assertIn("must be", self.tools["walk"]("sideways", 1))
        self.assertEqual(self.body.actions(), [("forward", 5), ("backward", 1)])

    def test_search_turns_until_found(self):
        turns = []

        def appear():
            turns.append(1)
            if len(turns) == 3:
                self.world.on_message("sensor/vision/detections",
                                      {"objects": [{"label": "cat", "score": 0.8, "box": [0.4, 0.4, 0.6, 0.9]}]})

        self.body.on_turn = appear
        result = self.tools["search"]("cat")
        self.assertIn("Found cat after 3 turn(s)", result)
        self.assertIn("cat on the center", result)
        self.assertEqual(self.body.actions(), [("turn left", 1)] * 3)

    def test_look_and_stale_detections(self):
        self.assertIn("nothing", self.tools["look"]().lower())
        self.world.on_message("sensor/vision/detections",
                              {"objects": [{"label": "person", "score": 0.9, "box": [0.0, 0.1, 0.2, 0.9]}]})
        self.assertIn("person on the left, close", self.tools["look"]())
        self.mono.t += 5
        self.assertIn("offline", self.tools["look"]())

    def test_schemas_match_tools(self):
        self.assertEqual({t["function"]["name"] for t in TOOL_SCHEMAS}, set(self.tools))


class KeywordBrainTest(unittest.TestCase):

    def setUp(self):
        self.body, self.world, _ = make_world()
        self.brain = KeywordBrain(build_tools(self.world))

    def test_commands(self):
        cases = [("geh zwei Schritte vorwärts", ("forward", 2)), ("dreh dich nach links", ("turn left", 1)),
                 ("3 Schritte zurück", ("backward", 3)), ("setz dich", ("sit", 1)), ("wink mal", ("wave", 1)),
                 ("turn right", ("turn right", 1)), ("dreh dich zweimal nach rechts", ("turn right", 2))]
        for text, expected in cases:
            self.body.sent.clear()
            self.brain.ask(text)
            self.assertEqual(self.body.actions(), [expected], text)

    def test_movement_is_confirmed_briefly(self):
        self.assertEqual(self.brain.ask("geh vorwärts"), "Okay.")

    def test_stop_search_and_unknown(self):
        self.brain.ask("stopp")
        self.assertEqual(self.body.sent[-1][0], "cmd/stop")
        self.world.on_message("sensor/vision/detections", {"objects": [{"label": "cat", "score": 0.9,
                                                                         "box": [0.4, 0.4, 0.6, 0.6]}]})
        self.assertIn("Found cat", self.brain.ask("such die Katze"))
        self.assertEqual(self.brain.ask("erzähl einen Witz"), "Das habe ich nicht verstanden.")


class AgentTest(unittest.TestCase):

    def setUp(self):
        self.sent = []
        self.asked = []
        agent_self = self

        class RecordingBrain:
            name = "recording"

            def ask(self, text, context=""):
                agent_self.asked.append(text)
                return "ok"

        self.world = World(lambda *m: self.sent.append(m))
        self.agent = Agent(lambda *m: self.sent.append(m), [RecordingBrain()], {"language": "en"}, world=self.world)

    def process(self):
        while not self.agent._queue.empty():
            text, source = self.agent._queue.get_nowait()
            reply, brain = self.agent.think(text)
            self.agent.reply(reply, text, source, brain)

    def replies(self):
        return [m[1] for m in self.sent if m[0] == "agent/reply"]

    def test_wake_word(self):
        strip = self.agent.strip_wake_word
        self.assertEqual(strip("Crawler, geh vorwärts"), "geh vorwärts")
        self.assertEqual(strip("Hey Crawler such die Katze"), "such die Katze")
        self.assertIsNone(strip("Was gibt es heute zum Essen"))
        self.assertIsNone(strip("Crawler"))

    def test_speech_needs_wake_word_web_does_not(self):
        self.agent.on_message("sensor/speech/text", {"text": "geh vorwärts"})
        self.agent.on_message("sensor/speech/text", {"text": "Crawler, geh vorwärts"})
        self.agent.on_message("agent/ask", {"text": "wave please", "user": "anna"})
        self.process()
        self.assertEqual(self.asked, ["geh vorwärts", "wave please"])
        self.assertEqual([r["source"] for r in self.replies()], ["speech", "anna"])

    def test_stop_is_immediate_and_drains_queue(self):
        self.agent.on_message("agent/ask", {"text": "walk forward"})
        self.agent.on_message("sensor/speech/text", {"text": "Crawler stopp!"})
        self.process()
        self.assertEqual(self.asked, [])
        self.assertIn("cmd/stop", [m[0] for m in self.sent])
        self.assertEqual(self.replies()[0]["brain"], "fast path")

    def test_retained_and_garbage_ignored(self):
        self.agent.on_message("agent/ask", {"text": "walk"}, retain=True)
        self.agent.on_message("agent/ask", {"text": ""})
        self.agent.on_message("agent/ask", None)
        self.assertTrue(self.agent._queue.empty())

    def test_fallback_to_next_brain(self):
        class Down:
            name = "down"

            def ask(self, text, context=""):
                raise BrainUnavailable("offline")

        self.agent.brains.insert(0, Down())
        self.assertEqual(self.agent.think("hello"), ("ok", "recording"))


class StubChatServer(http.server.BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        StubChatServer.calls.append(body)
        if not any(m["role"] == "tool" for m in body["messages"]):
            message = {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "walk",
                                                              "arguments": '{"direction": "forward", "steps": 2}'}}]}
        else:
            message = {"role": "assistant", "content": "Bin zwei Schritte gelaufen."}
        data = json.dumps({"choices": [{"message": message}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class OpenAiBrainTest(unittest.TestCase):

    def test_tool_round_trip(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), StubChatServer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body, world, _ = make_world()
            brain = OpenAiBrain("http://127.0.0.1:%d" % server.server_address[1], "gemma", build_tools(world))
            self.assertEqual(brain.ask("geh zwei Schritte"), "Bin zwei Schritte gelaufen.")
            self.assertEqual(body.actions(), [("forward", 2)])
            tool_msg = [m for m in StubChatServer.calls[-1]["messages"] if m["role"] == "tool"][0]
            self.assertIn("Walked forward 2", tool_msg["content"])
            self.assertEqual(StubChatServer.calls[0]["tools"], TOOL_SCHEMAS)
        finally:
            server.shutdown()
            server.server_close()

    def test_unreachable_is_skipped_for_a_while(self):
        _, world, _ = make_world()
        brain = OpenAiBrain("http://127.0.0.1:9", "x", build_tools(world), timeout=1)
        with self.assertRaises(BrainUnavailable):
            brain.ask("hi")
        with self.assertRaises(BrainUnavailable) as cm:
            brain.ask("hi")
        self.assertIn("skipped", str(cm.exception))

    def test_bad_tool_arguments(self):
        _, world, _ = make_world()
        brain = OpenAiBrain("http://127.0.0.1:9", "x", build_tools(world))
        self.assertIn("unknown tool", brain.run_tool({"name": "rm"}))
        self.assertIn("bad arguments", brain.run_tool({"name": "walk", "arguments": "{not json"}))
        self.assertIn("bad arguments", brain.run_tool({"name": "walk", "arguments": '{"speed": 1}'}))


if __name__ == "__main__":
    unittest.main()
