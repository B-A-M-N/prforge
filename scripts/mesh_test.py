import sys
from scripts.mesh.coordinator import _tick
from scripts.mesh.auditor import _poll
print("Coordinator tick exists:", callable(_tick))
print("Auditor poll exists:", callable(_poll))
