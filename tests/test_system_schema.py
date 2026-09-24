"""The published system schema agrees with the Python factory interface."""
import json
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from physics_demo import build_system
from physics_demo.api import call_tool


@unittest.skipIf(Draft202012Validator is None,"Optional developer JSON Schema package is unavailable")
class SystemSchemaTests(unittest.TestCase):
    def test_machine_contract_matches_common_input_boundaries(self):
        schema=json.loads((Path(__file__).resolve().parents[1]/'agent/system-v1.schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        validator=Draft202012Validator(schema)
        valid=[{'type':'pendulum'},{'type':'double_pendulum'},
               {'type':'double_pendulum','lengths':[.5,2],'masses':[2,3],'angles':[.4,-.8],'angular_velocities':[1,-2],'output_fps':60}]
        invalid=[{'type':'triple_pendulum'}, {'type':'pendulum','masses':[1,2]},
                 {'type':'double_pendulum','lengths':[1,0]}, {'type':'pendulum','angles':[True]},
                 {'type':'pendulum','output_fps':2.5}, {'type':'pendulum','duration':31},
                 {'type':'pendulum','solver':'guess'}, {'type':'pendulum','gravity':0}]
        for spec in valid:
            self.assertTrue(validator.is_valid(spec),spec)
            self.assertTrue(call_tool('physics_system',{'spec_json':json.dumps(spec)})['ok'])
        for spec in invalid:
            self.assertFalse(validator.is_valid(spec),spec)
            with self.assertRaises(ValueError):build_system(spec)
