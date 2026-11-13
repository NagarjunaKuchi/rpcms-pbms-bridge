import importlib.util
import pathlib
import sys

_p = pathlib.Path(__file__).parent.parent / "rpcms_pbms_bridge" / "mapping.py"
_spec = importlib.util.spec_from_file_location("rpcms_mapping", _p)
mapping = importlib.util.module_from_spec(_spec)
sys.modules["rpcms_mapping"] = mapping
_spec.loader.exec_module(mapping)
