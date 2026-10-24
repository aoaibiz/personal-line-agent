"""Count definitions shared by live summaries and saved contact views."""

from .identifiers import id_kind


def nonnegative_integer(value):
    # Thrift i64 fields arrive as decimal strings, never coerce floats/bools.
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ValueError("件数・設定値の形式が不正です")


def contact_counts(contacts):
    result = dict(
        all_contacts=len(contacts),
        visible_friends=0,
        hidden_friends=0,
        official_accounts=0,
        hidden_official_accounts=0,
        other_status=0,
    )
    for row in contacts:
        if (
            type(row.get("isOfficial")) is not bool
            or type(row.get("isHidden")) is not bool
            or type(row.get("contactStatus")) is not int
        ):
            raise ValueError("件数の区分に必要な情報がありません。友達を再同期してください")
        if row["contactStatus"] != 1:  # ContactStatus.FRIEND
            result["other_status"] += 1
        elif row["isOfficial"]:
            result["official_accounts"] += 1
            result["hidden_official_accounts"] += int(row["isHidden"])
        else:
            result["hidden_friends" if row["isHidden"] else "visible_friends"] += 1
    return result


def group_counts(raw):
    if not isinstance(raw, dict):
        raise ValueError("グループ一覧の形式が不正です")
    groups = []
    for key in ("memberChatMids", "invitedChatMids"):
        ids = raw.get(key)
        if not isinstance(ids, list):
            raise ValueError("グループ一覧に欠落があります")
        kinds = [id_kind(mid) for mid in set(ids)]
        if any(kind not in ("c", "r") for kind in kinds):
            raise ValueError("グループ以外のIDです")
        groups.append(kinds)
    return {
        "joined": groups[0].count("c"),
        "invited": groups[1].count("c"),
        "joined_rooms": groups[0].count("r"),
    }
