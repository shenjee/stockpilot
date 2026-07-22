from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


APP_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = APP_ROOT / "contracts"


def load_json(name: str):
    with (CONTRACTS / name).open(encoding="utf-8") as stream:
        return json.load(stream)


class ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.logical = load_json("logical-schema.json")
        cls.replay = load_json("replay-v1.schema.json")
        cls.fixture = load_json("fixtures/replay-speed-v1.json")
        cls.registry = Registry().with_resources(
            [
                (cls.logical["$id"], Resource.from_contents(cls.logical)),
                (cls.replay["$id"], Resource.from_contents(cls.replay)),
            ]
        )

    def validator(self, definition: str) -> Draft202012Validator:
        schema = {"$ref": f"{self.replay['$id']}#/$defs/{definition}"}
        return Draft202012Validator(schema, registry=self.registry)

    def test_logical_schema_is_not_storage_schema(self) -> None:
        self.assertIn("not a SQLite schema", self.logical["description"])
        expected = {"security", "bar", "quote", "indicators", "chan_analysis", "session", "warning", "workbench_snapshot"}
        self.assertTrue(expected.issubset(self.logical["$defs"]))

    def test_all_four_speed_commands_validate(self) -> None:
        validator = self.validator("set_replay_speed_request")
        self.assertEqual(self.fixture["valid_speeds"], [1, 2, 5, 10])
        for request in self.fixture["set_speed_requests"]:
            validator.validate(request)

    def test_invalid_speed_is_rejected(self) -> None:
        invalid = dict(self.fixture["set_speed_requests"][0], playback_speed=3)
        errors = list(self.validator("set_replay_speed_request").iter_errors(invalid))
        self.assertTrue(errors)

    def test_speed_event_and_snapshot_validate(self) -> None:
        self.validator("event_envelope").validate(self.fixture["changed_event"])
        self.validator("workbench_snapshot").validate(self.fixture["snapshot"])
        self.assertNotIn("operation_id", self.fixture["changed_event"])
        self.assertEqual(self.fixture["changed_event"]["revision"], self.fixture["snapshot"]["session"]["revision"])


if __name__ == "__main__":
    unittest.main()
