"""Bounded official public search. Public emid is never a canonical Square MID."""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class NuxtScript(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.parts = []
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NUXT_DATA__":
            self.inside = True
            self.count += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)


def parse_search(html, query):
    parser = NuxtScript()
    parser.feed(html)
    if parser.count != 1:
        raise ValueError("public_search_schema_changed")
    nodes = json.loads("".join(parser.parts))
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 20000:
        raise ValueError("public_search_schema_changed")

    def ref(index):
        seen = set()
        for _ in range(8):
            if index == -1:
                return None
            if type(index) is not int or not 0 <= index < len(nodes) or index in seen:
                raise ValueError("public_search_schema_changed")
            seen.add(index)
            value = nodes[index]
            if (
                isinstance(value, list)
                and len(value) == 2
                and value[0] in ("Reactive", "ShallowReactive", "Ref", "ShallowRef")
            ):
                index = value[1]
                continue
            return value
        raise ValueError("public_search_schema_changed")

    def field(obj, name):
        if not isinstance(obj, dict) or name not in obj:
            raise ValueError("public_search_schema_changed")
        return ref(obj[name])

    search = field(field(ref(0), "pinia"), "search")
    if (
        field(search, "keyword") != query
        or field(search, "isLoading") is not False
        or field(search, "isSubmittedSearch") is not True
    ):
        raise ValueError("public_search_incomplete")
    rows = field(search, "searchedSquares")
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("public_search_schema_changed")
    items = []
    for index in rows:
        row = ref(index)
        square = field(row, "square")
        emid = field(square, "emid")
        name = field(square, "name")
        desc = field(square, "desc")
        count = field(row, "memberCount")
        if (
            not isinstance(emid, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", emid)
            or not isinstance(name, str)
            or len(name) > 1000
            or not isinstance(desc, str)
            or len(desc) > 20000
            or type(count) is not int
            or count < 0
        ):
            raise ValueError("public_search_schema_changed")
        items.append(
            {
                "public_id": emid,
                "name": name,
                "description": desc,
                "member_count": count,
                "url": "https://openchat.line.me/jp/cover/" + emid,
            }
        )
    return {"items": items, "has_more": bool(field(search, "continuationToken"))}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("public_search_redirect_rejected")


def search_public(service, query, cursor=None):
    if service.paused():
        raise ValueError("agent_paused")
    if not isinstance(query, str) or not 1 <= len(query) <= 100 or cursor:
        raise ValueError("public_search_first_page_only")
    url = "https://openchat.line.me/jp/search?" + urllib.parse.urlencode({"keyword": query})

    def failure(stage, reason, http_status=None):
        return {
            "ok": False,
            "stage": stage,
            "reason": reason,
            "http_status": http_status,
            "items": None,
        }

    try:
        with urllib.request.build_opener(NoRedirect).open(url, timeout=20) as response:
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                return failure("public_search_fetch", "response_too_large")
            if response.status != 200:
                return failure("public_search_fetch", "upstream_http_error", response.status)
    except urllib.error.HTTPError as error:
        return failure("public_search_fetch", "upstream_http_error", error.code)
    except (TimeoutError, urllib.error.URLError, OSError):
        return failure("public_search_fetch", "transport_failed")
    except ValueError:
        return failure("public_search_fetch", "redirect_rejected")
    try:
        result = parse_search(data.decode("utf-8"), query)
    except UnicodeError:
        return failure("public_search_decode", "encoding_invalid")
    except json.JSONDecodeError:
        return failure("public_search_decode", "json_invalid")
    except ValueError as error:
        reason = str(error)
        return failure(
            "public_search_decode",
            reason
            if reason in {"public_search_schema_changed", "public_search_incomplete"}
            else "public_search_schema_changed",
        )
    except (KeyError, TypeError, IndexError, RecursionError):
        return failure("public_search_decode", "public_search_schema_changed")
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    if not owner:
        raise ValueError("owner_identity_unavailable")
    for item in result["items"]:
        service.store.put(
            "openchat_public_reference",
            item["public_id"],
            {"owner": owner, "expires_at": time.time() + 900},
        )
    return {
        "ok": True,
        **result,
        "source": "official_public_search",
        "search_url": url,
        "identity_namespace": "public_emid",
        "next_cursor": None,
        "first_page_only": True,
        "untrusted_content": True,
    }


class CoverLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tickets = set()

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        match = re.fullmatch(r"line://ti/g2/([A-Za-z0-9_-]{1,100})(?:\?[^#]*)?", href)
        if match:
            self.tickets.add(match[1])


def parse_invitation(html):
    links, script = CoverLinks(), NuxtScript()
    links.feed(html)
    script.feed(html)
    if len(links.tickets) != 1 or script.count != 1:
        raise ValueError("public_cover_schema_changed")
    nodes = json.loads("".join(script.parts))
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 20000:
        raise ValueError("public_cover_schema_changed")
    tickets = set()
    for node in nodes:
        if isinstance(node, dict) and "invitationTicket" in node:
            index = node["invitationTicket"]
            if type(index) is int and 0 <= index < len(nodes):
                value = nodes[index]
                if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
                    tickets.add(value)
    if tickets != links.tickets:
        raise ValueError("public_cover_schema_changed")
    return next(iter(tickets))


def resolve_ticket(public_id):
    if not isinstance(public_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", public_id):
        raise ValueError("invalid_public_id")
    try:
        with urllib.request.build_opener(NoRedirect).open(
            "https://openchat.line.me/jp/cover/" + public_id, timeout=20
        ) as response:
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                return {"ok": False, "stage": "public_cover_fetch", "reason": "response_too_large"}
            if response.status != 200:
                return {"ok": False, "stage": "public_cover_fetch", "reason": "upstream_http_error", "http_status": response.status}
    except urllib.error.HTTPError as error:
        return {"ok": False, "stage": "public_cover_fetch", "reason": "upstream_http_error", "http_status": error.code}
    except (TimeoutError, urllib.error.URLError, OSError):
        return {"ok": False, "stage": "public_cover_fetch", "reason": "transport_failed"}
    except ValueError:
        return {"ok": False, "stage": "public_cover_fetch", "reason": "redirect_rejected"}
    try:
        return {"ok": True, "ticket": parse_invitation(data.decode("utf-8"))}
    except (ValueError, TypeError, KeyError, RecursionError):
        return {"ok": False, "stage": "public_cover_decode", "reason": "public_cover_schema_changed"}
