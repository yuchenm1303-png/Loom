"""Parse Codex-style text patches into the existing atomic Loom edit plan.

Matching is deliberately exact and ordered. Ambiguous contexts fail rather than
guessing which occurrence should be changed. No shell or Git process is involved.
"""
from __future__ import annotations


def parse_text_patch(context, source: str):
    if not isinstance(source, str) or len(source) > 2_000_000:
        raise ValueError("patch must be a string of at most 2,000,000 characters")
    lines = source.splitlines()
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("patch requires Begin Patch and End Patch markers")
    changes = []
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        index += 1
        if header.startswith("*** Add File: "):
            path = header[len("*** Add File: "):]
            body = []
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                line = lines[index]
                if not line.startswith("+"):
                    raise ValueError("added file lines must start with +")
                body.append(line[1:])
                index += 1
            changes.append({"action": "add", "path": path, "content": "\n".join(body) + ("\n" if body else "")})
        elif header.startswith("*** Delete File: "):
            changes.append({"action": "delete", "path": header[len("*** Delete File: "):]})
        elif header.startswith("*** Update File: "):
            path = header[len("*** Update File: "):]
            target = context.resolve_workspace_path(path)
            if target.stat().st_size > 1_000_000:
                raise ValueError("patch target exceeds editable size limit")
            before = target.read_text(encoding="utf-8")
            content = before.splitlines()
            destination = None
            if lines[index].startswith("*** Move to: "):
                destination = lines[index][len("*** Move to: "):]
                index += 1
            cursor = 0
            while index < len(lines) - 1 and not lines[index].startswith("*** "):
                anchor = ""
                if lines[index].startswith("@@"):
                    anchor = lines[index][2:].strip()
                    index += 1
                if anchor:
                    found = [i for i in range(cursor, len(content)) if content[i] == anchor]
                    if len(found) != 1:
                        raise ValueError(f"patch anchor missing or ambiguous: {anchor}")
                    cursor = found[0] + 1
                old, new = [], []
                while index < len(lines) - 1 and not lines[index].startswith(("@@", "*** ")):
                    line = lines[index]
                    if not line or line[0] not in " +-":
                        raise ValueError("hunk lines require a context, +, or - prefix")
                    if line[0] in " -":
                        old.append(line[1:])
                    if line[0] in " +":
                        new.append(line[1:])
                    index += 1
                eof = lines[index] == "*** End of File"
                if eof:
                    index += 1
                if not old and not new:
                    raise ValueError("empty update hunk")
                if old:
                    found = [i for i in range(cursor, len(content) - len(old) + 1)
                        if content[i:i + len(old)] == old and (not eof or i + len(old) == len(content))]
                    if len(found) != 1:
                        raise ValueError(f"patch context missing or ambiguous in {path}")
                    at = found[0]
                else:
                    at = len(content) if eof else cursor
                content[at:at + len(old)] = new
                cursor = at + len(new)
            after = "\n".join(content) + ("\n" if content else "")
            changes.append({"action": "update", "path": path, "content": after, "expected_text": before})
            if destination:
                changes.append({"action": "move", "path": path, "move_to": destination})
        else:
            raise ValueError(f"unsupported patch header: {header}")
    if not changes:
        raise ValueError("patch contains no changes")
    return changes
