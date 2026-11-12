"""Robot tools for the language model.

LiteRT-LM builds the tool schema from each function's docstring and type
hints, so they are plain functions with Google-style docstrings. The same
functions back the OpenAI-compatible backend via TOOL_SCHEMAS. Loops such
as "turn until you see X" live here, not in the model: the small model only
decides what to do, the code does the repetitive part.
"""

MAX_STEPS = 5
ACTIONS = ("stand", "sit", "wave", "push up", "dance", "look left", "look right", "look up", "look down")
SEARCH_TURNS = 8


def build_tools(world, speed=60, log=None):
    """Return the tool functions bound to a World."""

    def note(text):
        if log:
            log(text)
        return text

    def clamp(steps):
        try:
            return max(1, min(MAX_STEPS, int(steps)))
        except (TypeError, ValueError):
            return 1

    def result(outcome, detail, done_text):
        if outcome == "done":
            return note(done_text)
        if outcome == "stopped":
            return note("Stopped (%s)." % (detail or "stop"))
        if outcome == "rejected":
            return note("The body refused: %s." % (detail or "unknown reason"))
        return note("Failed: %s." % (detail or outcome))

    def walk(direction: str, steps: int) -> str:
        """Walk forward or backward.

        Args:
            direction: "forward" or "backward".
            steps: Number of steps, 1 to 5.
        """
        if direction not in ("forward", "backward"):
            return note("direction must be forward or backward")
        if direction == "forward":
            cm = world.fresh_distance()
            if cm is not None and cm < 20:
                return note("Not walking forward: obstacle %.0f cm ahead." % cm)
        n = clamp(steps)
        outcome, detail = world.act(direction, n, speed)
        return result(outcome, detail, "Walked %s %d step(s)." % (direction, n))

    def turn(direction: str, steps: int) -> str:
        """Turn on the spot.

        Args:
            direction: "left" or "right".
            steps: Number of turning steps, 1 to 5. One step is roughly 20 to 30 degrees.
        """
        if direction not in ("left", "right"):
            return note("direction must be left or right")
        n = clamp(steps)
        outcome, detail = world.act("turn " + direction, n, speed)
        return result(outcome, detail, "Turned %s %d step(s)." % (direction, n))

    def do_action(name: str) -> str:
        """Perform a body action.

        Args:
            name: One of stand, sit, wave, push up, dance, look left, look right, look up, look down.
        """
        if name not in ACTIONS:
            return note("unknown action, use one of: " + ", ".join(ACTIONS))
        outcome, detail = world.act(name, 1, speed)
        return result(outcome, detail, "Did %s." % name)

    def stop() -> str:
        """Stop all motion immediately."""
        world.stop("agent")
        return note("Stopped.")

    def look() -> str:
        """Describe what the camera currently sees, with position and rough distance."""
        return note(world.describe())

    def distance() -> str:
        """Distance to the nearest obstacle straight ahead, from the ultrasonic sensor."""
        cm = world.fresh_distance()
        return note("No distance reading." if cm is None else "Obstacle %.0f cm ahead." % cm)

    def search(label: str) -> str:
        """Turn step by step until an object is in view, then stop.

        Args:
            label: Object class to look for, e.g. person, cat, dog, ball, chair, bottle, cup.
        """
        label = (label or "").strip().lower()
        if label in ("ball", "football"):
            label = "sports ball"
        for turn_no in range(SEARCH_TURNS + 1):
            found = world.find(label)
            if found is not None:
                return note("Found %s after %d turn(s). %s" % (label, turn_no, world.describe()))
            if turn_no == SEARCH_TURNS:
                break
            outcome, detail = world.act("turn left", 1, speed)
            if outcome != "done":
                return result(outcome, detail, "")
        return note("Did not find %s after a full turn." % label)

    return [walk, turn, do_action, stop, look, distance, search]


def _string(description, enum=None):
    schema = {"type": "string", "description": description}
    if enum:
        schema["enum"] = list(enum)
    return schema


def _steps():
    return {"type": "integer", "minimum": 1, "maximum": MAX_STEPS, "description": "Number of steps, 1 to 5."}


def _tool(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []}}}


TOOL_SCHEMAS = [
    _tool("walk", "Walk forward or backward.",
          {"direction": _string("Direction.", ("forward", "backward")), "steps": _steps()}, ["direction", "steps"]),
    _tool("turn", "Turn on the spot. One step is roughly 20 to 30 degrees.",
          {"direction": _string("Direction.", ("left", "right")), "steps": _steps()}, ["direction", "steps"]),
    _tool("do_action", "Perform a body action.", {"name": _string("Action.", ACTIONS)}, ["name"]),
    _tool("stop", "Stop all motion immediately."),
    _tool("look", "Describe what the camera currently sees, with position and rough distance."),
    _tool("distance", "Distance to the nearest obstacle straight ahead, from the ultrasonic sensor."),
    _tool("search", "Turn step by step until an object is in view, then stop.",
          {"label": _string("Object class, e.g. person, cat, dog, ball, chair, bottle, cup.")}, ["label"]),
]
