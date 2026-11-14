import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
for name, rel in (("rpcms_bridge_api", "rpcms_pbms_bridge/bridge_api.py"), ("mock_bridge", "dev/mock_bridge/mock_bridge.py")):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
