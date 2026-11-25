"""Make a small synthetic repository in one of eight languages, to check taskmine end to end in it.

    python3 demo/langs/make_repo.py LANG DIR       LANG: python typescript go java rust csharp ruby php

Every repository has the same three merged pull requests, written in that language's usual layout
and test runner. Its recipe is demo/langs/LANG.toml.
- #1 adds discount codes: a new source file and a new test file.
- #2 rounds tax per line: a changed source file and a changed test file, so it has a pass-to-pass test.
- #3 changes only the README, which taskmine leaves out.

Author and dates are fixed, so a repository is the same on every machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ENV = dict(os.environ, GIT_AUTHOR_NAME="Demo", GIT_AUTHOR_EMAIL="demo@example.com", GIT_COMMITTER_NAME="Demo",
           GIT_COMMITTER_EMAIL="demo@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
TITLES = ("Add discount codes (SAVE10, HALF)", "Round tax per line, as receipts print it", "Show usage in the README")


def _python():
    cart = ('"""Sums carts and taxes them, in cents."""\n\n\n'
            'def total(prices):\n    """The sum of prices in cents."""\n    return sum(prices)\n\n\n'
            'def tax(lines, rate_percent):\n    """Tax in cents on the whole order at rate_percent, rounded half up."""\n'
            '    return (total(lines) * rate_percent + 50) // 100\n')
    base = {
        "shop/__init__.py": "",
        "shop/cart.py": cart,
        "tests/test_cart.py": "from shop.cart import total\n\n\ndef test_total_sums_prices_in_cents():\n    assert total([150, 250]) == 400\n",
        "tests/test_tax.py": "from shop.cart import tax\n\n\ndef test_tax_on_1000_cents_at_8_percent_is_80():\n    assert tax([1000], 8) == 80\n",
    }
    pr1 = {
        "shop/discount.py": ('"""Discount codes: SAVE10 is 10%, HALF is 50%; codes ignore case and surrounding spaces."""\n\n'
                             'CODES = {"SAVE10": 10, "HALF": 50}\n\n\n'
                             'def discount(subtotal, code):\n    key = code.strip().upper()\n    if key not in CODES:\n'
                             '        raise ValueError(f"unknown code: {code.strip()}")\n    return subtotal * CODES[key] // 100\n'),
        "tests/test_discount.py": ("import pytest\n\nfrom shop.discount import discount\n\n\n"
                                   "def test_discount_of_1000_cents_with_SAVE10_is_100():\n    assert discount(1000, \"SAVE10\") == 100\n\n\n"
                                   "def test_codes_ignore_case_and_spaces_so_half_is_HALF_and_50_percent_off():\n"
                                   "    assert discount(1000, \"  half \") == 500\n\n\n"
                                   "def test_an_unknown_code_raises_ValueError_unknown_code_FREE():\n"
                                   "    with pytest.raises(ValueError, match=\"unknown code: FREE\"):\n        discount(1000, \"FREE\")\n"),
    }
    pr2 = {
        "shop/cart.py": cart.replace("on the whole order at rate_percent, rounded half up", "at rate_percent, each line rounded half up").replace(
            "    return (total(lines) * rate_percent + 50) // 100\n", "    return sum((line * rate_percent + 50) // 100 for line in lines)\n"),
        "tests/test_tax.py": base["tests/test_tax.py"] + ("\n\ndef test_tax_rounds_each_line_to_the_cent_before_adding_6_and_6_cents_at_8_percent_is_0():\n"
                                                          "    assert tax([6, 6], 8) == 0\n"),
    }
    return base, pr1, pr2


def _typescript():
    cart = ("/** Sums carts and taxes them, in cents. */\n\n"
            "export function total(prices: number[]): number {\n  return prices.reduce((sum, p) => sum + p, 0);\n}\n\n"
            "/** Tax in cents on the whole order at ratePercent, rounded half up. */\n"
            "export function tax(lines: number[], ratePercent: number): number {\n  return Math.floor((total(lines) * ratePercent + 50) / 100);\n}\n")
    base = {
        "package.json": ('{\n  "name": "shop",\n  "version": "1.0.0",\n  "type": "module",\n  "private": true,\n'
                         '  "scripts": { "test": "vitest run" },\n  "devDependencies": { "typescript": "5.7.2", "vitest": "2.1.8" }\n}\n'),
        "tsconfig.json": '{\n  "compilerOptions": { "target": "ES2022", "module": "ESNext", "moduleResolution": "Bundler", "strict": true }\n}\n',
        "src/cart.ts": cart,
        "test/cart.test.ts": ("import { expect, test } from 'vitest';\nimport { total } from '../src/cart';\n\n"
                              "test('total sums prices in cents', () => {\n  expect(total([150, 250])).toBe(400);\n});\n"),
        "test/tax.test.ts": ("import { expect, test } from 'vitest';\nimport { tax } from '../src/cart';\n\n"
                             "test('tax on 1000 cents at 8% is 80', () => {\n  expect(tax([1000], 8)).toBe(80);\n});\n"),
    }
    pr1 = {
        "src/discount.ts": ("/** Discount codes: SAVE10 is 10%, HALF is 50%; codes ignore case and surrounding spaces. */\n"
                            "const CODES: Record<string, number> = { SAVE10: 10, HALF: 50 };\n\n"
                            "export function discount(subtotal: number, code: string): number {\n"
                            "  const rate = CODES[code.trim().toUpperCase()];\n"
                            "  if (rate === undefined) throw new Error(`unknown code: ${code.trim()}`);\n"
                            "  return Math.floor((subtotal * rate) / 100);\n}\n"),
        "test/discount.test.ts": ("import { expect, test } from 'vitest';\nimport { discount } from '../src/discount';\n\n"
                                  "test('discount(1000, SAVE10) is 100 cents', () => {\n  expect(discount(1000, 'SAVE10')).toBe(100);\n});\n\n"
                                  "test('codes ignore case and spaces, so half is HALF and 50% off', () => {\n"
                                  "  expect(discount(1000, '  half ')).toBe(500);\n});\n\n"
                                  "test('an unknown code throws Error unknown code: FREE', () => {\n"
                                  "  expect(() => discount(1000, 'FREE')).toThrow('unknown code: FREE');\n});\n"),
    }
    pr2 = {
        "src/cart.ts": cart.replace("on the whole order at ratePercent, rounded half up", "at ratePercent, each line rounded half up").replace(
            "  return Math.floor((total(lines) * ratePercent + 50) / 100);\n",
            "  return lines.reduce((sum, line) => sum + Math.floor((line * ratePercent + 50) / 100), 0);\n"),
        "test/tax.test.ts": base["test/tax.test.ts"] + ("\ntest('tax rounds each line to the cent before adding: 6 and 6 cents at 8% is 0', () => {\n"
                                                        "  expect(tax([6, 6], 8)).toBe(0);\n});\n"),
    }
    return base, pr1, pr2


def _go():
    cart = ("// Package cart sums carts and taxes them, in cents.\npackage cart\n\n"
            "// Total is the sum of prices in cents.\nfunc Total(prices []int) int {\n\tsum := 0\n\tfor _, p := range prices {\n\t\tsum += p\n\t}\n\treturn sum\n}\n\n"
            "// Tax is the tax in cents on the whole order at ratePercent, rounded half up.\n"
            "func Tax(lines []int, ratePercent int) int {\n\treturn (Total(lines)*ratePercent + 50) / 100\n}\n")
    base = {
        "go.mod": "module example.com/shop\n\ngo 1.24\n",
        "cart/cart.go": cart,
        "cart/cart_test.go": ("package cart\n\nimport \"testing\"\n\n"
                              "func TestTotalSumsPricesInCents(t *testing.T) {\n\tif got := Total([]int{150, 250}); got != 400 {\n"
                              "\t\tt.Fatalf(\"Total = %d, want 400\", got)\n\t}\n}\n"),
        "cart/tax_test.go": ("package cart\n\nimport \"testing\"\n\n"
                             "func TestTaxOn1000CentsAt8PercentIs80(t *testing.T) {\n\tif got := Tax([]int{1000}, 8); got != 80 {\n"
                             "\t\tt.Fatalf(\"Tax = %d, want 80\", got)\n\t}\n}\n"),
    }
    pr1 = {
        "cart/discount.go": ("package cart\n\nimport (\n\t\"fmt\"\n\t\"strings\"\n)\n\n"
                             "var codes = map[string]int{\"SAVE10\": 10, \"HALF\": 50}\n\n"
                             "// Discount is the discount in cents for a code: SAVE10 is 10%, HALF is 50%. Codes ignore case and spaces.\n"
                             "func Discount(subtotal int, code string) (int, error) {\n\trate, ok := codes[strings.ToUpper(strings.TrimSpace(code))]\n"
                             "\tif !ok {\n\t\treturn 0, fmt.Errorf(\"unknown code: %s\", strings.TrimSpace(code))\n\t}\n\treturn subtotal * rate / 100, nil\n}\n"),
        "cart/discount_test.go": ("package cart\n\nimport \"testing\"\n\n"
                                  "func TestDiscountOf1000CentsWithSAVE10Is100(t *testing.T) {\n"
                                  "\tif got, err := Discount(1000, \"SAVE10\"); err != nil || got != 100 {\n\t\tt.Fatalf(\"got %d, %v\", got, err)\n\t}\n}\n\n"
                                  "func TestCodesIgnoreCaseAndSpacesSoHalfIsHALFAnd50PercentOff(t *testing.T) {\n"
                                  "\tif got, err := Discount(1000, \"  half \"); err != nil || got != 500 {\n\t\tt.Fatalf(\"got %d, %v\", got, err)\n\t}\n}\n\n"
                                  "func TestAnUnknownCodeIsTheErrorUnknownCodeFREE(t *testing.T) {\n"
                                  "\tif _, err := Discount(1000, \"FREE\"); err == nil || err.Error() != \"unknown code: FREE\" {\n"
                                  "\t\tt.Fatalf(\"err = %v\", err)\n\t}\n}\n"),
    }
    pr2 = {
        "cart/cart.go": cart.replace("on the whole order at ratePercent, rounded half up", "at ratePercent, each line rounded half up").replace(
            "\treturn (Total(lines)*ratePercent + 50) / 100\n",
            "\tsum := 0\n\tfor _, line := range lines {\n\t\tsum += (line*ratePercent + 50) / 100\n\t}\n\treturn sum\n"),
        "cart/tax_test.go": base["cart/tax_test.go"] + ("\nfunc TestTaxRoundsEachLineBeforeAdding6And6CentsAt8PercentIs0(t *testing.T) {\n"
                                                        "\tif got := Tax([]int{6, 6}, 8); got != 0 {\n\t\tt.Fatalf(\"Tax = %d, want 0\", got)\n\t}\n}\n"),
    }
    return base, pr1, pr2


def _java():
    pkg = "src/main/java/com/acme/shop"
    tpk = "src/test/java/com/acme/shop"
    cart = ("package com.acme.shop;\n\n/** Sums carts and taxes them, in cents. */\npublic final class Cart {\n    private Cart() {}\n\n"
            "    /** The sum of prices in cents. */\n    public static int total(int... prices) {\n        int sum = 0;\n"
            "        for (int p : prices) sum += p;\n        return sum;\n    }\n\n"
            "    /** Tax in cents on the whole order at ratePercent, rounded half up. */\n"
            "    public static int tax(int ratePercent, int... lines) {\n        return (total(lines) * ratePercent + 50) / 100;\n    }\n}\n")
    test = ("package com.acme.shop;\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
            "import org.junit.jupiter.api.Test;\n\nclass {cls} {{\n{body}}}\n")
    base = {
        "pom.xml": ("<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n  <modelVersion>4.0.0</modelVersion>\n"
                    "  <groupId>com.acme</groupId>\n  <artifactId>shop</artifactId>\n  <version>1.0.0</version>\n"
                    "  <properties>\n    <maven.compiler.release>21</maven.compiler.release>\n"
                    "    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>\n  </properties>\n"
                    "  <dependencies>\n    <dependency>\n      <groupId>org.junit.jupiter</groupId>\n      <artifactId>junit-jupiter</artifactId>\n"
                    "      <version>5.11.4</version>\n      <scope>test</scope>\n    </dependency>\n  </dependencies>\n"
                    "  <build>\n    <plugins>\n      <plugin>\n        <groupId>org.apache.maven.plugins</groupId>\n"
                    "        <artifactId>maven-surefire-plugin</artifactId>\n        <version>3.5.2</version>\n      </plugin>\n"
                    "    </plugins>\n  </build>\n</project>\n"),
        f"{pkg}/Cart.java": cart,
        f"{tpk}/CartTest.java": test.format(cls="CartTest", body="    @Test\n    void totalSumsPricesInCents() {\n        assertEquals(400, Cart.total(150, 250));\n    }\n"),
        f"{tpk}/TaxTest.java": test.format(cls="TaxTest", body="    @Test\n    void taxOn1000CentsAt8PercentIs80() {\n        assertEquals(80, Cart.tax(8, 1000));\n    }\n"),
    }
    pr1 = {
        f"{pkg}/Discount.java": ("package com.acme.shop;\n\nimport java.util.Locale;\nimport java.util.Map;\n\n"
                                 "/** Discount codes: SAVE10 is 10%, HALF is 50%; codes ignore case and surrounding spaces. */\n"
                                 "public final class Discount {\n    private static final Map<String, Integer> CODES = Map.of(\"SAVE10\", 10, \"HALF\", 50);\n\n"
                                 "    private Discount() {}\n\n    public static int of(int subtotal, String code) {\n"
                                 "        Integer rate = CODES.get(code.strip().toUpperCase(Locale.ROOT));\n"
                                 "        if (rate == null) throw new IllegalArgumentException(\"unknown code: \" + code.strip());\n"
                                 "        return subtotal * rate / 100;\n    }\n}\n"),
        f"{tpk}/DiscountTest.java": ("package com.acme.shop;\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n"
                                     "import static org.junit.jupiter.api.Assertions.assertThrows;\n\nimport org.junit.jupiter.api.Test;\n\n"
                                     "class DiscountTest {\n    @Test\n    void discountOf1000CentsWithSave10Is100() {\n"
                                     "        assertEquals(100, Discount.of(1000, \"SAVE10\"));\n    }\n\n"
                                     "    @Test\n    void codesIgnoreCaseAndSpacesSoHalfIsFiftyPercentOff() {\n"
                                     "        assertEquals(500, Discount.of(1000, \"  half \"));\n    }\n\n"
                                     "    @Test\n    void anUnknownCodeThrowsIllegalArgumentExceptionUnknownCodeFree() {\n"
                                     "        var e = assertThrows(IllegalArgumentException.class, () -> Discount.of(1000, \"FREE\"));\n"
                                     "        assertEquals(\"unknown code: FREE\", e.getMessage());\n    }\n}\n"),
    }
    pr2 = {
        f"{pkg}/Cart.java": cart.replace("on the whole order at ratePercent, rounded half up", "at ratePercent, each line rounded half up").replace(
            "        return (total(lines) * ratePercent + 50) / 100;\n",
            "        int sum = 0;\n        for (int line : lines) sum += (line * ratePercent + 50) / 100;\n        return sum;\n"),
        f"{tpk}/TaxTest.java": test.format(cls="TaxTest", body=(
            "    @Test\n    void taxOn1000CentsAt8PercentIs80() {\n        assertEquals(80, Cart.tax(8, 1000));\n    }\n\n"
            "    @Test\n    void taxRoundsEachLineBeforeAdding6And6CentsAt8PercentIs0() {\n        assertEquals(0, Cart.tax(8, 6, 6));\n    }\n")),
    }
    return base, pr1, pr2


def _rust():
    lib = ("//! Sums carts and taxes them, in cents.\n\n/// The sum of prices in cents.\n"
           "pub fn total(prices: &[i64]) -> i64 {\n    prices.iter().sum()\n}\n\n"
           "/// Tax in cents on the whole order at `rate_percent`, rounded half up.\n"
           "pub fn tax(lines: &[i64], rate_percent: i64) -> i64 {\n    (total(lines) * rate_percent + 50) / 100\n}\n")
    lib1 = lib.replace("//! Sums carts and taxes them, in cents.\n\n", "//! Sums carts and taxes them, in cents.\n\npub mod discount;\n\n")
    base = {
        "Cargo.toml": "[package]\nname = \"shop\"\nversion = \"1.0.0\"\nedition = \"2021\"\n\n[dependencies]\n",
        ".config/nextest.toml": "[profile.ci.junit]\npath = \"junit.xml\"\n",
        "src/lib.rs": lib,
        "tests/cart.rs": "use shop::total;\n\n#[test]\nfn total_sums_prices_in_cents() {\n    assert_eq!(total(&[150, 250]), 400);\n}\n",
        "tests/tax.rs": "use shop::tax;\n\n#[test]\nfn tax_on_1000_cents_at_8_percent_is_80() {\n    assert_eq!(tax(&[1000], 8), 80);\n}\n",
    }
    pr1 = {
        "src/lib.rs": lib1,
        "src/discount.rs": ("//! Discount codes: SAVE10 is 10%, HALF is 50%; codes ignore case and surrounding spaces.\n\n"
                            "pub fn discount(subtotal: i64, code: &str) -> Result<i64, String> {\n"
                            "    let rate = match code.trim().to_uppercase().as_str() {\n        \"SAVE10\" => 10,\n        \"HALF\" => 50,\n"
                            "        _ => return Err(format!(\"unknown code: {}\", code.trim())),\n    };\n    Ok(subtotal * rate / 100)\n}\n"),
        "tests/discount.rs": ("use shop::discount::discount;\n\n"
                              "#[test]\nfn discount_of_1000_cents_with_save10_is_100() {\n    assert_eq!(discount(1000, \"SAVE10\"), Ok(100));\n}\n\n"
                              "#[test]\nfn codes_ignore_case_and_spaces_so_half_is_fifty_percent_off() {\n"
                              "    assert_eq!(discount(1000, \"  half \"), Ok(500));\n}\n\n"
                              "#[test]\nfn an_unknown_code_is_the_error_unknown_code_free() {\n"
                              "    assert_eq!(discount(1000, \"FREE\"), Err(\"unknown code: FREE\".to_string()));\n}\n"),
    }
    pr2 = {
        "src/lib.rs": lib1.replace("on the whole order at `rate_percent`, rounded half up", "at `rate_percent`, each line rounded half up").replace(
            "    (total(lines) * rate_percent + 50) / 100\n", "    lines.iter().map(|line| (line * rate_percent + 50) / 100).sum()\n"),
        "tests/tax.rs": base["tests/tax.rs"] + ("\n#[test]\nfn tax_rounds_each_line_before_adding_6_and_6_cents_at_8_percent_is_0() {\n"
                                                "    assert_eq!(tax(&[6, 6], 8), 0);\n}\n"),
    }
    return base, pr1, pr2


def _csharp():
    cart = ("namespace Shop;\n\n/// <summary>Sums carts and taxes them, in cents.</summary>\npublic static class Cart\n{\n"
            "    public static long Total(params long[] prices) => prices.Sum();\n\n"
            "    /// <summary>Tax in cents on the whole order at ratePercent, rounded half up.</summary>\n"
            "    public static long Tax(long ratePercent, params long[] lines) => (Total(lines) * ratePercent + 50) / 100;\n}\n")
    test = "namespace Shop.Tests;\n\npublic class {cls}\n{{\n{body}}}\n"
    base = {
        "Shop/Shop.csproj": ("<Project Sdk=\"Microsoft.NET.Sdk\">\n  <PropertyGroup>\n    <TargetFramework>net8.0</TargetFramework>\n"
                             "    <ImplicitUsings>enable</ImplicitUsings>\n    <Nullable>enable</Nullable>\n  </PropertyGroup>\n</Project>\n"),
        "Shop/Cart.cs": cart,
        "Shop.Tests/Shop.Tests.csproj": ("<Project Sdk=\"Microsoft.NET.Sdk\">\n  <PropertyGroup>\n    <TargetFramework>net8.0</TargetFramework>\n"
                                         "    <ImplicitUsings>enable</ImplicitUsings>\n    <IsPackable>false</IsPackable>\n  </PropertyGroup>\n"
                                         "  <ItemGroup>\n    <PackageReference Include=\"Microsoft.NET.Test.Sdk\" Version=\"17.12.0\" />\n"
                                         "    <PackageReference Include=\"xunit\" Version=\"2.9.2\" />\n"
                                         "    <PackageReference Include=\"xunit.runner.visualstudio\" Version=\"2.8.2\" />\n"
                                         "    <PackageReference Include=\"JunitXml.TestLogger\" Version=\"4.1.0\" />\n  </ItemGroup>\n"
                                         "  <ItemGroup>\n    <Using Include=\"Xunit\" />\n    <ProjectReference Include=\"../Shop/Shop.csproj\" />\n"
                                         "  </ItemGroup>\n</Project>\n"),
        "Shop.Tests/CartTests.cs": test.format(cls="CartTests", body="    [Fact]\n    public void TotalSumsPricesInCents() => Assert.Equal(400, Cart.Total(150, 250));\n"),
        "Shop.Tests/TaxTests.cs": test.format(cls="TaxTests", body="    [Fact]\n    public void TaxOn1000CentsAt8PercentIs80() => Assert.Equal(80, Cart.Tax(8, 1000));\n"),
    }
    pr1 = {
        "Shop/Discount.cs": ("namespace Shop;\n\n/// <summary>Discount codes: SAVE10 is 10%, HALF is 50%; codes ignore case and surrounding spaces.</summary>\n"
                             "public static class Discount\n{\n    private static readonly Dictionary<string, long> Codes = new() { [\"SAVE10\"] = 10, [\"HALF\"] = 50 };\n\n"
                             "    public static long Of(long subtotal, string code)\n    {\n"
                             "        if (!Codes.TryGetValue(code.Trim().ToUpperInvariant(), out var rate))\n"
                             "            throw new ArgumentException($\"unknown code: {code.Trim()}\");\n        return subtotal * rate / 100;\n    }\n}\n"),
        "Shop.Tests/DiscountTests.cs": test.format(cls="DiscountTests", body=(
            "    [Fact]\n    public void DiscountOf1000CentsWithSave10Is100() => Assert.Equal(100, Discount.Of(1000, \"SAVE10\"));\n\n"
            "    [Fact]\n    public void CodesIgnoreCaseAndSpacesSoHalfIsFiftyPercentOff() => Assert.Equal(500, Discount.Of(1000, \"  half \"));\n\n"
            "    [Fact]\n    public void AnUnknownCodeThrowsArgumentExceptionUnknownCodeFree() =>\n"
            "        Assert.Equal(\"unknown code: FREE\", Assert.Throws<ArgumentException>(() => Discount.Of(1000, \"FREE\")).Message);\n")),
    }
    pr2 = {
        "Shop/Cart.cs": cart.replace("on the whole order at ratePercent, rounded half up", "at ratePercent, each line rounded half up").replace(
            "(Total(lines) * ratePercent + 50) / 100;", "lines.Sum(line => (line * ratePercent + 50) / 100);"),
        "Shop.Tests/TaxTests.cs": test.format(cls="TaxTests", body=(
            "    [Fact]\n    public void TaxOn1000CentsAt8PercentIs80() => Assert.Equal(80, Cart.Tax(8, 1000));\n\n"
            "    [Fact]\n    public void TaxRoundsEachLineBeforeAdding6And6CentsAt8PercentIs0() => Assert.Equal(0, Cart.Tax(8, 6, 6));\n")),
    }
    return base, pr1, pr2


def _ruby():
    cart = ("# Sums carts and taxes them, in cents.\nmodule Shop\n  module Cart\n    module_function\n\n"
            "    # The sum of prices in cents.\n    def total(prices) = prices.sum\n\n"
            "    # Tax in cents on the whole order at rate_percent, rounded half up.\n"
            "    def tax(lines, rate_percent) = (total(lines) * rate_percent + 50) / 100\n  end\nend\n")
    base = {
        "Gemfile": "source \"https://rubygems.org\"\n\ngem \"rspec\", \"~> 3.13\"\ngem \"rspec_junit_formatter\", \"~> 0.6\"\n",
        "lib/shop/cart.rb": cart,
        "spec/cart_spec.rb": ("require_relative \"../lib/shop/cart\"\n\nRSpec.describe Shop::Cart do\n"
                              "  it \"total sums prices in cents\" do\n    expect(described_class.total([150, 250])).to eq(400)\n  end\nend\n"),
        "spec/tax_spec.rb": ("require_relative \"../lib/shop/cart\"\n\nRSpec.describe Shop::Cart do\n"
                             "  it \"taxes 1000 cents at 8% as 80\" do\n    expect(described_class.tax([1000], 8)).to eq(80)\n  end\nend\n"),
    }
    pr1 = {
        "lib/shop/discount.rb": ("# Discount codes: SAVE10 is 10%, HALF is 50%; codes ignore case and surrounding spaces.\nmodule Shop\n  module Discount\n"
                                 "    CODES = { \"SAVE10\" => 10, \"HALF\" => 50 }.freeze\n\n    module_function\n\n"
                                 "    def of(subtotal, code)\n      rate = CODES.fetch(code.strip.upcase) { raise ArgumentError, \"unknown code: #{code.strip}\" }\n"
                                 "      subtotal * rate / 100\n    end\n  end\nend\n"),
        "spec/discount_spec.rb": ("require_relative \"../lib/shop/discount\"\n\nRSpec.describe Shop::Discount do\n"
                                  "  it \"gives 100 cents off 1000 with SAVE10\" do\n    expect(described_class.of(1000, \"SAVE10\")).to eq(100)\n  end\n\n"
                                  "  it \"ignores case and spaces, so half is HALF and 50% off\" do\n    expect(described_class.of(1000, \"  half \")).to eq(500)\n  end\n\n"
                                  "  it \"raises ArgumentError unknown code: FREE for a code it does not know\" do\n"
                                  "    expect { described_class.of(1000, \"FREE\") }.to raise_error(ArgumentError, \"unknown code: FREE\")\n  end\nend\n"),
    }
    pr2 = {
        "lib/shop/cart.rb": cart.replace("on the whole order at rate_percent, rounded half up", "at rate_percent, each line rounded half up").replace(
            "def tax(lines, rate_percent) = (total(lines) * rate_percent + 50) / 100", "def tax(lines, rate_percent) = lines.sum { |line| (line * rate_percent + 50) / 100 }"),
        "spec/tax_spec.rb": base["spec/tax_spec.rb"].replace("  end\nend\n", "  end\n\n  it \"rounds each line to the cent before adding: 6 and 6 cents at 8% is 0\" do\n"
                                                              "    expect(described_class.tax([6, 6], 8)).to eq(0)\n  end\nend\n"),
    }
    return base, pr1, pr2


def _php():
    cart = ("<?php\n\ndeclare(strict_types=1);\n\nnamespace Acme\\Shop;\n\n/** Sums carts and taxes them, in cents. */\nfinal class Cart\n{\n"
            "    /** The sum of prices in cents. */\n    public static function total(int ...$prices): int\n    {\n        return array_sum($prices);\n    }\n\n"
            "    /** Tax in cents on the whole order at $ratePercent, rounded half up. */\n"
            "    public static function tax(int $ratePercent, int ...$lines): int\n    {\n        return intdiv(self::total(...$lines) * $ratePercent + 50, 100);\n    }\n}\n")
    test = ("<?php\n\ndeclare(strict_types=1);\n\nnamespace Acme\\Shop\\Tests;\n\n{uses}use PHPUnit\\Framework\\TestCase;\n\n"
            "final class {cls} extends TestCase\n{{\n{body}}}\n")
    base = {
        "composer.json": ('{\n    "name": "acme/shop",\n    "autoload": { "psr-4": { "Acme\\\\Shop\\\\": "src/" } },\n'
                          '    "require-dev": { "phpunit/phpunit": "^11.5" }\n}\n'),
        "src/Cart.php": cart,
        "tests/CartTest.php": test.format(uses="use Acme\\Shop\\Cart;\n", cls="CartTest", body=(
            "    public function testTotalSumsPricesInCents(): void\n    {\n        $this->assertSame(400, Cart::total(150, 250));\n    }\n")),
        "tests/TaxTest.php": test.format(uses="use Acme\\Shop\\Cart;\n", cls="TaxTest", body=(
            "    public function testTaxOn1000CentsAt8PercentIs80(): void\n    {\n        $this->assertSame(80, Cart::tax(8, 1000));\n    }\n")),
    }
    pr1 = {
        "src/Discount.php": ("<?php\n\ndeclare(strict_types=1);\n\nnamespace Acme\\Shop;\n\n"
                             "/** Discount codes: SAVE10 is 10%, HALF is 50%; codes ignore case and surrounding spaces. */\nfinal class Discount\n{\n"
                             "    private const CODES = ['SAVE10' => 10, 'HALF' => 50];\n\n"
                             "    public static function of(int $subtotal, string $code): int\n    {\n"
                             "        $rate = self::CODES[strtoupper(trim($code))] ?? null;\n"
                             "        if ($rate === null) {\n            throw new \\InvalidArgumentException('unknown code: ' . trim($code));\n        }\n"
                             "        return intdiv($subtotal * $rate, 100);\n    }\n}\n"),
        "tests/DiscountTest.php": test.format(uses="use Acme\\Shop\\Discount;\n", cls="DiscountTest", body=(
            "    public function testDiscountOf1000CentsWithSave10Is100(): void\n    {\n        $this->assertSame(100, Discount::of(1000, 'SAVE10'));\n    }\n\n"
            "    public function testCodesIgnoreCaseAndSpacesSoHalfIsFiftyPercentOff(): void\n    {\n"
            "        $this->assertSame(500, Discount::of(1000, '  half '));\n    }\n\n"
            "    public function testAnUnknownCodeThrowsInvalidArgumentExceptionUnknownCodeFree(): void\n    {\n"
            "        $this->expectException(\\InvalidArgumentException::class);\n        $this->expectExceptionMessage('unknown code: FREE');\n"
            "        Discount::of(1000, 'FREE');\n    }\n")),
    }
    pr2 = {
        "src/Cart.php": cart.replace("on the whole order at $ratePercent, rounded half up", "at $ratePercent, each line rounded half up").replace(
            "        return intdiv(self::total(...$lines) * $ratePercent + 50, 100);\n",
            "        return array_sum(array_map(fn (int $line): int => intdiv($line * $ratePercent + 50, 100), $lines));\n"),
        "tests/TaxTest.php": test.format(uses="use Acme\\Shop\\Cart;\n", cls="TaxTest", body=(
            "    public function testTaxOn1000CentsAt8PercentIs80(): void\n    {\n        $this->assertSame(80, Cart::tax(8, 1000));\n    }\n\n"
            "    public function testTaxRoundsEachLineBeforeAdding6And6CentsAt8PercentIs0(): void\n    {\n"
            "        $this->assertSame(0, Cart::tax(8, 6, 6));\n    }\n")),
    }
    return base, pr1, pr2


LANGS = {"python": _python, "typescript": _typescript, "go": _go, "java": _java, "rust": _rust, "csharp": _csharp,
         "ruby": _ruby, "php": _php}


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in LANGS:
        print(__doc__, file=sys.stderr)
        return 2
    lang, root = sys.argv[1], Path(sys.argv[2])
    base, pr1, pr2 = LANGS[lang]()
    root.mkdir(parents=True)
    day = iter(f"2026-01-{d:02d}T10:00:00Z" for d in range(1, 29))

    def git(*args):
        date = next(day)
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                       env=dict(ENV, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date))

    def write(files):
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    git("init", "-q", "-b", "main")
    write({"README.md": f"# shop ({lang})\n\nA tiny cart library.\n", **base})
    git("add", "-A")
    git("commit", "-q", "-m", "A tiny cart library")
    readme = {"README.md": f"# shop ({lang})\n\nA tiny cart library: totals, tax and discount codes, in cents.\n"}
    for n, (branch, files) in enumerate((("discount-codes", pr1), ("tax-per-line", pr2), ("readme-usage", readme)), 1):
        git("checkout", "-q", "-b", branch)
        write(files)
        git("add", "-A")
        git("commit", "-q", "-m", TITLES[n - 1])
        git("checkout", "-q", "main")
        git("merge", "-q", "--no-ff", branch, "-m", f"Merge pull request #{n} from acme/{branch}\n\n{TITLES[n - 1]}")
    print(f"{root}: {lang}, 3 merged pull requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
