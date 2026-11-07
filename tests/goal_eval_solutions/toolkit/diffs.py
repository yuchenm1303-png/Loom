import re


def _ops(a, b):
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) - 1, -1, -1):
        for j in range(len(b) - 1, -1, -1):
            table[i][j] = table[i + 1][j + 1] + 1 if a[i] == b[j] else max(table[i + 1][j], table[i][j + 1])
    i, j, ops = 0, 0, []
    while i < len(a) or j < len(b):
        if i < len(a) and j < len(b) and a[i] == b[j]:
            ops.append((" ", a[i]))
            i, j = i + 1, j + 1
        elif i < len(a) and (j == len(b) or table[i + 1][j] >= table[i][j + 1]):
            ops.append(("-", a[i]))
            i += 1
        else:
            ops.append(("+", b[j]))
            j += 1
    return ops


def _range(start, count):
    if count == 1:
        return str(start + 1)
    return f"{start + 1 if count else start},{count}"


def unified(a, b, context=3):
    ops = _ops(a, b)
    changed = [at for at, (kind, _) in enumerate(ops) if kind != " "]
    groups = []
    for at in changed:
        if groups and at - groups[-1][1] <= 2 * context:
            groups[-1][1] = at
        else:
            groups.append([at, at])
    out = []
    for first, last in groups:
        start, end = max(0, first - context), min(len(ops), last + context + 1)
        old = sum(1 for kind, _ in ops[:start] if kind != "+")
        new = sum(1 for kind, _ in ops[:start] if kind != "-")
        part = ops[start:end]
        out.append(f"@@ -{_range(old, sum(kind != '+' for kind, _ in part))} +{_range(new, sum(kind != '-' for kind, _ in part))} @@")
        out += [kind + line for kind, line in part]
    return "\n".join(out)


def apply(a, patch):
    out, at = [], 0
    for line in patch.splitlines():
        found = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
        if found:
            start = int(found[1]) - (0 if found[2] == "0" else 1)
            if start < at:
                raise ValueError(line)
            out += a[at:start]
            at = start
        elif line[:1] == "+":
            out.append(line[1:])
        else:
            if at >= len(a) or a[at] != line[1:]:
                raise ValueError(line)
            if line[:1] == " ":
                out.append(a[at])
            at += 1
    return out + a[at:]
