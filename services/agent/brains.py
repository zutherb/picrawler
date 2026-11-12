"""Language model backends. Each turns a request into a reply, calling tools.

- LiteRtBrain: Gemma 4 E2B (or any .litertlm model) on the Pi via LiteRT-LM.
  LiteRT-LM executes the Python tool functions itself.
- OpenAiBrain: any OpenAI-compatible chat server with tool calls, e.g.
  llama-server or Ollama on a Mac in the home network. No internet needed.
- KeywordBrain: deterministic German/English command parser. Always
  available; the fallback when no model is loaded or reachable.
"""
import json
import re
import time
import urllib.error
import urllib.request

from .tools import TOOL_SCHEMAS

SYSTEM_PROMPT = (
    "You are the brain of PiCrawler, a small four-legged robot. "
    "You get commands from people, usually in German. Act by calling tools; "
    "keep replies to one or two short sentences in the language of the request. "
    "Use look before saying what you see. Never invent tool results. "
    "Safety reflexes (obstacle stop) are handled elsewhere; if a tool reports "
    "a stop or refusal, say so instead of retrying more than once."
)
MAX_TOOL_ROUNDS = 6
RETRY_REMOTE_AFTER_S = 30.0


class BrainUnavailable(Exception):
    pass


class LiteRtBrain:
    """LiteRT-LM Python API (pip install litert-lm-api)."""

    name = "litert-lm"

    def __init__(self, model_path, tools, cache_dir=None, system_prompt=SYSTEM_PROMPT):
        try:
            import litert_lm
        except ImportError as e:
            raise BrainUnavailable("litert-lm-api not installed: %s" % e)
        self._litert_lm = litert_lm
        kwargs = {"backend": litert_lm.Backend.CPU()}
        if cache_dir:
            kwargs["cache_dir"] = cache_dir
        try:
            self._engine = litert_lm.Engine(model_path, **kwargs)
        except Exception as e:
            raise BrainUnavailable("cannot load %s: %s" % (model_path, e))
        self._tools = tools
        self._system_prompt = system_prompt

    def ask(self, text, context=""):
        lm = self._litert_lm
        messages = [lm.Message.system(self._system_prompt)]
        prompt = "%s\n\n%s" % (context, text) if context else text
        # A fresh conversation per request keeps the prompt short; prefill
        # time on the Pi grows with every token of history.
        with self._engine.create_conversation(messages=messages, tools=self._tools) as conversation:
            response = conversation.send_message(prompt)
        return "".join(part.get("text", "") for part in response.get("content", []) if isinstance(part, dict)).strip()

    def close(self):
        close = getattr(self._engine, "close", None)
        if close:
            close()


class OpenAiBrain:
    """OpenAI-compatible /v1/chat/completions with tool calls."""

    name = "openai-compatible"

    def __init__(self, url, model, tools, timeout=60.0, api_key=None, system_prompt=SYSTEM_PROMPT):
        self.url = url.rstrip("/") + "/v1/chat/completions" if not url.endswith("/completions") else url
        self.model = model
        self.timeout = timeout
        self.api_key = api_key
        self._tools = {fn.__name__: fn for fn in tools}
        self._system_prompt = system_prompt
        self._down_until = 0.0

    def _post(self, body):
        # After a failure, skip this server for a while instead of waiting
        # for a timeout on every request while the Mac is asleep.
        if time.monotonic() < self._down_until:
            raise BrainUnavailable("%s skipped after a recent failure" % self.url)
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            self._down_until = time.monotonic() + RETRY_REMOTE_AFTER_S
            raise BrainUnavailable("%s: %s" % (self.url, e))

    def ask(self, text, context=""):
        messages = [{"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": "%s\n\n%s" % (context, text) if context else text}]
        for _ in range(MAX_TOOL_ROUNDS):
            reply = self._post({"model": self.model, "messages": messages, "tools": TOOL_SCHEMAS,
                                "temperature": 0.2})
            message = reply["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                return (message.get("content") or "").strip()
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            for call in calls:
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": self.run_tool(call.get("function", {}))})
        return "I stopped after too many steps."

    def run_tool(self, function):
        fn = self._tools.get(function.get("name"))
        if fn is None:
            return "unknown tool %s" % function.get("name")
        try:
            args = json.loads(function.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
            return str(fn(**args))
        except (ValueError, TypeError) as e:
            return "bad arguments: %s" % e


# --- keyword fallback -------------------------------------------------------
# German and English command words. They are data for recognizing speech,
# so they stay in both languages.

NUMBERS = {"ein": 1, "eins": 1, "einen": 1, "einmal": 1, "one": 1, "once": 1,
           "zwei": 2, "zweimal": 2, "two": 2, "twice": 2, "drei": 3, "dreimal": 3, "three": 3,
           "vier": 4, "viermal": 4, "four": 4, "fünf": 5, "fünfmal": 5, "five": 5}
DONE_PREFIXES = ("Walked ", "Turned ", "Did ", "Stopped.")
PATTERNS = [
    # (regex, tool name, fixed kwargs)
    (r"\b(stopp?|halt|anhalten|stehen ?bleiben)\b", "stop", {}),
    (r"\b(vorwärts|vor|geradeaus|forward|ahead)\b", "walk", {"direction": "forward"}),
    (r"\b(rückwärts|zurück|backward|back)\b", "walk", {"direction": "backward"}),
    (r"\b(links|left)\b", "turn", {"direction": "left"}),
    (r"\b(rechts|right)\b", "turn", {"direction": "right"}),
    (r"\b(setz|sitz|hinsetzen|sit)\w*", "do_action", {"name": "sit"}),
    (r"\b(steh auf|aufstehen|stand)\b", "do_action", {"name": "stand"}),
    (r"\b(wink|wave)\w*", "do_action", {"name": "wave"}),
    (r"\b(tanz|dance)\w*", "do_action", {"name": "dance"}),
    (r"\b(liegestütz|push ?up)\w*", "do_action", {"name": "push up"}),
    (r"\b(was siehst du|siehst du|what do you see|look)\b", "look", {}),
    (r"\b(abstand|entfernung|distance)\b", "distance", {}),
]
SEARCH = re.compile(r"\b(?:such|find|search)\w*\s+(?:die|den|das|eine?n?|the|a)?\s*(\w+)", re.I)
GERMAN_LABELS = {"katze": "cat", "hund": "dog", "person": "person", "mensch": "person", "ball": "sports ball",
                 "stuhl": "chair", "flasche": "bottle", "tasse": "cup", "becher": "cup"}


class KeywordBrain:
    """Understands short commands without a model. Fast and predictable."""

    name = "keywords"

    def __init__(self, tools, language="de"):
        self._tools = {fn.__name__: fn for fn in tools}
        german = language == "de"
        self.not_understood = "Das habe ich nicht verstanden." if german else "I did not understand that."
        self.confirm = "Okay."

    def ask(self, text, context=""):
        lowered = text.lower()
        m = SEARCH.search(lowered)
        if m:
            word = m.group(1)
            return self._tools["search"](GERMAN_LABELS.get(word, word))
        for pattern, tool, kwargs in PATTERNS:
            if re.search(pattern, lowered):
                if tool in ("walk", "turn"):
                    kwargs = dict(kwargs, steps=self._steps(lowered))
                result = self._tools[tool](**kwargs)
                # Tool texts are English for the model; a plain movement only
                # needs a short confirmation the voice can say.
                return self.confirm if result.startswith(DONE_PREFIXES) else result
        return self.not_understood

    @staticmethod
    def _steps(text):
        m = re.search(r"\b(\d+)\b", text)
        if m:
            return int(m.group(1))
        for word, n in NUMBERS.items():
            if re.search(r"\b%s\b" % word, text):
                return n
        return 1


def timed(brain, text, context=""):
    started = time.monotonic()
    reply = brain.ask(text, context)
    return reply, time.monotonic() - started
