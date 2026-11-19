"""Cleaning, PIN checks, and duplicate detection for public listing rows."""

import re
import unicodedata

PIN_PATTERN = re.compile(r"^[1-9][0-9]{5}$")
LEADING_INDEX = re.compile(r"^\d+\.\s*")


def clean_text(value):
    """Collapse whitespace and drop empty strings."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def standardise_place_name(value):
    """Use the portal's English place label, without the index or Hindi text."""
    text = clean_text(value)
    if not text:
        return ""
    english = text.split("/")[0]
    english = LEADING_INDEX.sub("", english).strip(" -")
    return english.title()


def validate_pin(value):
    """Return a 6-digit PIN, or an empty string when the listing value is unusable."""
    text = clean_text(value)
    digits = re.sub(r"\D", "", text)
    if PIN_PATTERN.fullmatch(digits):
        return digits
    return ""


def verification_status(record):
    """Local format check only. This is not a government verification."""
    if not record.get("Enterprise Name") or not record.get("Business Address"):
        return "Missing value"
    if not record.get("PIN Code"):
        return "Invalid PIN"
    return "Format checked"


def duplicate_key(record):
    """Identity used to drop the same public listing row collected twice."""
    parts = [
        clean_text(record.get("Enterprise Name", "")).casefold(),
        clean_text(record.get("Business Address", "")).casefold(),
        clean_text(record.get("PIN Code", "")),
        clean_text(record.get("NIC Code", "")),
        clean_text(record.get("District", "")).casefold(),
    ]
    return "|".join(parts)


def clean_record(record):
    """Return a cleaned copy. Source URL, keyword, and NIC text are preserved."""
    cleaned = {
        "Enterprise Name": clean_text(record.get("Enterprise Name")),
        "Business Address": clean_text(record.get("Business Address")),
        "State": standardise_place_name(record.get("State")),
        "District": standardise_place_name(record.get("District")),
        "PIN Code": validate_pin(record.get("PIN Code")),
        "NIC Code": clean_text(record.get("NIC Code")),
        "NIC Description": clean_text(record.get("NIC Description")),
        "Search Keyword": clean_text(record.get("Search Keyword")),
        "Source URL": clean_text(record.get("Source URL")),
        "Collection Date": clean_text(record.get("Collection Date")),
    }
    cleaned["Verification Status"] = verification_status(cleaned)
    cleaned["dedupe_key"] = duplicate_key(cleaned)
    return cleaned


def remove_duplicates(records):
    """Drop repeated rows and return (unique records, duplicate count)."""
    unique = []
    seen = set()
    duplicates = 0
    for record in records:
        cleaned = record if "dedupe_key" in record else clean_record(record)
        key = cleaned["dedupe_key"]
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique, duplicates
