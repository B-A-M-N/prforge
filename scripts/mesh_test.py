import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "redis" not in sys.modules:
    fake_redis_module = types.ModuleType("redis")

    class _RedisType:
        pass

    fake_redis_module.Redis = _RedisType
    sys.modules["redis"] = fake_redis_module

from scripts.mesh.coordinator import _tick
from scripts.mesh.auditor import _poll
print("Coordinator tick exists:", callable(_tick))
print("Auditor poll exists:", callable(_poll))
