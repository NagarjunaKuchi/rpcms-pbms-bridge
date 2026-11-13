import importlib.util
import pathlib
import sys

_p = pathlib.Path(__file__).parent.parent / "rpcms_pbms_bridge" / "mapping.py"
_spec = importlib.util.spec_from_file_location("rpcms_mapping", _p)
mapping = importlib.util.module_from_spec(_spec)
sys.modules["rpcms_mapping"] = mapping
_spec.loader.exec_module(mapping)

_p2 = pathlib.Path(__file__).parent.parent / "rpcms_pbms_bridge" / "bridge_api.py"
_spec2 = importlib.util.spec_from_file_location("rpcms_bridge_api", _p2)
bridge_api = importlib.util.module_from_spec(_spec2)
sys.modules["rpcms_bridge_api"] = bridge_api
_spec2.loader.exec_module(bridge_api)
