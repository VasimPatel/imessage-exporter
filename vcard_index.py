"""Utility for parsing vCard files and indexing contacts.

This module provides a lightweight, dependency-free vCard parser that supports
common vCard 3.0 and 4.0 properties (FN, N, TEL, EMAIL) and builds searchable
indices for phone numbers and email addresses.

Doctest-style examples
----------------------

Line folding is handled per RFC rules where a line beginning with a space or	tab continues the previous line. The folded content is concatenated without the
leading whitespace:

    >>> from vcard_index import VCardIndex, parse_vcards_from_text
    >>> text = \"\"\"BEGIN:VCARD
    ... VERSION:3.0
    ... FN:Jane
    ...  Doe
    ... TEL;TYPE=CELL:+1 555-123-4567
    ... END:VCARD
    ... \"\"\"
    >>> contacts = parse_vcards_from_text(text)
    >>> contacts[0].full_name
    'JaneDoe'

Phone numbers are normalized by stripping formatting while preserving a leading
"+". A fallback key of the last 10 digits is also indexed when available:

    >>> idx = VCardIndex(contacts)
    >>> idx.get_by_phone("(555) 123-4567").full_name
    'JaneDoe'

Emails are normalized by lowercasing and trimming whitespace:

    >>> text = \"\"\"BEGIN:VCARD
    ... VERSION:3.0
    ... FN:Test User
    ... EMAIL;TYPE=HOME:Test@Example.Com
    ... END:VCARD
    ... \"\"\"
    >>> VCardIndex(parse_vcards_from_text(text)).get_by_email(\" test@example.com \").full_name
    'Test User'
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
import argparse
import os
import re


PHONE_DIGITS_RE = re.compile(r"[^0-9]")


@dataclass
class Contact:
    """Represents a parsed contact from a vCard file.

    Attributes:
        full_name: Resolved display name (FN if available, otherwise composed
            from N components).
        phones: List of phone numbers exactly as they appear in the vCard.
        emails: List of email addresses exactly as they appear in the vCard.
        raw_fields: Optional mapping of property names to their collected raw
            values; useful for debugging.
    """

    full_name: str
    phones: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    raw_fields: Dict[str, Any] = field(default_factory=dict)


class VCardIndex:
    """In-memory index for contacts loaded from a vCard (.vcf) file."""

    def __init__(self, contacts: Iterable[Contact]):
        self.contacts: List[Contact] = list(contacts)
        self.phone_index: Dict[str, List[Contact]] = {}
        self.email_index: Dict[str, List[Contact]] = {}
        self.name_index: Dict[str, List[Contact]] = {}
        self._build_indices()

    @classmethod
    def from_file(cls, path: str) -> "VCardIndex":
        """Load contacts from a vCard file and build the index."""

        return cls(parse_vcards(path))

    def _build_indices(self) -> None:
        for contact in self.contacts:
            name_key = normalize_name(contact.full_name)
            if name_key:
                self.name_index.setdefault(name_key, []).append(contact)
            for phone in contact.phones:
                for key in normalize_phone_keys(phone):
                    if not key:
                        continue
                    self.phone_index.setdefault(key, []).append(contact)
            for email in contact.emails:
                key = normalize_email(email)
                if not key:
                    continue
                self.email_index.setdefault(key, []).append(contact)

    def get_by_phone(self, phone: str) -> Optional[Contact]:
        """Return the first contact matching a normalized phone number."""

        matches = self.get_all_matches_by_phone(phone)
        return matches[0] if matches else None

    def get_all_matches_by_phone(self, phone: str) -> List[Contact]:
        """Return all contacts matching a normalized phone number."""

        results: List[Contact] = []
        for key in normalize_phone_keys(phone):
            if not key:
                continue
            results.extend(self.phone_index.get(key, []))
        # Preserve original ordering while removing duplicates
        seen: set[int] = set()
        unique_results: List[Contact] = []
        for contact in results:
            if id(contact) not in seen:
                seen.add(id(contact))
                unique_results.append(contact)
        return unique_results

    def get_by_email(self, email: str) -> Optional[Contact]:
        """Return the first contact matching a normalized email."""

        key = normalize_email(email)
        if not key:
            return None
        matches = self.email_index.get(key, [])
        return matches[0] if matches else None

    def get_by_name(self, name: str) -> Optional[Contact]:
        """Return the first contact matching a normalized display name."""

        key = normalize_name(name)
        if not key:
            return None
        matches = self.name_index.get(key, [])
        return matches[0] if matches else None


# Parsing helpers

def normalize_email(email: str) -> str:
    """Normalize an email address for indexing."""

    return email.strip().lower()


def normalize_name(name: str) -> str:
    """Normalize a display name for lookup and identity matching."""

    return " ".join(name.strip().casefold().split())


def normalize_phone_keys(phone: str) -> Tuple[str, ...]:
    """Return canonical and fallback phone keys for indexing.

    Canonical key preserves a leading "+" if present and removes other
    non-digit characters. A fallback key of the last 10 digits is included for
    NANP-style numbers when possible.
    """

    phone = phone.strip()
    if not phone:
        return ("",)

    canonical = _canonical_phone(phone)
    digits_only = PHONE_DIGITS_RE.sub("", phone)
    last_10 = digits_only[-10:] if len(digits_only) >= 10 else ""

    keys = [canonical]
    if last_10 and last_10 != canonical:
        keys.append(last_10)
    return tuple(keys)


def _canonical_phone(phone: str) -> str:
    leading_plus = phone.startswith("+")
    digits = PHONE_DIGITS_RE.sub("", phone)
    if leading_plus:
        return "+" + digits
    return digits


def parse_vcards(path: str) -> List[Contact]:
    """Parse contacts from a vCard file."""

    with open(path, "r", encoding="utf-8") as f:
        content = f.read().splitlines()
    unfolded = unfold_lines(content)
    return parse_vcards_from_lines(unfolded)


def parse_vcards_from_text(text: str) -> List[Contact]:
    """Parse contacts from raw vCard text (useful for doctests)."""

    return parse_vcards_from_lines(unfold_lines(text.splitlines()))


def unfold_lines(lines: Iterable[str]) -> List[str]:
    """Unfold continuation lines according to RFC rules."""

    unfolded: List[str] = []
    for line in lines:
        if line.startswith(" ") or line.startswith("\t"):
            if not unfolded:
                continue
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def parse_vcards_from_lines(lines: Iterable[str]) -> List[Contact]:
    contacts: List[Contact] = []
    current: Dict[str, Any] = {}
    collecting = False

    def finalize_contact() -> None:
        nonlocal current, collecting
        if not collecting:
            current = {}
            return
        contact = build_contact(current)
        contacts.append(contact)
        current = {}
        collecting = False

    for raw_line in lines:
        line = raw_line.strip("\r\n")
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("BEGIN:VCARD"):
            finalize_contact()
            collecting = True
            current = {"FN": [], "N": [], "TEL": [], "EMAIL": [], "RAW": {}}
            continue
        if upper.startswith("END:VCARD"):
            finalize_contact()
            continue
        if not collecting:
            continue
        property_name, value = split_property(line)
        key = property_name.upper()
        if key in {"FN", "N", "TEL", "EMAIL"}:
            current.setdefault(key, []).append(value)
        current.setdefault("RAW", {}).setdefault(key, []).append(value)

    finalize_contact()
    return contacts


def split_property(line: str) -> Tuple[str, str]:
    """Split a vCard property line into name and value parts."""

    if ":" in line:
        head, value = line.split(":", 1)
    else:
        head, value = line, ""
    name = head.split(";", 1)[0]
    return name, value


def build_contact(data: Dict[str, Any]) -> Contact:
    fn_values = data.get("FN", [])
    n_values = data.get("N", [])
    full_name = fn_values[-1].strip() if fn_values else compose_name(n_values[-1] if n_values else "")

    phones = data.get("TEL", [])
    emails = data.get("EMAIL", [])
    raw_fields = data.get("RAW", {})

    return Contact(full_name=full_name, phones=phones, emails=emails, raw_fields=raw_fields)


def compose_name(n_value: str) -> str:
    """Compose a display name from the N property components."""

    parts = n_value.split(";")
    last_name = parts[0] if len(parts) > 0 else ""
    first_name = parts[1] if len(parts) > 1 else ""
    additional = parts[2] if len(parts) > 2 else ""
    prefix = parts[3] if len(parts) > 3 else ""
    suffix = parts[4] if len(parts) > 4 else ""

    ordered = [prefix, first_name, additional, last_name, suffix]
    return " ".join([p for p in ordered if p]).strip()


# CLI

def main() -> None:
    parser = argparse.ArgumentParser(description="Lookup contacts from a vCard file")
    parser.add_argument("path", help="Path to vCard (.vcf) file")
    parser.add_argument("--phone", help="Phone number to search for")
    parser.add_argument("--email", help="Email address to search for")
    args = parser.parse_args()

    if (args.phone and args.email) or (not args.phone and not args.email):
        parser.error("Provide exactly one of --phone or --email")

    if not os.path.exists(args.path):
        parser.error(f"File not found: {args.path}")

    index = VCardIndex.from_file(args.path)

    if args.phone:
        matches = index.get_all_matches_by_phone(args.phone)
    else:
        key = normalize_email(args.email)
        matches = index.email_index.get(key, []) if key else []

    if not matches:
        print("No matches found")
        return

    best = matches[0]
    print(f"Best match: {best.full_name}")
    print(f"Phones: {', '.join(best.phones) if best.phones else 'N/A'}")
    print(f"Emails: {', '.join(best.emails) if best.emails else 'N/A'}")
    print(f"Total matches: {len(matches)}")


if __name__ == "__main__":
    main()
