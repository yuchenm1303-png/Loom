def table(rows):
    keys = list(rows[0])
    cells = [keys] + [[str(row[key]) for key in keys] for row in rows]
    widths = [max(len(line[at]) for line in cells) for at in range(len(keys))]
    return "\n".join(" | ".join(cell.ljust(width) for cell, width in zip(line, widths)) for line in cells)


def totals(rows, key):
    return sum(row.get(key, 0) for row in rows)
