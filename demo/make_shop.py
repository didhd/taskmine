"""Make `shop`, a small synthetic repository for the public demo: a Node library with node:test tests and
six merged pull requests, written by this script, so the demo shows no real or private code.

    python3 demo/make_shop.py DIR

Four pull requests change code together with tests that prove it, so they can become tasks. The
other two are the kinds taskmine leaves out:
- #3 changes only documentation;
- #6 adds a test that reads the source text of the file the change touches, so it passes on
  spelling, not behaviour.
Author and dates are fixed, so the repository is the same on every machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ENV = dict(os.environ, GIT_AUTHOR_NAME="Demo", GIT_AUTHOR_EMAIL="demo@example.com", GIT_COMMITTER_NAME="Demo",
           GIT_COMMITTER_EMAIL="demo@example.com", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
DAY = iter(f"2026-0{m}-{d:02d}T10:00:00Z" for m in (1, 2, 3) for d in range(1, 29))


def git(root: Path, *args: str) -> None:
    date = next(DAY)
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   env=dict(ENV, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date))


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def pr(root: Path, n: int, branch: str, title: str, files: dict[str, str]) -> None:
    git(root, "checkout", "-q", "-b", branch)
    write(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", title)
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", branch, "-m", f"Merge pull request #{n} from acme/{branch}\n\n{title}")


BASE = {
    "package.json": '{\n  "name": "shop",\n  "version": "1.0.0",\n  "type": "module",\n'
                    '  "scripts": { "test": "node --test test/" }\n}\n',
    "README.md": "# shop\n\nA tiny cart library.\n",
    "lib/cart.mjs": (
        "// A cart is a list of lines: { sku, price (cents), qty }.\n"
        "export function createCart() {\n  return { lines: [] };\n}\n\n"
        "export function addItem(cart, sku, price, qty = 1) {\n"
        "  if (!Number.isInteger(price) || price < 0) throw new RangeError('price must be whole cents');\n"
        "  const line = cart.lines.find((l) => l.sku === sku);\n"
        "  if (line) line.qty += qty;\n  else cart.lines.push({ sku, price, qty });\n  return cart;\n}\n\n"
        "export function subtotal(cart) {\n  return cart.lines.reduce((sum, l) => sum + l.price * l.qty, 0);\n}\n"),
    "lib/tax.mjs": (
        "// Tax in cents for a cart, at `rate` (0.08 is 8%).\n"
        "import { subtotal } from './cart.mjs';\n\n"
        "export function tax(cart, rate) {\n  return Math.round(subtotal(cart) * rate);\n}\n"),
    "test/cart.test.mjs": (
        "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { createCart, addItem, subtotal } from '../lib/cart.mjs';\n\n"
        "test('adds lines and sums them', () => {\n  const c = createCart();\n"
        "  addItem(c, 'pen', 150, 2);\n  addItem(c, 'pad', 400);\n  assert.equal(subtotal(c), 700);\n});\n\n"
        "test('merges a repeated sku into one line', () => {\n  const c = createCart();\n"
        "  addItem(c, 'pen', 150);\n  addItem(c, 'pen', 150, 3);\n  assert.equal(c.lines.length, 1);\n"
        "  assert.equal(c.lines[0].qty, 4);\n});\n\n"
        "test('refuses a price that is not whole cents', () => {\n"
        "  assert.throws(() => addItem(createCart(), 'pen', 1.5), RangeError);\n});\n"),
    "test/tax.test.mjs": (
        "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { createCart, addItem } from '../lib/cart.mjs';\nimport { tax } from '../lib/tax.mjs';\n\n"
        "test('taxes the subtotal', () => {\n  const c = createCart();\n  addItem(c, 'pad', 1000);\n"
        "  assert.equal(tax(c, 0.08), 80);\n});\n"),
}

DISCOUNT = {
    "lib/discount.mjs": (
        "// Discount codes: SAVE10 takes 10% off, HALF takes half off; codes are case-insensitive.\n"
        "const CODES = { SAVE10: 0.10, HALF: 0.50 };\n\n"
        "export function discountRate(code) {\n  const rate = CODES[String(code).trim().toUpperCase()];\n"
        "  if (rate === undefined) throw new Error(`unknown code: ${code}`);\n  return rate;\n}\n\n"
        "export function applyCode(cart, code) {\n  cart.code = String(code).trim().toUpperCase();\n"
        "  discountRate(cart.code);\n  return cart;\n}\n\n"
        "export function discount(cart, subtotalCents) {\n"
        "  return cart.code ? Math.round(subtotalCents * discountRate(cart.code)) : 0;\n}\n"),
    "test/discount.test.mjs": (
        "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { createCart, addItem, subtotal } from '../lib/cart.mjs';\n"
        "import { applyCode, discount } from '../lib/discount.mjs';\n\n"
        "test('SAVE10 takes ten percent off', () => {\n  const c = addItem(createCart(), 'pad', 1000);\n"
        "  applyCode(c, 'SAVE10');\n  assert.equal(discount(c, subtotal(c)), 100);\n});\n\n"
        "test('codes ignore case and spaces', () => {\n  const c = addItem(createCart(), 'pad', 1000);\n"
        "  applyCode(c, '  half ');\n  assert.equal(discount(c, subtotal(c)), 500);\n});\n\n"
        "test('an unknown code is refused', () => {\n"
        "  assert.throws(() => applyCode(createCart(), 'FREE'), /unknown code/);\n});\n"),
}

TAX_PER_LINE = {
    "lib/tax.mjs": (
        "// Tax in cents for a cart, at `rate` (0.08 is 8%), rounded per line as receipts print it.\n"
        "export function tax(cart, rate) {\n"
        "  return cart.lines.reduce((sum, l) => sum + Math.round(l.price * l.qty * rate), 0);\n}\n"),
    "test/tax.test.mjs": (
        "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { createCart, addItem } from '../lib/cart.mjs';\nimport { tax } from '../lib/tax.mjs';\n\n"
        "test('taxes the subtotal', () => {\n  const c = createCart();\n  addItem(c, 'pad', 1000);\n"
        "  assert.equal(tax(c, 0.08), 80);\n});\n\n"
        "test('rounds each line to the cent before adding', () => {\n  const c = createCart();\n"
        "  addItem(c, 'gum', 6);\n  addItem(c, 'mint', 6);\n"
        "  // 0.48 + 0.48 cents: each rounds to 0, the order total would round 0.96 to 1\n"
        "  assert.equal(tax(c, 0.08), 0);\n});\n"),
}

DOCS = {"README.md": "# shop\n\nA tiny cart library.\n\n```js\nimport { createCart, addItem } from './lib/cart.mjs';\n```\n"}

STOCK = {
    "lib/stock.mjs": (
        "// Stock levels per sku; reserve() holds units for a cart and refuses what is not there.\n"
        "export function createStock(levels) {\n  return { levels: { ...levels }, held: {} };\n}\n\n"
        "export function available(stock, sku) {\n"
        "  return (stock.levels[sku] ?? 0) - (stock.held[sku] ?? 0);\n}\n\n"
        "export function reserve(stock, sku, qty) {\n"
        "  if (qty > available(stock, sku)) throw new Error(`only ${available(stock, sku)} ${sku} left`);\n"
        "  stock.held[sku] = (stock.held[sku] ?? 0) + qty;\n  return stock;\n}\n\n"
        "export function release(stock, sku, qty) {\n"
        "  stock.held[sku] = Math.max(0, (stock.held[sku] ?? 0) - qty);\n  return stock;\n}\n"),
    "test/stock.test.mjs": (
        "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { createStock, reserve, release, available } from '../lib/stock.mjs';\n\n"
        "test('reserving lowers what is available', () => {\n  const s = createStock({ pen: 5 });\n"
        "  reserve(s, 'pen', 3);\n  assert.equal(available(s, 'pen'), 2);\n});\n\n"
        "test('reserving more than is left is refused', () => {\n  const s = createStock({ pen: 2 });\n"
        "  assert.throws(() => reserve(s, 'pen', 3), /only 2 pen left/);\n});\n\n"
        "test('releasing gives units back, never below zero', () => {\n  const s = createStock({ pen: 2 });\n"
        "  reserve(s, 'pen', 2);\n  release(s, 'pen', 5);\n  assert.equal(available(s, 'pen'), 2);\n});\n"),
}

CSV = {
    "lib/csv.mjs": (
        "// One CSV row per cart line: sku,qty,price,total. Fields with a comma, quote or newline are quoted.\n"
        "function field(v) {\n  const s = String(v);\n"
        "  return /[\",\\n]/.test(s) ? '\"' + s.replaceAll('\"', '\"\"') + '\"' : s;\n}\n\n"
        "export function toCsv(cart) {\n  const rows = [['sku', 'qty', 'price', 'total']];\n"
        "  for (const l of cart.lines) rows.push([l.sku, l.qty, l.price, l.price * l.qty]);\n"
        "  return rows.map((r) => r.map(field).join(',')).join('\\n') + '\\n';\n}\n"),
    "test/csv.test.mjs": (
        "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { createCart, addItem } from '../lib/cart.mjs';\nimport { toCsv } from '../lib/csv.mjs';\n\n"
        "test('one row per line under a header', () => {\n  const c = addItem(createCart(), 'pen', 150, 2);\n"
        "  assert.equal(toCsv(c), 'sku,qty,price,total\\npen,2,150,300\\n');\n});\n\n"
        "test('quotes a field with a comma or a quote', () => {\n"
        "  const c = addItem(createCart(), 'pen, \"blue\"', 150);\n"
        "  assert.equal(toCsv(c).split('\\n')[1], '\"pen, \"\"blue\"\"\",1,150,150');\n});\n"),
}

RENAME = {
    "lib/cart.mjs": BASE["lib/cart.mjs"].replace("// A cart is a list of lines", "// A cart holds a list of lines"),
    "test/cart-source.test.mjs": (
        "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\nimport { readFileSync } from 'node:fs';\n\n"
        "test('the cart module documents what a cart holds', () => {\n"
        "  const src = readFileSync(new URL('../lib/cart.mjs', import.meta.url), 'utf8');\n"
        "  assert.match(src, /A cart holds a list of lines/);\n});\n"),
}


def main() -> int:
    root = Path(sys.argv[1])
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    write(root, BASE)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "A tiny cart library")
    pr(root, 1, "discount-codes", "Add discount codes (SAVE10, HALF)", DISCOUNT)
    pr(root, 2, "tax-per-line", "Round tax per line, as receipts print it", TAX_PER_LINE)
    pr(root, 3, "readme-usage", "Show usage in the README", DOCS)
    pr(root, 4, "stock-reservations", "Reserve stock for a cart", STOCK)
    pr(root, 5, "csv-export", "Export a cart as CSV", CSV)
    pr(root, 6, "cart-comment", "Reword the cart module's comment", RENAME)
    print(f"{root}: 6 merged pull requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
