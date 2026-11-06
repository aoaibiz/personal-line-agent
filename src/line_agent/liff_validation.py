"""Validate rich message structure before preparation, as well as in the worker."""

import json
from urllib.parse import urlsplit


def validate_messages(messages):
    if len(json.dumps(messages, ensure_ascii=False).encode()) > 50000:
        raise ValueError("liff_payload_too_large")
    for message in messages:
        for key, limit in (
            ("text", 5000),
            ("altText", 400),
            ("originalContentUrl", 2000),
            ("previewImageUrl", 2000),
        ):
            value = message.get(key)
            if (
                isinstance(value, str)
                and len(value.encode("utf-16-le", errors="surrogatepass")) // 2 > limit
            ):
                raise ValueError("liff_text_limit")
        if message["type"] not in {"flex", "template"}:
            continue
        root = message.get("contents" if message["type"] == "flex" else "template")
        roots = (
            {"bubble", "carousel"}
            if message["type"] == "flex"
            else {"buttons", "confirm", "carousel", "image_carousel"}
        )
        if not isinstance(root, dict) or root.get("type") not in roots:
            raise ValueError("liff_root_invalid")
        nodes = 0

        def visit(value, depth, key=""):
            nonlocal nodes
            nodes += 1
            if nodes > 2000 or depth > 12:
                raise ValueError("liff_structure_limit")
            if key.lower().endswith(("url", "uri")):
                if not isinstance(value, str):
                    raise ValueError("liff_https_uri_required")
                parsed = urlsplit(value)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                ):
                    raise ValueError("liff_https_uri_required")
            if (
                isinstance(value, str)
                and len(value.encode("utf-16-le", errors="surrogatepass")) // 2 > 10000
            ):
                raise ValueError("liff_text_limit")
            if isinstance(value, dict):
                for k, v in value.items():
                    visit(v, depth + 1, k)
            elif isinstance(value, list):
                for v in value:
                    visit(v, depth + 1)

        visit(root, 0)
