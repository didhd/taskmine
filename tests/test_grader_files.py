"""The grader files every testcase under its run file, also for runners whose JUnit names no file.

The reports below have the shapes these runners write: surefire (Maven, Gradle), gotestsum,
cargo-nextest, JunitXml.TestLogger (dotnet test), vitest, and a report with a file attribute.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("grader", ROOT / "src" / "taskmine" / "templates" / "grader.py")
grader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grader)


def statuses(xml: str, files: dict[str, str]) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as d:
        app = Path(d)
        for rel, text in files.items():
            (app / rel).parent.mkdir(parents=True, exist_ok=True)
            (app / rel).write_text(text)
        (app / "junit.xml").write_text(xml)
        return grader.parse_junit(str(app / "junit.xml"), str(app), list(files))


class RunnersWithoutAFileAttribute(unittest.TestCase):
    def test_surefire_class_names_map_to_java_paths(self):
        files = {"src/test/java/com/acme/shop/DiscountTest.java": "class DiscountTest {}",
                 "src/test/java/com/acme/shop/TaxTest.java": "class TaxTest {}"}
        xml = ('<testsuites><testsuite name="com.acme.shop.DiscountTest">'
               '<testcase name="ofSave10()" classname="com.acme.shop.DiscountTest"/></testsuite>'
               '<testsuite name="com.acme.shop.TaxTest"><testcase name="roundsEachLine()" classname="com.acme.shop.TaxTest">'
               '<failure message="x"/></testcase></testsuite></testsuites>')
        self.assertEqual(statuses(xml, files), {
            "src/test/java/com/acme/shop/DiscountTest.java::com.acme.shop.DiscountTest::ofSave10()": "passed",
            "src/test/java/com/acme/shop/TaxTest.java::com.acme.shop.TaxTest::roundsEachLine()": "failed",
        })

    def test_gotestsum_packages_map_by_the_test_function(self):
        files = {"cart/discount_test.go": "package cart\nfunc TestDiscountSave10(t *testing.T) {}\n",
                 "cart/tax_test.go": "package cart\nfunc TestTaxPerLine(t *testing.T) {}\n"}
        xml = ('<testsuites><testsuite name="example.com/shop/cart">'
               '<testcase classname="example.com/shop/cart" name="TestDiscountSave10"/>'
               '<testcase classname="example.com/shop/cart" name="TestTaxPerLine/two_lines"/>'
               '</testsuite></testsuites>')
        self.assertEqual(set(statuses(xml, files)), {
            "cart/discount_test.go::example.com/shop/cart::TestDiscountSave10",
            "cart/tax_test.go::example.com/shop/cart::TestTaxPerLine/two_lines",
        })

    def test_nextest_binaries_map_to_integration_test_files(self):
        files = {"tests/discount.rs": "#[test]\nfn save10() {}\n", "tests/tax.rs": "#[test]\nfn per_line() {}\n"}
        xml = ('<testsuites><testsuite name="shop::discount"><testcase name="save10" classname="shop::discount"/></testsuite>'
               '<testsuite name="shop::tax"><testcase name="per_line" classname="shop::tax"/></testsuite></testsuites>')
        self.assertEqual(set(statuses(xml, files)), {"tests/discount.rs::shop::discount::save10", "tests/tax.rs::shop::tax::per_line"})

    def test_dotnet_classes_map_to_their_cs_files(self):
        files = {"Shop.Tests/DiscountTests.cs": "public class DiscountTests {}", "Shop.Tests/TaxTests.cs": "public class TaxTests {}"}
        xml = ('<testsuites><testsuite name="Shop.Tests.dll">'
               '<testcase classname="Shop.Tests.DiscountTests" name="Shop.Tests.DiscountTests.OfSave10"/>'
               '<testcase classname="Shop.Tests.TaxTests" name="Shop.Tests.TaxTests.PerLine(x: 1)"/></testsuite></testsuites>')
        got = statuses(xml, files)
        self.assertEqual(sorted(k.split("::")[0] for k in got), ["Shop.Tests/DiscountTests.cs", "Shop.Tests/TaxTests.cs"])

    def test_vitest_puts_the_path_in_the_class_and_suite(self):
        files = {"test/discount.test.ts": "", "test/tax.test.ts": ""}
        xml = ('<testsuites><testsuite name="test/discount.test.ts">'
               '<testcase classname="test/discount.test.ts" name="discount(1000, SAVE10) is 100 cents"/></testsuite>'
               '<testsuite name="test/tax.test.ts"><testcase classname="test/tax.test.ts" name="tax rounds each line"/>'
               '</testsuite></testsuites>')
        self.assertEqual(sorted(k.split("::")[0] for k in statuses(xml, files)), ["test/discount.test.ts", "test/tax.test.ts"])


class StillExact(unittest.TestCase):
    def test_a_file_attribute_wins(self):
        files = {"tests/test_a.py": "def test_x(): pass\n", "tests/test_b.py": "def test_x(): pass\n"}
        xml = '<testsuites><testsuite name="pytest"><testcase classname="tests.test_b" name="test_x" file="tests/test_b.py"/></testsuite></testsuites>'
        self.assertEqual(list(statuses(xml, files)), ["tests/test_b.py::pytest::test_x"])

    def test_an_ambiguous_test_keeps_its_class(self):
        files = {"a/x_test.go": "func TestSame(t *testing.T) {}", "b/y_test.go": "func TestSame(t *testing.T) {}"}
        xml = '<testsuites><testsuite name="m"><testcase classname="example.com/m" name="TestSame"/></testsuite></testsuites>'
        self.assertEqual(list(statuses(xml, files)), ["example.com/m::m::TestSame"])


if __name__ == "__main__":
    unittest.main()
