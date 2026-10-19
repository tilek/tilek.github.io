#!/usr/bin/env python3
"""Validate generated Jekyll HTML without network access or third-party packages.

Run after the production build (and CSS processing):
    python3 scripts/validate_site.py _site

Checks local href/src/srcset targets throughout the generated site, plus the
homepage/podcasts navigation and existing podcast embeds. External services,
fragment IDs, inline JavaScript, and CSS/JavaScript-generated URLs are deliberately
outside this deterministic build check.
"""

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import sys
from urllib.parse import unquote, urljoin, urlsplit


INTERNAL_HOSTS = {"tilek.net", "www.tilek.net", "tilek.github.io"}
EXPECTED_PODCAST_IDS = {
    "dqBzdWr4ofE",
    "4YWAZEcYeMQ",
    "14NrpEi32ag",
    "JmA429gSsBQ",
    "jhcgrvTZW-w",
    "3eK_f76U8lY",
    "Ki6ao-woyl4",
}
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
ASCII_WHITESPACE = " \t\n\r\f"


def srcset_urls(value):
    """Read URL tokens, allowing commas inside URLs (notably data: images)."""
    position = 0
    while position < len(value):
        while position < len(value) and value[position] in ASCII_WHITESPACE + ",":
            position += 1
        start = position
        while position < len(value) and value[position] not in ASCII_WHITESPACE:
            position += 1
        url = value[start:position]
        if not url:
            break
        yield url.rstrip(",")
        if url.endswith(","):
            continue
        # Descriptors end at the next comma outside parentheses.
        parentheses = 0
        while position < len(value):
            char = value[position]
            position += 1
            if char == "," and not parentheses:
                break
            if char == "(":
                parentheses += 1
            elif char == ")" and parentheses:
                parentheses -= 1


@dataclass
class Reference:
    tag: str
    attribute: str
    url: str
    line: int
    navigation: bool


class SiteHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references = []
        self.base_href = None
        self.nav_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "nav":
            self.nav_depth += 1
        for attribute, value in attrs:
            if value is None:
                continue
            if tag == "base" and attribute == "href":
                if self.base_href is None:
                    self.base_href = value
                continue
            if attribute not in {"href", "src", "srcset"}:
                continue
            urls = srcset_urls(value) if attribute == "srcset" else [value]
            for url in urls:
                self.references.append(
                    Reference(tag, attribute, url, self.getpos()[0], self.nav_depth > 0)
                )

    def handle_endtag(self, tag):
        if tag == "nav":
            self.nav_depth = max(0, self.nav_depth - 1)


def local_target(site_dir, base_url, raw_url):
    """Return a local file target, or None for nonlocal/dynamic references.

    Directory routes resolve to index.html, including links without a trailing
    slash. Query strings and fragments do not change the requested file.
    """
    raw_url = raw_url.strip()
    if not raw_url or raw_url.startswith("#"):
        return None
    parsed = urlsplit(urljoin(base_url, raw_url))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in INTERNAL_HOSTS:
        return None
    target = (site_dir / unquote(parsed.path).lstrip("/")).resolve()
    if not target.is_relative_to(site_dir):
        raise ValueError("URL resolves outside the generated site")
    if parsed.path.endswith("/") or target.is_dir():
        target = target / "index.html"
    return target


@dataclass
class ValidationResult:
    errors: list
    html_files: int
    local_references: int


def validate_site(site_dir):
    site_dir = Path(site_dir).resolve()
    errors = []
    documents = {}
    local_references = 0
    if not site_dir.is_dir():
        return ValidationResult([f"Generated site directory does not exist: {site_dir}"], 0, 0)

    for path in sorted(site_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            continue
        relative = path.relative_to(site_dir).as_posix()
        parser = SiteHTMLParser()
        try:
            parser.feed(path.read_text(encoding="utf-8"))
            parser.close()
        except (OSError, UnicodeError) as error:
            errors.append(f"{relative}: cannot read HTML: {error}")
            continue
        documents[relative] = parser
        base_url = "https://tilek.net/" + relative
        if parser.base_href is not None:
            base_url = urljoin(base_url, parser.base_href)
        parser.navigation_targets = set()
        for reference in parser.references:
            location = f"{relative}:{reference.line}: {reference.tag}[{reference.attribute}]"
            try:
                target = local_target(site_dir, base_url, reference.url)
            except ValueError as error:
                errors.append(f"{location} {reference.url!r}: {error}")
                continue
            if target is None:
                continue
            local_references += 1
            if reference.navigation and reference.tag == "a" and reference.attribute == "href":
                parser.navigation_targets.add(target)
            if not target.is_file():
                errors.append(
                    f"{location} {reference.url!r}: missing {target.relative_to(site_dir)}"
                )

    required_pages = ("index.html", "podcasts/index.html")
    for relative in required_pages:
        if relative not in documents:
            errors.append(f"Required page missing: {relative}")
            continue
        for destination in required_pages:
            if site_dir / destination not in documents[relative].navigation_targets:
                errors.append(f"{relative}: navigation must link to {destination}")

    podcasts = documents.get("podcasts/index.html")
    if podcasts is not None:
        embed_ids = set()
        for reference in podcasts.references:
            if reference.tag != "iframe" or reference.attribute != "src":
                continue
            try:
                parsed = urlsplit(reference.url)
                if parsed.hostname in YOUTUBE_HOSTS and parsed.path.startswith("/embed/"):
                    embed_ids.add(parsed.path.removeprefix("/embed/").rstrip("/"))
            except ValueError:
                continue
        missing_ids = EXPECTED_PODCAST_IDS - embed_ids
        if missing_ids:
            errors.append("podcasts/index.html: missing podcast embeds: " + ", ".join(sorted(missing_ids)))

    return ValidationResult(errors, len(documents), local_references)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_dir", nargs="?", default="_site", help="Generated site directory (default: _site)")
    args = parser.parse_args(argv)
    result = validate_site(args.site_dir)
    if result.errors:
        print(f"Site validation failed ({len(result.errors)} errors):", file=sys.stderr)
        for error in result.errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        f"Site validation passed: {result.html_files} HTML files, "
        f"{result.local_references} local references, navigation and "
        f"{len(EXPECTED_PODCAST_IDS)} required podcast embeds."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
