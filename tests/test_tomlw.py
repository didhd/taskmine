"""The TOML writer: what it writes reads back unchanged with tomllib, types included."""

from __future__ import annotations

import tomllib
import unittest

from taskmine import tomlw


class Values(unittest.TestCase):
    def round_trip(self, doc: dict) -> dict:
        back = tomllib.loads(tomlw.dumps(doc))
        self.assertEqual(back, doc)
        return back

    def test_floats_keep_a_decimal_point(self):
        back = self.round_trip({"t": {"a": 900.0, "b": 1e20, "c": -0.5, "d": float("inf")}})
        for v in back["t"].values():
            self.assertIsInstance(v, float)
        self.assertIn("a = 900.0", tomlw.dumps({"t": {"a": 900.0}}))

    def test_ints_bools_strings_and_lists(self):
        self.round_trip({"t": {"n": -3, "yes": True, "no": False, "s": 'quote " and \\ back', "l": [1, "a", 2.5]}})

    def test_control_characters_and_unicode(self):
        self.round_trip({"t": {"s": "nul \x00 esc \x1b tab \t newline \n", "u": "é 한 \U0001f600"}})

    def test_delete_character_is_escaped(self):
        # TOML forbids a raw U+007F in a basic string; JSON does not escape it.
        self.round_trip({"t": {"s": "a\x7fb"}})

    def test_unsupported_types_are_refused(self):
        with self.assertRaises(TypeError):
            tomlw.dumps({"t": {"x": None}})
        with self.assertRaises(TypeError):
            tomlw.dumps({"t": {"x": [{"a": 1}]}})


class Tables(unittest.TestCase):
    def test_nested_tables_in_insertion_order(self):
        doc = {"version": "1.0", "metadata": {"author": "x", "taskmine": {"n": 1}}, "verifier": {"timeout_sec": 1.0}}
        text = tomlw.dumps(doc)
        self.assertEqual(text, 'version = "1.0"\n\n[metadata]\nauthor = "x"\n\n[metadata.taskmine]\nn = 1\n\n'
                               '[verifier]\ntimeout_sec = 1.0\n')
        self.assertEqual(tomllib.loads(text), doc)

    def test_a_table_of_tables_gets_no_empty_header(self):
        self.assertEqual(tomlw.dumps({"a": {"b": {"c": 1}}}), "[a.b]\nc = 1\n")
        self.assertEqual(tomllib.loads(tomlw.dumps({"a": {}})), {"a": {}})

    def test_keys_that_are_not_bare_are_quoted(self):
        # Unquoted, `a.b` was written as a dotted key and `a b` made the file unreadable.
        doc = {"t": {"a.b": 1, "a b": 2, "é": 3, "": 4}, "x.y": {"z": 5}}
        self.assertEqual(tomllib.loads(tomlw.dumps(doc)), doc)
        self.assertIn('"a.b" = 1', tomlw.dumps(doc))
        self.assertIn("plain_key-1 = 1", tomlw.dumps({"t": {"plain_key-1": 1}}))


if __name__ == "__main__":
    unittest.main()
