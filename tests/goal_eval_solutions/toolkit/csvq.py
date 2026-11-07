import csv
import io
import re

SQL = re.compile(r"SELECT\s+(?P<cols>\*|\w+(?:\s*,\s*\w+)*)(?:\s+WHERE\s+(?P<where>.+?))?"
                 r"(?:\s+ORDER\s+BY\s+(?P<order>\w+)(?P<desc>\s+DESC)?)?(?:\s+LIMIT\s+(?P<limit>\d+))?\s*$")
COND = re.compile(r"\s*(\w+)\s*(<=|>=|!=|=|<|>)\s*(.+?)\s*$")


def _key(value):
    try:
        return (0, float(value), "")
    except ValueError:
        return (1, 0.0, value)


def _test(left, op, right):
    a, b = _key(left), _key(right)
    return {"=": a == b, "!=": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]


def query(text, sql):
    found = SQL.match(sql.strip())
    if not found:
        raise ValueError(sql)
    rows = list(csv.DictReader(io.StringIO(text)))
    if found["where"]:
        for part in re.split(r"\s+AND\s+", found["where"]):
            cond = COND.match(part)
            if not cond:
                raise ValueError(part)
            rows = [row for row in rows if _test(row[cond[1]], cond[2], cond[3])]
    if found["order"]:
        rows.sort(key=lambda row: _key(row[found["order"]]), reverse=bool(found["desc"]))
    if found["limit"]:
        rows = rows[:int(found["limit"])]
    if found["cols"] != "*":
        cols = [col.strip() for col in found["cols"].split(",")]
        rows = [{col: row[col] for col in cols} for row in rows]
    return rows
