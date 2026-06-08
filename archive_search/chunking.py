from __future__ import annotations


DEFAULT_CHUNK_CHARS = 2000
DEFAULT_CHUNK_OVERLAP = 200
MIN_CHUNK_CHARS = 300


def chunk_text(
    text: str | None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []

    size = max(int(chunk_chars), MIN_CHUNK_CHARS)
    overlap = min(max(int(overlap_chars), 0), size // 2)

    chunks = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = _best_boundary(normalized, start, end)
            if boundary > start:
                end = boundary

        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(normalized):
            break

        next_start = max(end - overlap, start + 1)
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.rstrip() for line in str(text).replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def _best_boundary(text: str, start: int, end: int) -> int:
    lower_bound = start + ((end - start) // 2)
    for marker, offset in (("\n\n", 2), (". ", 2), ("\n", 1), (" ", 1)):
        boundary = text.rfind(marker, lower_bound, end)
        if boundary > start:
            return boundary + offset
    return end
