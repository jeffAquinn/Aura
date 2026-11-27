"""After 10 idle minutes Aura frees the Mac's memory: every model unloads, and with Sleep Mode on
only its model is loaded, to stay resident (server.IdleSleep)."""

from __future__ import annotations

from aura.model_routing import SleepMode
from aura.server import IdleSleep


class Mac:
    """Stands in for Ollama and llama-server: what is loaded, and what Aura asked of them."""

    def __init__(self, loaded: list[str], sleep: SleepMode, busy: bool = False):
        self.models, self.sleep, self.is_busy = list(loaded), sleep, busy
        self.llama_running = True
        self.calls: list[tuple[str, str]] = []

    def idle_sleep(self, start: float = 0.0) -> IdleSleep:
        sleeper = IdleSleep(
            600, busy=lambda: self.is_busy, sleep_mode=lambda: self.sleep, loaded=lambda: list(self.models),
            unload=self.unload, keep=self.keep, release=lambda m: self.calls.append(("release", m)),
            stop_llama=self.stop_llama)
        sleeper.last_active = start
        return sleeper

    def unload(self, model: str) -> None:
        self.calls.append(("unload", model))
        self.models.remove(model)

    def keep(self, model: str) -> None:
        self.calls.append(("keep", model))
        if model not in self.models:
            self.models.append(model)

    def stop_llama(self, reason: str) -> None:
        self.calls.append(("stop llama", reason))
        self.llama_running = False


def test_without_sleep_mode_every_model_unloads_after_ten_idle_minutes():
    mac = Mac(["gemma4:26b", "qwen3.5:9b"], SleepMode(False, "qwen3.5:9b"))
    sleeper = mac.idle_sleep()
    assert sleeper.check(now=599) is False and mac.calls == []  # not yet
    assert sleeper.check(now=601) is True
    assert mac.models == [] and not mac.llama_running
    assert sleeper.check(now=2000) is False  # once per idle period


def test_with_sleep_mode_only_its_model_stays_loaded():
    mac = Mac(["gemma4:26b"], SleepMode(True, "qwen3.5:9b"))
    sleeper = mac.idle_sleep()
    assert sleeper.check(now=601) is True
    assert mac.models == ["qwen3.5:9b"] and not mac.llama_running
    assert ("unload", "gemma4:26b") in mac.calls and mac.calls[-1] == ("keep", "qwen3.5:9b")


def test_the_sleep_model_already_loaded_is_kept_not_reloaded_from_scratch():
    mac = Mac(["qwen3.5:9b", "gemma4:26b"], SleepMode(True, "qwen3.5:9b"))
    mac.idle_sleep().check(now=601)
    assert ("unload", "qwen3.5:9b") not in mac.calls and mac.models == ["qwen3.5:9b"]


def test_a_running_turn_is_never_put_to_sleep_and_counts_as_activity():
    mac = Mac(["gemma4:26b"], SleepMode(False, "qwen3.5:9b"), busy=True)
    sleeper = mac.idle_sleep()
    assert sleeper.check(now=5000) is False and mac.models == ["gemma4:26b"]
    mac.is_busy = False  # the turn ended at 5000: ten more minutes from there
    assert sleeper.check(now=5500) is False
    assert sleeper.check(now=5601) is True and mac.models == []


def test_activity_wakes_the_sleep_model_back_to_the_ordinary_lifetime():
    mac = Mac([], SleepMode(True, "qwen3.5:9b"))
    sleeper = mac.idle_sleep()
    sleeper.check(now=601)
    sleeper.active(now=700)  # a message
    assert mac.calls[-1] == ("release", "qwen3.5:9b") and not sleeper.asleep
    sleeper.active(now=710)
    assert mac.calls.count(("release", "qwen3.5:9b")) == 1
    assert sleeper.check(now=1311) is True  # idle again: asleep again
