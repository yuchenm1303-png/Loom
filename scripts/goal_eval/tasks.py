"""Long tasks with automatic graders, to compare ways of running a long goal on what they produce and what they cost.

Each task has a prepare(workspace) that seeds the folder, a prompt in the person's words (the timed cases give time
and say they are away; open_research deliberately gives no time; none mention sub-agents), and a grade(workspace) that returns one result per check, computed
outside the agent's control: inputs and answers live here, the agent never sees them.

- toolkit: four independent modules, ideal for lines.
- bugs: a generated repository with seeded bugs in separate modules; hidden tests per bug. Lots of reading.
- phase: numerical study; answers are computed from theory here. Two parts are independent, the report is not.
- pipeline: each step feeds the next and they share one state file, so splitting it is a mistake; here lines must
  not make things worse, and the honest decision is solo_reason.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile

ONLY_STDLIB = "只在这个文件夹里工作，只用 Python 标准库，不要联网、不要装依赖。运行用的 Python：{python}\n"
AWAY = "我要出门一个小时，你有一个小时，我不在旁边，不用等我。\n"


def _clean_env():
    env = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONPATH", "PYTHONSAFEPATH", "PYTHONHOME"}}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_code(workspace, code, stdin=None, timeout=60):
    """Run hidden check code with the workspace as the import root; the result is the last JSON line it prints."""
    with tempfile.TemporaryDirectory(prefix="goal-eval-") as folder:
        script = Path(folder) / "hidden_check.py"
        script.write_text(code, encoding="utf-8")
        env = dict(_clean_env(), PYTHONPATH=str(workspace))
        try:
            run = subprocess.run([sys.executable, "-B", str(script)], cwd=workspace, input=stdin, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, "timeout"
    lines = [line for line in run.stdout.splitlines() if line.strip()]
    try:
        return json.loads(lines[-1]), ""
    except (IndexError, ValueError):
        return None, (run.stderr or run.stdout)[-300:]


def _checks(workspace, cases):
    """Each case is (name, code printing a JSON true when it passes)."""
    results = []
    for name, code in cases:
        value, why = _run_code(workspace, code)
        results.append({"case": name, "passed": value is True, **({} if value is True else {"why": str(why or value)[:200]})})
    return results


# ---------------------------------------------------------------- toolkit

TOOLKIT_PROMPT = AWAY + (
    "在这个文件夹里做一个纯 Python 工具包 toolkit/，四个互相独立的模块，每个模块都要有 tests/ 下的 unittest 测试，"
    "整个工具包要有 README.md 说明每个模块的用法。\n"
    "1. toolkit/expr.py：evaluate(text) 计算算术表达式，支持 + - * / ^（右结合）、括号、一元负号、整数和小数；"
    "结果用 fractions.Fraction 精确计算并返回 Fraction；除以零抛 ZeroDivisionError，语法错误抛 ValueError。不许用 eval。\n"
    "2. toolkit/csvq.py：query(text, sql) 对 CSV 文本（首行是表头）执行 SELECT 子集："
    "SELECT col[,col]|* [WHERE col op value [AND ...]] [ORDER BY col [DESC]] [LIMIT n]，op 是 = != < > <= >=；"
    "数字按数值比较，其他按字符串；返回 dict 列表，值保持原字符串。语法错误抛 ValueError。\n"
    "3. toolkit/cron.py：next_run(expr, after) 对 5 段 cron 表达式（分 时 日 月 周，支持 * , - /；周 0-6，0 是周日；"
    "日和周都不是 * 时按或的关系）返回严格晚于 datetime after 的下一个时刻（秒为 0）。语法错误抛 ValueError。\n"
    "4. toolkit/diffs.py：unified(a, b, context=3) 对两个行列表给出统一 diff 的 hunk 文本（不含 ---/+++ 头，"
    "格式同 difflib.unified_diff），不许 import difflib；apply(a, patch) 把这样的 patch 应用到 a 得到 b，上下文不符抛 ValueError。\n"
    + ONLY_STDLIB)


def _expr_case(text, expected):
    return ("expr " + text, "import json\nfrom fractions import Fraction\nfrom toolkit.expr import evaluate\n"
            f"print(json.dumps(evaluate({text!r}) == Fraction({expected!r})))")


def _raises(module, call, error):
    return (f"{module} raises {error}: {call[:30]}", f"import json\nfrom toolkit.{module} import *\n"
            f"try:\n    {call}\n    print('false')\nexcept {error}:\n    print('true')\nexcept Exception:\n    print('false')\n")


CSV = "name,age,city\\nann,30,Oslo\\nbob,5,Rome\\ncid,100,Oslo\\ndee,30,Lima\\n"


def _csv_case(sql, expected):
    return ("csvq " + sql, f"import json\nfrom toolkit.csvq import query\nprint(json.dumps(query('{CSV}', {sql!r}) == {expected!r}))")


def _row(name, age, city):
    return {"name": name, "age": age, "city": city}


def _cron_case(expr, after, expected):
    return (f"cron {expr} after {after}", "import json\nfrom datetime import datetime as D\nfrom toolkit.cron import next_run\n"
            f"print(json.dumps(next_run({expr!r}, D.fromisoformat({after!r})) == D.fromisoformat({expected!r})))")


def _diff_round(a, b):
    return (f"diffs round trip {len(a)}/{len(b)}", "import json\nfrom toolkit.diffs import unified, apply\n"
            f"a, b = {a!r}, {b!r}\nprint(json.dumps(apply(a, unified(a, b)) == b))")


def _diff_same(a, b):
    return (f"diffs matches difflib {len(a)}/{len(b)}", "import json, difflib\nfrom toolkit.diffs import unified\n"
            f"a, b = {a!r}, {b!r}\nwant = list(difflib.unified_diff(a, b, lineterm='', n=3))[2:]\n"
            "got = unified(a, b)\ngot = got.splitlines() if isinstance(got, str) else list(got)\n"
            "print(json.dumps([l.rstrip() for l in got] == [l.rstrip() for l in want]))")


def grade_toolkit(workspace):
    long_a = [f"line {n}" for n in range(30)]
    long_b = long_a[:5] + ["changed"] + long_a[6:20] + long_a[22:] + ["end"]
    cases = [
        _expr_case("1+2*3", "7"), _expr_case("2^3^2", "512"), _expr_case("-(1/3)+1", "2/3"), _expr_case("1.5*2", "3"),
        _expr_case("(2+3)*(4-1)/5", "3"), _expr_case("-3+5", "2"), _expr_case("0.1+0.2", "3/10"),
        _raises("expr", "evaluate('1/0')", "ZeroDivisionError"), _raises("expr", "evaluate('1+')", "ValueError"),
        _raises("expr", "evaluate('__import__(\"os\")')", "ValueError"),
        _csv_case("SELECT name FROM t WHERE age > 10 ORDER BY age DESC LIMIT 1".replace(" FROM t", ""), [{"name": "cid"}]),
        _csv_case("SELECT * WHERE city = Oslo", [_row("ann", "30", "Oslo"), _row("cid", "100", "Oslo")]),
        _csv_case("SELECT name ORDER BY age", [{"name": "bob"}, {"name": "ann"}, {"name": "dee"}, {"name": "cid"}]),
        _csv_case("SELECT name,city WHERE age >= 30 AND city != Oslo", [{"name": "dee", "city": "Lima"}]),
        _raises("csvq", f"query('{CSV}', 'SELEC name')", "ValueError"),
        _cron_case("*/15 * * * *", "2026-01-01T00:07:30", "2026-01-01T00:15:00"),
        _cron_case("0 9 * * 1", "2026-01-01T00:00:00", "2026-01-05T09:00:00"),
        _cron_case("30 8 13 * 5", "2026-02-14T00:00:00", "2026-02-20T08:30:00"),
        _cron_case("0 0 29 2 *", "2026-03-01T00:00:00", "2028-02-29T00:00:00"),
        _cron_case("5-10/2 1,3 * * *", "2026-01-01T01:09:00", "2026-01-01T03:05:00"),
        _raises("cron", "next_run('* * *', __import__('datetime').datetime(2026,1,1))", "ValueError"),
        _diff_round(["a", "b", "c"], ["a", "x", "c", "d"]), _diff_round(long_a, long_b), _diff_round([], ["x"]), _diff_round(["x"], []),
        _diff_same(long_a, long_b), _diff_same(["a", "b", "c"], ["a", "x", "c"]),
        _raises("diffs", "apply(['z','z','z'], unified(['a','b','c'], ['a','x','c']))", "ValueError"),
        # What the code does, not what its comments mention: a real diffs.py explained itself by naming difflib.
        ("no eval or difflib", "import ast, json, pathlib\ne = ast.parse(pathlib.Path('toolkit/expr.py').read_text('utf-8'))\n"
         "d = ast.parse(pathlib.Path('toolkit/diffs.py').read_text('utf-8'))\n"
         "calls = any(isinstance(n, ast.Call) and getattr(n.func, 'id', '') in {'eval', 'exec'} for n in ast.walk(e))\n"
         "names = [a.name for n in ast.walk(d) if isinstance(n, ast.Import) for a in n.names] + [n.module for n in ast.walk(d) if isinstance(n, ast.ImportFrom)]\n"
         "print(json.dumps(not calls and 'difflib' not in names))"),
    ]
    results = _checks(workspace, cases)
    run = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=workspace, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", env=_clean_env(), timeout=300)
    results.append({"case": "own tests pass", "passed": run.returncode == 0 and "Ran 0 tests" not in run.stderr})
    results.append({"case": "readme covers modules", "passed": (workspace / "README.md").exists() and all(
        name in (workspace / "README.md").read_text("utf-8", "replace") for name in ("expr", "csvq", "cron", "diffs"))})
    return results


# ---------------------------------------------------------------- bugs

BUGS_PROMPT = AWAY + (
    "这个文件夹是一个小的 Python 库 inventory/，有十二个模块，每个模块的 docstring 写明了它应该做什么。"
    "有人反映它有不少错误，但不知道具体在哪：请把所有和 docstring 不符的行为都修好，不要改函数签名，"
    "把每个修复记在 FIXES.md 里（模块、错在哪、怎么修的）。现有测试太少，抓不到这些错误，可以自己补。\n"
    + ONLY_STDLIB)

# module: (source, [(hidden check name, expression that is True when fixed)])
BUG_MODULES = {
    "money": ('''"""Money in integer cents. to_cents('12.30') is 1230; it accepts an optional leading minus and at most two decimals, and rejects anything else with ValueError. from_cents(-5) is '-0.05'."""
def to_cents(text):
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("-")
    whole, _, part = text.partition(".")
    return sign * (int(whole) * 100 + int(part or 0))


def from_cents(cents):
    return f"{cents // 100}.{cents % 100:02d}"
''', [("to_cents one decimal", "money.to_cents('12.3') == 1230"), ("from_cents negative", "money.from_cents(-5) == '-0.05'"),
      ("rejects three decimals", "raises(ValueError, money.to_cents, '1.234')")]),
    "dates": ('''"""Dates. add_months(date(2026,1,31), 1) clamps to the last day of the target month, date(2026,2,28). quarter(date) is 1..4. days_between(a, b) is the absolute number of days."""
from datetime import date


def add_months(day, months):
    month = day.month + months
    return date(day.year + month // 12, month % 12 or 12, day.day)


def quarter(day):
    return day.month // 3 + 1


def days_between(a, b):
    return (b - a).days
''', [("clamp", "dates.add_months(date(2026,1,31), 1) == date(2026,2,28)"), ("december", "dates.add_months(date(2026,11,15), 1) == date(2026,12,15)"),
      ("quarter", "dates.quarter(date(2026,3,1)) == 1 and dates.quarter(date(2026,12,1)) == 4"), ("abs", "dates.days_between(date(2026,1,2), date(2026,1,1)) == 1")]),
    "stock": ('''"""Stock levels. remove(levels, sku, n) lowers a level and raises ValueError, leaving levels unchanged, when there is not enough. A level reaching zero removes the sku. low(levels, limit) lists skus strictly below limit, sorted."""


def remove(levels, sku, n):
    levels[sku] = levels.get(sku, 0) - n
    if levels[sku] < 0:
        raise ValueError(sku)


def low(levels, limit):
    return [sku for sku, level in levels.items() if level <= limit]
''', [("unchanged on error", "check_remove()"), ("zero removes", "zero_removes()"), ("low strict sorted", "stock.low({'b': 1, 'a': 1, 'c': 5}, 5) == ['a', 'b']")]),
    "pricing": ('''"""Prices in integer cents. discount(cents, percent) rounds half up to whole cents. bulk(unit, qty) gives 10% off the whole line from 10 units and 20% from 100. tax(cents, rate_percent) rounds half up."""


def discount(cents, percent):
    return round(cents * (100 - percent) / 100)


def bulk(unit, qty):
    line = unit * qty
    if qty > 100:
        return discount(line, 20)
    if qty > 10:
        return discount(line, 10)
    return line


def tax(cents, rate_percent):
    return int(cents * rate_percent / 100)
''', [("half up", "pricing.discount(25, 50) == 13"), ("bulk thresholds", "pricing.bulk(100, 10) == 900 and pricing.bulk(100, 100) == 8000"), ("tax half up", "pricing.tax(50, 5) == 3")]),
    "search": ('''"""Search. find(items, word) is case-insensitive on whole words, keeping the items' order. rank(items, words) sorts by how many distinct words occur, most first, ties in the original order."""
import re


def find(items, word):
    return [item for item in items if word in item]


def rank(items, words):
    def score(item):
        return sum(1 for word in words if word.lower() in item.lower())
    return sorted(items, key=score)
''', [("whole word", "search.find(['red apple', 'redo'], 'RED') == ['red apple']"), ("rank order", "search.rank(['a b', 'a', 'b a c'], ['a', 'b', 'c']) == ['b a c', 'a b', 'a']"),
      ("rank distinct whole", "search.rank(['cat', 'a a', 'b'], ['a', 'b']) == ['a a', 'b', 'cat']")]),
    "paging": ('''"""Paging. page(items, number, size) is 1-based; a number past the end gives []. pages(total, size) is the number of pages, 0 for no items. number and size below 1 raise ValueError."""


def page(items, number, size):
    return items[number * size:(number + 1) * size]


def pages(total, size):
    return total // size + 1
''', [("first page", "paging.page([1,2,3], 1, 2) == [1,2]"), ("past end", "paging.page([1], 5, 2) == []"), ("pages exact", "paging.pages(4, 2) == 2 and paging.pages(0, 2) == 0 and paging.pages(5, 2) == 3"),
      ("rejects", "raises(ValueError, paging.page, [1], 0, 2)")]),
    "codes": ('''"""SKU codes look like AB-1234: two capital letters, a dash, four digits. valid(code) says so. normalize(text) trims and upper-cases, and adds the dash when missing. check_digit(code) is the sum of the digits mod 10."""
import re


def valid(code):
    return bool(re.match(r"[A-Z]{2}-\\d{4}", code))


def normalize(text):
    return text.upper()


def check_digit(code):
    return sum(int(ch) for ch in code if ch.isdigit()) % 9
''', [("fullmatch", "codes.valid('AB-1234x') is False and codes.valid('AB-1234') is True"), ("normalize", "codes.normalize(' ab1234 ') == 'AB-1234'"), ("check digit", "codes.check_digit('AB-9999') == 6")]),
    "merge": ('''"""Merging. merge_counts(a, b) sums counts of both without changing either. dedupe(items) keeps the first of each, in order. chunks(items, n) splits into lists of n, the last possibly shorter."""


def merge_counts(a, b):
    for key, value in b.items():
        a[key] = value
    return a


def dedupe(items):
    return list(set(items))


def chunks(items, n):
    return [items[at:at + n] for at in range(0, len(items) - n, n)]
''', [("sums", "merge.merge_counts({'a': 1}, {'a': 2}) == {'a': 3}"), ("no mutation", "no_mutation()"), ("order", "merge.dedupe([3,1,3,2,1]) == [3,1,2]"), ("last chunk", "merge.chunks([1,2,3], 2) == [[1,2],[3]]")]),
    "report": ('''"""Reports. table(rows) renders dicts as text: a header of the first row's keys, then one line per row, columns separated by ' | ' and padded to the widest cell. totals(rows, key) sums a numeric field, treating a missing value as 0."""


def table(rows):
    keys = list(rows[0])
    lines = [" | ".join(keys)]
    for row in rows:
        lines.append(" | ".join(str(row[key]) for key in keys))
    return "\\n".join(lines)


def totals(rows, key):
    return sum(row[key] for row in rows)
''', [("padding", "report.table([{'a': 1, 'bb': 2}, {'a': 100, 'bb': 3}]).splitlines() == ['a   | bb', '1   | 2 ', '100 | 3 ']"), ("missing", "report.totals([{'x': 1}, {}], 'x') == 1")]),
    "orders": ('''"""Orders move new -> paid -> shipped -> done, or new/paid -> cancelled. advance(state, to) returns to or raises ValueError for any other move. total(lines) sums qty*price of (qty, price) lines, with qty above 0 required, else ValueError."""
MOVES = {"new": ["paid"], "paid": ["shipped"], "shipped": ["done", "cancelled"], "done": []}


def advance(state, to):
    if to in MOVES[state] or to == "cancelled":
        return to
    raise ValueError(to)


def total(lines):
    return sum(qty * price for qty, price in lines)
''', [("cancel from paid", "orders.advance('paid', 'cancelled') == 'cancelled'"), ("no cancel after shipped", "raises(ValueError, orders.advance, 'shipped', 'cancelled')"), ("no cancel after done", "raises(ValueError, orders.advance, 'done', 'cancelled')"),
      ("cancelled final", "raises(ValueError, orders.advance, 'cancelled', 'paid')"), ("qty", "raises(ValueError, orders.total, [(0, 5)])")]),
    "units": ('''"""Units. convert(value, frm, to) converts among g, kg, t exactly, returning a Fraction. parse('1.5 kg') gives (Fraction(3,2), 'kg'), with any spacing. Unknown units raise ValueError."""
from fractions import Fraction

FACTOR = {"g": 1, "kg": 1000, "t": 100000}


def convert(value, frm, to):
    return Fraction(value) * FACTOR[to] / FACTOR[frm]


def parse(text):
    value, unit = text.split(" ")
    return Fraction(value), unit
''', [("direction", "units.convert(1, 'kg', 'g') == 1000"), ("tonne", "units.convert(1, 't', 'kg') == 1000"), ("spacing", "units.parse('1.5kg') == (Fraction(3,2), 'kg') and units.parse(' 2   t ') == (2, 't')"),
      ("unknown", "raises(ValueError, units.convert, 1, 'lb', 'g')")]),
    "queue": ('''"""A work queue. Queue.push(item, priority) and Queue.pop() returning the highest priority first, first in first out within a priority; pop on empty raises IndexError. len(queue) works. peek() is like pop without removing."""
import heapq


class Queue:
    def __init__(self):
        self.items = []

    def push(self, item, priority):
        heapq.heappush(self.items, (priority, item))

    def pop(self):
        return heapq.heappop(self.items)[0]

    def peek(self):
        return self.items[0]

    def __len__(self):
        return len(self.items) + 1
''', [("priority fifo", "queue_order()"), ("mixed types", "queue_mixed()"), ("len peek", "queue_len()")]),
}

BUG_HELPERS = """
from datetime import date
from fractions import Fraction
from inventory import money, dates, stock, pricing, search, paging, codes, merge, report, orders, units, queue
def raises(error, call, *args):
    try:
        call(*args)
    except error:
        return True
    except Exception:
        return False
    return False
def check_remove():
    levels = {'a': 1}
    return raises(ValueError, stock.remove, levels, 'a', 2) and levels == {'a': 1}
def zero_removes():
    levels = {'a': 1}
    stock.remove(levels, 'a', 1)
    return levels == {}
def no_mutation():
    a, b = {'a': 1}, {'a': 2}
    merge.merge_counts(a, b)
    return a == {'a': 1} and b == {'a': 2}
def queue_order():
    q = queue.Queue()
    for item, p in (('l', 1), ('h1', 9), ('h2', 9)):
        q.push(item, p)
    return [q.pop(), q.pop(), q.pop()] == ['h1', 'h2', 'l'] and raises(IndexError, q.pop)
def queue_mixed():
    q = queue.Queue()
    q.push({'a': 1}, 1)
    q.push({'b': 2}, 1)
    return q.pop() == {'a': 1}
def queue_len():
    q = queue.Queue()
    q.push('x', 1)
    return len(q) == 1 and q.peek() == 'x' and len(q) == 1
"""


def prepare_bugs(workspace):
    package = workspace / "inventory"
    package.mkdir()
    (package / "__init__.py").write_text('"""A small inventory library."""\n', encoding="utf-8")
    for name, (source, _) in BUG_MODULES.items():
        (package / f"{name}.py").write_text(source, encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_smoke.py").write_text(
        "import unittest\nfrom inventory import money\n\n\nclass Smoke(unittest.TestCase):\n"
        "    def test_money(self):\n        self.assertEqual(money.to_cents('12.30'), 1230)\n", encoding="utf-8")


def grade_bugs(workspace):
    cases = [(f"{name}: {check}", BUG_HELPERS + f"\nimport json\nprint(json.dumps(bool({expression})))")
             for name, (_, checks) in BUG_MODULES.items() for check, expression in checks]
    results = _checks(workspace, cases)
    fixes = workspace / "FIXES.md"
    text = fixes.read_text("utf-8", "replace") if fixes.exists() else ""
    results.append({"case": "FIXES.md names the modules", "passed": sum(name in text for name in BUG_MODULES) >= 10})
    return results


# ---------------------------------------------------------------- phase

PHASE_PROMPT = AWAY + (
    "请只用 Python 标准库做一个小研究：随机图的巨连通分量。\n"
    "1. Erdős–Rényi G(n, c/n)：对 c 取 0.5, 1.5, 2.0, 3.0，n 取 1000 和 100000，每组至少 10 次独立模拟，"
    "测量最大连通分量占比的均值，并与方程 S = 1 - exp(-cS) 的解比较。\n"
    "2. 配置模型：度分布是 P(1)=p、P(3)=1-p，p 取 0.2, 0.5, 0.8，n=100000，每组至少 5 次独立模拟，"
    "用生成函数算理论的巨分量占比并比较，并用 Molloy–Reed 判据算出临界的 p。\n"
    "3. 把结果写成 results.json，结构是 "
    '{"er": {"<c>": {"<n>": 平均占比}}, "er_theory": {"<c>": 理论值}, "config": {"<p>": 平均占比}, '
    '"config_theory": {"<p>": 理论值}, "critical_p": 临界 p}，键就是上面写的数字的字符串（如 "0.5"、"1000"），'
    "并写 report.md：方法、结果表、误差分析和结论，结论里的数字都要能用文件夹里的脚本 run_all.py 重新跑出来。\n"
    + ONLY_STDLIB)


def _er_theory(c):
    s = 0.5
    for _ in range(10000):
        s = 1 - math.exp(-c * s)
    return s if s > 1e-9 else 0.0


def _config_theory(p):
    """Degrees 1 and 3: G0(x)=p x+(1-p) x^3, excess G1(x)=(p + 3(1-p) x^2)/mean; u=G1(u), S=1-G0(u)."""
    mean = p + 3 * (1 - p)
    u = 0.5
    for _ in range(100000):
        u = (p + 3 * (1 - p) * u * u) / mean
    return 1 - (p * u + (1 - p) * u ** 3)


# Open-ended, no time given, literature allowed: the shape of the real "深入研究一下 AI-RAN" request, small enough for
# a CPU in an hour or two. What it exercises is the Host's research bar (standard criteria, literature, the claims
# ledger, the checker's re-runs), so the grade only checks that a reviewable deliverable exists.
OPEN_RESEARCH_PROMPT = (
    "你先自己深入研究一下布隆过滤器（Bloom filter）的一个小问题：在相同内存预算下，哈希函数个数 k 偏离理论最优值时误判率怎样变化；"
    "现实中的非理想哈希（比如 Python 内置 hash 加盐、简单的双重哈希 Kirsch–Mitzenmacher）会不会让经典误判率公式失准。"
    "看看能不能做出一些有价值的结论，最好能写成别人能评审的东西。\n"
    "只在这个文件夹里工作，只用 Python 标准库，不要装依赖；可以上网查文献。运行用的 Python：{python}\n")


def grade_open_research(workspace):
    report = next((path for path in workspace.rglob("*.md") if "readme" not in path.name.lower()), None)
    text = report.read_text("utf-8", errors="replace") if report else ""
    scripts = list(workspace.rglob("*.py"))
    return [{"case": "report", "passed": len(text) > 1500, "why": f"{report} {len(text)} chars"},
            {"case": "references", "passed": bool(re.search(r"https?://|arXiv|doi", text, re.I))},
            {"case": "scripts", "passed": bool(scripts)}]


def grade_phase(workspace):
    results = []

    def check(name, ok, why=""):
        results.append({"case": name, "passed": bool(ok), **({} if ok else {"why": str(why)[:200]})})
    path = workspace / "results.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return [{"case": "results.json", "passed": False, "why": str(exc)}]

    def near(value, want, tolerance):
        try:
            return abs(float(value) - want) <= tolerance
        except (TypeError, ValueError):
            return False

    def get(*keys):
        value = data
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        return value
    for c in ("0.5", "1.5", "2.0", "3.0"):
        theory = _er_theory(float(c))
        check(f"er theory c={c}", near(get("er_theory", c), theory, 1e-3), get("er_theory", c))
        big = get("er", c, "100000")
        check(f"er n=100000 c={c}", near(big, theory, 0.01), big)
        small = get("er", c, "1000")
        check(f"er n=1000 c={c}", near(small, theory, 0.06 if c != "0.5" else 0.03), small)
    for p in ("0.2", "0.5", "0.8"):
        theory = _config_theory(float(p))
        check(f"config theory p={p}", near(get("config_theory", p), theory, 2e-3), get("config_theory", p))
        check(f"config sim p={p}", near(get("config", p), theory, 0.015), get("config", p))
    check("critical p", near(data.get("critical_p"), 0.75, 1e-3), data.get("critical_p"))
    report = workspace / "report.md"
    text = report.read_text("utf-8", "replace") if report.exists() else ""
    check("report has the numbers", len(text) > 500 and "0.75" in text and ("0.79" in text or "0.80" in text))
    check("run_all.py exists", (workspace / "run_all.py").exists())
    return results


# ---------------------------------------------------------------- pipeline

PIPELINE_PROMPT = AWAY + (
    "在这个文件夹里写一条数据流水线，每一步都依赖上一步的输出和同一个 state.json：\n"
    "1. step1_generate.py：用 random.Random(2026) 生成 2000 条订单写进 orders.csv（id, customer 取 c0..c49, amount 取 1..500 的整数），"
    "并把总额写进 state.json 的 total。\n"
    "2. step2_clean.py：读 orders.csv，去掉 amount 能被 7 整除的行，写 clean.csv，把剩下的总额写进 state.json 的 clean_total。\n"
    "3. step3_rollup.py：读 clean.csv，算每个 customer 的总额，写 rollup.json；金额最大的客户写进 state.json 的 top_customer，"
    "它的金额写进 top_amount。\n"
    "4. step4_scale.py：读 state.json，把 rollup.json 里每个金额乘以 clean_total / total（保留 6 位小数），写 scaled.json，"
    "总和写进 state.json 的 scaled_sum。\n"
    "5. run_all.py 依次跑这四步；再跑一次结果必须完全相同。写 NOTES.md 说明每一步。\n"
    + ONLY_STDLIB)


def _pipeline_truth():
    rng = random.Random(2026)
    orders = [(index, f"c{rng.randrange(50)}", rng.randint(1, 500)) for index in range(2000)]
    return orders


def grade_pipeline(workspace):
    results = []

    def check(name, ok, why=""):
        results.append({"case": name, "passed": bool(ok), **({} if ok else {"why": str(why)[:200]})})
    run_all = workspace / "run_all.py"
    if not run_all.exists():
        return [{"case": "run_all.py", "passed": False}]
    with tempfile.TemporaryDirectory(prefix="goal-eval-") as folder:
        copy = Path(folder) / "copy"
        import shutil
        shutil.copytree(workspace, copy, ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("orders.csv", "clean.csv", "rollup.json", "scaled.json", "state.json"):
            (copy / name).unlink(missing_ok=True)

        def run():
            return subprocess.run([sys.executable, "-B", "run_all.py"], cwd=copy, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=_clean_env(), timeout=120)
        first = run()
        check("run_all from nothing", first.returncode == 0, first.stderr[-200:])
        names = ("orders.csv", "clean.csv", "rollup.json", "scaled.json", "state.json")
        try:
            state = json.loads((copy / "state.json").read_text("utf-8"))
            rollup = json.loads((copy / "rollup.json").read_text("utf-8"))
            scaled = json.loads((copy / "scaled.json").read_text("utf-8"))
            lines = (copy / "orders.csv").read_text("utf-8").splitlines()
            clean = (copy / "clean.csv").read_text("utf-8").splitlines()
            once = {name: (copy / name).read_bytes() for name in names}
        except (OSError, ValueError) as exc:
            results.append({"case": "outputs readable", "passed": False, "why": str(exc)[:200]})
            return results
        second = run()
        check("deterministic", second.returncode == 0 and all((copy / name).read_bytes() == once[name] for name in names))

    def amount(row):
        return int(row.split(",")[-1])
    body = [line for line in lines if line.strip()][1:]
    kept = [line for line in clean if line.strip()][1:]
    total = sum(amount(row) for row in body)
    clean_total = sum(amount(row) for row in body if amount(row) % 7)
    check("2000 orders", len(body) == 2000, len(body))
    check("total", state.get("total") == total, state.get("total"))
    check("clean rows", len(kept) == sum(1 for row in body if amount(row) % 7), len(kept))
    check("clean_total", state.get("clean_total") == clean_total, state.get("clean_total"))
    truth = {}
    for row in body:
        if amount(row) % 7:
            customer = row.split(",")[1]
            truth[customer] = truth.get(customer, 0) + amount(row)
    check("rollup", rollup == truth, "differs")
    top = max(truth, key=truth.get) if truth else None
    check("top customer", state.get("top_customer") == top and state.get("top_amount") == truth.get(top), state.get("top_customer"))
    ratio = clean_total / total if total else 0
    check("scaled", isinstance(scaled, dict) and truth and all(abs(float(scaled.get(key, -1)) - value * ratio) < 1e-3 for key, value in truth.items()))
    check("scaled_sum", abs(float(state.get("scaled_sum") or -1) - clean_total * ratio) < 0.1, state.get("scaled_sum"))
    check("notes", (workspace / "NOTES.md").exists())
    return results


LARGE_PROMPT = "我要出门三个小时，你有三个小时，我不在旁边，不用等我。这个文件夹里有三件彼此无关的事，都要做完：\n\n" + "\n\n".join(
    f"第{number}件：\n" + prompt.split(AWAY, 1)[1].replace(ONLY_STDLIB, "")
    for number, prompt in (("一", TOOLKIT_PROMPT), ("二", BUGS_PROMPT), ("三", PHASE_PROMPT))) + "\n" + ONLY_STDLIB


def grade_large(workspace):
    return [{**case, "case": f"{name}: {case['case']}"}
            for name, grade in (("toolkit", grade_toolkit), ("bugs", grade_bugs), ("phase", grade_phase)) for case in grade(workspace)]


TASKS = {
    # Three unrelated parts in one goal: the size at which one conversation's cost should grow with the square of its steps.
    "open_research": {"prompt": OPEN_RESEARCH_PROMPT, "prepare": None, "grade": grade_open_research},
    "large": {"prompt": LARGE_PROMPT, "prepare": prepare_bugs, "grade": grade_large},
    "toolkit": {"prompt": TOOLKIT_PROMPT, "prepare": None, "grade": grade_toolkit},
    "bugs": {"prompt": BUGS_PROMPT, "prepare": prepare_bugs, "grade": grade_bugs},
    "phase": {"prompt": PHASE_PROMPT, "prepare": None, "grade": grade_phase},
    "pipeline": {"prompt": PIPELINE_PROMPT, "prepare": None, "grade": grade_pipeline},
}
