"""LINE identifiers are opaque and case-sensitive; never normalize their payload."""

import re

MID_PATTERN = r"^(?:[ucr][0-9a-f]{32}|[UCR][A-Za-z0-9_-]{43})$"


def id_kind(value):
    if not isinstance(value, str) or re.fullmatch(MID_PATTERN, value) is None:
        raise ValueError("LINE IDの形式が不正です")
    return value[0].lower()


def is_user(value):
    return id_kind(value) == "u"


def is_group(value):
    return id_kind(value) == "c"
