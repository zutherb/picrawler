"""Run motion commands on a worker thread, interruptible between frames.

Exactly one thread (the worker) talks to the Picrawler. Everybody else
submits commands. See docs/target-architecture.md, section 3.2.
"""
import collections
import dataclasses
import enum
import threading
import time
import traceback


class Priority(enum.IntEnum):
    GOAL = 0      # remote control, missions, LLM
    REFLEX = 1    # sensor reactions, e.g. evading
    SAFETY = 2    # stop, watchdog, emergency stop


@dataclasses.dataclass(frozen=True)
class Command:
    kind: str                     # "action", "frame", "sit"
    name: str = None
    steps: int = 1
    speed: int = 50
    frame: list = None
    priority: Priority = Priority.GOAL
    id: str = None


@dataclasses.dataclass(frozen=True)
class ExecutorState:
    status: str                   # "idle", "running", "stopping", "error", "offline"
    command_id: str = None
    name: str = None
    priority: Priority = None
    queue_len: int = 0
    error: str = None
    legs: list = None


COMMAND_KINDS = ("action", "frame", "sit")


class MotionExecutor:

    def __init__(self, crawler, on_state=None, queue_max=8, lockout_s=1.0, clock=time.monotonic):
        self._crawler = crawler
        self._on_state = on_state
        self._queue_max = queue_max
        self._lockout_s = lockout_s
        self._clock = clock

        self._lock = threading.Lock()
        self._wakeup = threading.Condition(self._lock)
        self._queue = collections.deque()
        self._current = None
        # Bumped on every stop/preemption; a command aborts once the epoch
        # it started in is gone.
        self._epoch = 0
        # Priority -> clock time of its last activity, for the lockout.
        self._last_active = {}
        self._closing = False
        self._thread = None
        self._state = ExecutorState("idle")

    # --- public API ---

    def start(self):
        if self._thread is not None:
            raise RuntimeError("executor already started")
        self._thread = threading.Thread(target=self._run, name="motion-executor", daemon=True)
        self._thread.start()

    def submit(self, cmd):
        """Queue a command. Returns False if it was rejected."""
        if cmd.kind not in COMMAND_KINDS:
            raise ValueError("unknown command kind: %r" % (cmd.kind,))
        with self._lock:
            if self._closing or self._locked_out(cmd.priority):
                return False
            current = self._current.priority if self._current else None
            if current is not None and cmd.priority > current:
                self._epoch += 1
            self._queue = collections.deque(c for c in self._queue if c.priority >= cmd.priority)
            if len(self._queue) >= self._queue_max:
                return False
            self._queue.append(cmd)
            self._last_active[cmd.priority] = self._clock()
            self._wakeup.notify()
        self._emit()
        return True

    def stop(self, priority=Priority.SAFETY, sit=False, speed=40):
        """Abort the running command and drop everything queued."""
        with self._lock:
            self._epoch += 1
            self._queue.clear()
            self._last_active[priority] = self._clock()
            if sit and not self._closing:
                self._queue.append(Command("sit", speed=speed, priority=priority))
            self._wakeup.notify()
        self._emit()

    def shutdown(self, sit=True, timeout=10.0):
        """Stop, optionally sit down, and end the worker thread."""
        self.stop(sit=sit)
        with self._lock:
            self._closing = True
            self._wakeup.notify()
        if self._thread is not None:
            self._thread.join(timeout)
        with self._lock:
            self._state = dataclasses.replace(self._state, status="offline", queue_len=0)
        self._notify(self._state)

    @property
    def state(self):
        with self._lock:
            return self._state

    def wait_idle(self, timeout=None):
        """Block until the queue is empty and nothing runs (for tests/tools)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                if not self._queue and self._current is None:
                    return True
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.005)

    # --- worker ---

    def _locked_out(self, priority):
        now = self._clock()
        if self._current is not None and self._current.priority > priority:
            return True
        if any(c.priority > priority for c in self._queue):
            return True
        return any(p > priority and now - t < self._lockout_s for p, t in self._last_active.items())

    def _run(self):
        while True:
            with self._lock:
                while not self._queue and not self._closing:
                    self._wakeup.wait()
                if not self._queue:
                    return
                cmd = self._queue.popleft()
                self._current = cmd
                epoch = self._epoch
            self._emit()

            error = None
            try:
                self._execute(cmd, epoch)
            except Exception as e:
                traceback.print_exc()
                error = "%s: %s" % (type(e).__name__, e)

            with self._lock:
                self._current = None
                self._last_active[cmd.priority] = self._clock()
                if error is not None:
                    self._epoch += 1
                    self._queue.clear()
            legs = self._legs()
            if error is not None:
                self._emit(status="error", error=error, cmd=cmd, legs=legs)
            # idle, but naming the command that just finished, so a caller
            # can tell its own command is done
            self._emit(cmd=cmd, legs=legs)

    def _execute(self, cmd, epoch):
        if cmd.kind == "sit":
            frames = self._crawler.step_list["sit"]
        elif cmd.kind == "frame":
            frames = [cmd.frame]
        else:
            frames = self._crawler.iter_action(cmd.name, cmd.steps)
        for frame in frames:
            if self._epoch != epoch:
                self._emit(status="stopping", cmd=cmd)
                return
            self._crawler.do_step(frame, speed=cmd.speed)

    def _legs(self):
        try:
            return self._crawler.current_step_all_leg_value()
        except Exception:
            return None

    # --- state reporting ---

    def _emit(self, status=None, error=None, cmd=None, legs=None):
        with self._lock:
            cmd = cmd or self._current
            if status is None:
                status = "running" if self._current else "idle"
            self._state = ExecutorState(
                status=status,
                command_id=cmd.id if cmd else None,
                name=(cmd.name or cmd.kind) if cmd else None,
                priority=cmd.priority if cmd else None,
                queue_len=len(self._queue),
                error=error,
                legs=legs if legs is not None else self._state.legs,
            )
            state = self._state
        self._notify(state)

    def _notify(self, state):
        if self._on_state is None:
            return
        try:
            self._on_state(state)
        except Exception:
            traceback.print_exc()
