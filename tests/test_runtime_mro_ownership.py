"""Execution methods belong to the runtime source, not import-time wrappers."""
import inspect
from app.agent_runtime import AgentRuntime


def test_production_runtime_mro_methods_are_owned_by_runtime_source():
    foreign = []
    for cls in AgentRuntime.__mro__:
        if cls is object:
            continue
        for name, value in vars(cls).items():
            if isinstance(value, (staticmethod, classmethod)):
                value = value.__func__
            if inspect.isfunction(value) and not value.__module__.startswith("app.agent_runtime."):
                foreign.append(f"{cls.__name__}.{name}: {value.__module__}")
    assert foreign == []
