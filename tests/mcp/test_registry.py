"""Registry + input-schema tests for the MCP server (B-130).

Pins the subcommand→tool mapping for a representative sample and the
JSON-Schema generation. SDK-free and offline — no ``mcp`` import, no DB.
"""

from __future__ import annotations

import unittest

from genkei.mcp.registry import TOOL_SPECS, ToolParam, ToolSpec, tool_by_name
from genkei.mcp.server import build_input_schema


class RegistryShapeTests(unittest.TestCase):
    def test_tool_names_are_unique(self) -> None:
        names = [spec.name for spec in TOOL_SPECS]
        self.assertEqual(len(names), len(set(names)), "duplicate tool names in registry")

    def test_required_b130_tools_are_present(self) -> None:
        """The B-130 acceptance-criteria minimum surface is exposed."""
        names = {spec.name for spec in TOOL_SPECS}
        for required in (
            "prices",
            "signals",
            "tvl",
            "zcash_usage",
            "query",
            "watchlist_list",
            "watchlist_health",
            "watchlist_gaps",
            "watchlist_score",
        ):
            self.assertIn(required, names, f"missing required tool {required!r}")

    def test_subcommand_mapping_for_sample(self) -> None:
        """Pin the CLI subcommand path each representative tool maps to."""
        cases = {
            "prices": ("prices",),
            "signals": ("signals",),
            "tvl": ("tvl",),
            "zcash_usage": ("zcash-usage",),
            "query": ("query",),
            "watchlist_health": ("watchlist", "health"),
            "watchlist_score": ("watchlist", "score"),
        }
        for name, subcommand in cases.items():
            self.assertEqual(tool_by_name(name).subcommand, subcommand)

    def test_every_spec_emits_json(self) -> None:
        """Every exposed subcommand supports --json (the pass-through contract)."""
        for spec in TOOL_SPECS:
            self.assertTrue(spec.emits_json, f"{spec.name} should pass through --json")

    def test_tool_by_name_raises_on_unknown(self) -> None:
        with self.assertRaises(KeyError):
            tool_by_name("does-not-exist")

    def test_query_sql_is_a_required_positional(self) -> None:
        spec = tool_by_name("query")
        sql_param = next(p for p in spec.params if p.name == "sql")
        self.assertIsNone(sql_param.flag, "SQL must be positional")
        self.assertTrue(sql_param.required)


class InputSchemaTests(unittest.TestCase):
    def test_schema_marks_required_params(self) -> None:
        schema = build_input_schema(tool_by_name("prices"))
        self.assertEqual(schema["type"], "object")
        self.assertIn("ticker", schema["properties"])
        self.assertIn("ticker", schema["required"])
        self.assertFalse(schema["additionalProperties"])

    def test_schema_maps_param_types(self) -> None:
        spec = ToolSpec(
            name="_probe",
            subcommand=("probe",),
            description="probe",
            params=(
                ToolParam("s", "--s", "string"),
                ToolParam("i", "--i", "integer"),
                ToolParam("n", "--n", "number"),
                ToolParam("b", "--b", "boolean"),
            ),
        )
        props = build_input_schema(spec)["properties"]
        self.assertEqual(props["s"]["type"], "string")
        self.assertEqual(props["i"]["type"], "integer")
        self.assertEqual(props["n"]["type"], "number")
        self.assertEqual(props["b"]["type"], "boolean")

    def test_schema_omits_required_key_when_no_required_params(self) -> None:
        spec = ToolSpec(
            name="_probe",
            subcommand=("probe",),
            description="probe",
            params=(ToolParam("opt", "--opt", "string"),),
        )
        self.assertNotIn("required", build_input_schema(spec))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
