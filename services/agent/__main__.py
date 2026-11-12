"""Agent service: offline language model with robot tools.

    python -m services.agent --config /etc/picrawler/agent.toml [--fake]

Brains in order of preference, from agent.toml:
  [remote]  OpenAI-compatible server in the LAN (optional, e.g. a Mac)
  [litert]  Gemma 4 E2B on the Pi via LiteRT-LM
  keyword parser, always last
--fake uses only the keyword parser.
"""
import signal
import threading

from services.common import config
from services.common.mqtt import make_bus

from .agent import Agent
from .brains import BrainUnavailable, KeywordBrain, LiteRtBrain, OpenAiBrain
from .tools import build_tools
from .world import World

SUBSCRIPTIONS = ("status", "state/motion", "event/rejected", "event/stop", "sensor/vision/detections",
                 "sensor/ultrasonic/distance", "sensor/speech/text", "agent/ask")


def make_brains(cfg, tools, fake, language="de"):
    brains = []
    if not fake:
        remote = cfg.get("remote", {})
        if remote.get("url"):
            brains.append(OpenAiBrain(remote["url"], remote.get("model", "default"), tools,
                                      timeout=remote.get("timeout_s", 20)))
        litert = cfg.get("litert", {})
        if litert.get("model"):
            try:
                brains.append(LiteRtBrain(litert["model"], tools, cache_dir=litert.get("cache_dir")))
                print("agent: loaded %s" % litert["model"])
            except BrainUnavailable as e:
                print("agent: LiteRT-LM not available, keyword parser only: %s" % e)
    brains.append(KeywordBrain(tools, language))
    return brains


def main():
    args = config.arg_parser("PiCrawler agent").parse_args()
    cfg = config.load(args.config)
    agent_cfg = cfg.get("agent", {})

    bus = make_bus(cfg, "picrawler-agent", "agent/status", fake=args.fake)
    world = World(bus.publish)
    tools = build_tools(world, speed=agent_cfg.get("speed", 60), log=lambda t: print("tool:", t))
    agent = Agent(bus.publish, make_brains(cfg, tools, args.fake, agent_cfg.get("language", "de")), agent_cfg,
                  world=world)
    for topic in SUBSCRIPTIONS:
        bus.subscribe(topic, qos=0 if topic.startswith("sensor/vision") else 1)
    bus.on_message = agent.on_message
    bus.on_connect = agent.status

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    bus.start()
    worker = threading.Thread(target=agent.run, daemon=True)
    worker.start()
    print("agent: running with %s" % ", ".join(b.name for b in agent.brains))
    stopping.wait()
    agent.stop()
    worker.join(5)
    for brain in agent.brains:
        close = getattr(brain, "close", None)
        if close:
            close()
    bus.stop()


if __name__ == "__main__":
    main()
