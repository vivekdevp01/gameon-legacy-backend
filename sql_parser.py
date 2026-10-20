"""Parse INSERT statements for a given table out of a raw MySQL dump.
Handles multi-row VALUES, quoted strings with backslash and doubled-quote
escaping, NULLs and negative/large numbers.
"""
import re

_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "0": "\0",
    "b": "\b", "Z": "\x1a", "\\": "\\", "'": "'", '"': '"',
}


def _parse_row(text, i):
    """Parse a single (v1, v2, ...) tuple beginning at text[i] == '('.
    Returns (values_list, index_after_closing_paren)."""
    n = len(text)
    i += 1  # skip '('
    values = []
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        c = text[i]
        if c == ")":
            return values, i + 1
        if c == ",":
            i += 1
            continue
        if c == "'":
            i += 1
            buf = []
            while i < n:
                ch = text[i]
                if ch == "\\":
                    nxt = text[i + 1] if i + 1 < n else ""
                    buf.append(_ESCAPES.get(nxt, nxt))
                    i += 2
                    continue
                if ch == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(ch)
                i += 1
            values.append("".join(buf))
        else:
            start = i
            while i < n and text[i] not in ",)":
                i += 1
            token = text[start:i].strip()
            if token.upper() == "NULL" or token == "":
                values.append(None)
            else:
                values.append(token)
    return values, i


def _coerce(token):
    if token is None:
        return None
    if isinstance(token, str):
        t = token.strip()
        if re.fullmatch(r"-?\d+", t):
            try:
                return int(t)
            except ValueError:
                return t
        if re.fullmatch(r"-?\d+\.\d+", t):
            try:
                return float(t)
            except ValueError:
                return t
    return token


def detect_table(text):
    """Return the first es_* table name found in an INSERT header."""
    m = re.search(r"INSERT\s+(?:IGNORE\s+)?INTO\s+`?(es_\w+)`?", text, re.IGNORECASE)
    return m.group(1).lower() if m else None


def iter_inserts(text, table_name, string_cols=frozenset()):
    """Yield one dict per row for every INSERT into `table_name` in `text`.

    Streaming: rows come out one at a time so the caller can insert them in
    batches and drop them. Materialising a big dump as a list instead costs
    several hundred MB (an 86 MB dump becomes ~640 MB of dicts) — enough to
    OOM a small container. Prefer this for anything user-uploaded.
    """
    header_re = re.compile(
        r"INSERT\s+(?:IGNORE\s+)?INTO\s+`?" + re.escape(table_name) + r"`?\s*\(([^)]*)\)\s*VALUES",
        re.IGNORECASE,
    )
    for m in header_re.finditer(text):
        cols = [c.strip().strip("`").strip() for c in m.group(1).split(",")]
        i = m.end()
        n = len(text)
        while i < n:
            while i < n and text[i] in " \t\r\n,":
                i += 1
            if i >= n or text[i] == ";":
                break
            if text[i] != "(":
                break
            values, i = _parse_row(text, i)
            row = {}
            for idx, col in enumerate(cols):
                val = values[idx] if idx < len(values) else None
                if isinstance(val, str) and col in string_cols:
                    row[col] = val
                else:
                    row[col] = _coerce(val)
            yield row


def parse_inserts(text, table_name, string_cols=frozenset()):
    """List form of iter_inserts(). Convenient for tests and small files."""
    return list(iter_inserts(text, table_name, string_cols))


CUSTOMER_STRING_COLS = {
    "rfid", "phone", "name", "dob", "guid", "email", "payment_mode",
    "status", "is_free_time", "game_id", "user", "location", "center_key",
    "updated_by", "pos_name", "center_code", "last_activity", "created_at",
    "updated_at_dt", "updated_at",
}


BILLING_STRING_COLS = {
    "guid", "rfid", "name", "email", "phone", "is_deleted", "is_refund",
    "refund_by", "card_type", "center_key", "updated_by",
    "play_by_balance_package_points", "pos_name", "center_code",
    "created_at", "updated_at_dt", "updated_at", "refund_at",
    "log_device_ip", "log_game",
}

PAYMENT_STRING_COLS = {
    "rfid", "guid", "name", "email", "phone", "is_deleted", "payment_mode",
    "user", "wa_response", "center_key", "updated_by", "discount_comment",
    "pos_name", "bonus_comment", "payment_for", "center_code", "created_at",
    "updated_at_dt", "updated_at", "gst_amount",
}
