"""Формирование и разбиение сообщений для Discord."""
import re


def split_discord_message(text, limit=1800):
    if not text:
        return []
    fence = re.compile(r"(?ms)^```[^\n]*\n.*?^```(?=\n|$)")

    def split_plain(block, ml):
        if not block:
            return []
        parts, cur = [], ""
        for line in block.splitlines():
            line = line.rstrip()
            if len(line) > ml:
                if cur:
                    parts.append(cur.rstrip())
                    cur = ""
                for p in range(0, len(line), ml):
                    parts.append(line[p:p + ml])
                continue
            cand = line if not cur else cur + "\n" + line
            if len(cand) <= ml:
                cur = cand
            else:
                if cur:
                    parts.append(cur.rstrip())
                cur = line
        if cur.strip():
            parts.append(cur.rstrip())
        return parts

    def split_code(block, ml):
        lines = block.splitlines()
        if len(lines) < 2:
            return [block] if len(block) <= ml else []
        opening, closing, body = lines[0], lines[-1], lines[1:-1]
        hdr, ds = [], 0
        if body:
            hdr.append(body[0])
            ds = 1
        if len(body) > 1 and body[1].startswith("+-") and body[1].endswith("-+"):
            hdr.append(body[1])
            ds = 2
        reserved = len(opening) + 1 + len(closing) + 2
        bl = max(100, ml - reserved)
        chunks, cur = [], list(hdr)
        cl = sum(len(x) for x in cur) + max(0, len(cur) - 1)

        def flush():
            if cur:
                chunks.append(opening + "\n" + "\n".join(cur) + "\n" + closing)

        for line in body[ds:]:
            line = line.rstrip()
            if len(line) > bl:
                if cur:
                    flush()
                    cur[:] = hdr
                    cl = sum(len(v) for v in cur) + max(0, len(cur) - 1)
                chunks.append(opening + "\n" + line + "\n" + closing)
                continue
            extra = len(line) + (1 if cur else 0)
            if cur and cl + extra > bl:
                flush()
                cur[:] = hdr
                cl = sum(len(v) for v in cur) + max(0, len(cur) - 1)
            cur.append(line)
            cl += len(line) + (1 if len(cur) > 1 else 0)
        if cur:
            flush()
        return chunks

    parts, pos = [], 0
    for m in fence.finditer(text):
        parts.extend(split_plain(text[pos:m.start()], limit))
        parts.extend(split_code(m.group(0), limit))
        pos = m.end()
    parts.extend(split_plain(text[pos:], limit))
    return [p for p in parts if p and p.strip()]
