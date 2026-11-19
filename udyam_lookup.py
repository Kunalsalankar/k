"""Identify a Udyam Registration Number from official portal pages.

The public portal can confirm a number the caller already has, or recover a
forgotten number after a one-time password. It does not publish a PAN search.
This module never invents a number and never submits a PAN unless an official
page is an open PAN lookup with no captcha, OTP, or login step.
"""

import logging
import re
from datetime import date

from bs4 import BeautifulSoup

import config
import export
from data_cleaning import clean_text, standardise_place_name
from scraper import (
    DISTRICT_SELECT,
    STATE_SELECT,
    UdyamAccessError,
    UdyamRequestError,
    UdyamScraper,
    form_payload,
)

LOGGER = logging.getLogger("tci_msme")

VERIFIED = "VERIFIED"
PARTIALLY_VERIFIED = "PARTIALLY VERIFIED"
MULTIPLE_MATCHES = "MULTIPLE MATCHES"
NOT_FOUND = "NOT FOUND"
UNVERIFIABLE = "UNVERIFIABLE"
INVALID_INPUT = "INVALID INPUT"

PAN_PATTERN = re.compile(r"^[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]$")
URN_PATTERN = re.compile(r"^UDYAM-[A-Z]{2}-\d{2}-\d{7}$")
PAN_TOKEN = re.compile(r"\bpan\b", re.IGNORECASE)
SENSITIVE_CONTROL = re.compile(
    r"\b(aadhaar|aadhar|otp|mobile|captcha|password)\b",
    re.IGNORECASE,
)
OFFICIAL_SOURCE = "Official Udyam portal"

UNVERIFIABLE_REMARK = (
    "The official Udyam portal does not offer a public search from PAN to an "
    "Udyam Registration Number. Verifying a number requires that number. "
    "Printing a certificate or recovering a forgotten number requires a "
    "one-time password or a captcha, which this tool does not bypass. "
    "No registration number was guessed."
)


def mask_pan(pan):
    """Keep the first two and last characters. The middle is never stored."""
    text = clean_text(pan).upper()
    if len(text) < 3:
        return ""
    return text[:2] + ("*" * (len(text) - 3)) + text[-1:]


def validate_pan(pan):
    """Return the normalised PAN, or an error when the official format fails."""
    text = re.sub(r"\s+", "", clean_text(pan)).upper()
    if PAN_PATTERN.fullmatch(text):
        return text, ""
    return "", (
        "PAN format is invalid. A PAN is 10 characters: five letters, four digits, "
        "and one letter. The fourth character must be a holder type such as P, C, H, or F."
    )


def compact_name(value):
    return re.sub(r"[^a-z0-9]", "", clean_text(value).casefold())


def places_match(left, right):
    if not clean_text(left) or not clean_text(right):
        return False
    return standardise_place_name(left).casefold() == standardise_place_name(right).casefold()


def usable_urn(value):
    """Accept a real registration number and reject portal examples."""
    text = clean_text(value).upper()
    if not URN_PATTERN.fullmatch(text):
        return ""
    state_code, _series, serial = text.split("-")[1:]
    if state_code in {"XX", "YY"} or set(serial) == {"0"}:
        return ""
    return text


def page_blocks_lookup(html):
    """True when the page asks for a captcha, OTP, or other access step."""
    sample = (html or "")[:150000].casefold()
    if "recaptcha" in sample or 'id="captcha' in sample or "cf-error-details" in sample:
        return True
    if "one time password" in sample or "one-time password" in sample or "otp" in sample:
        return True
    if "captcha" in sample or "verification code" in sample:
        return True
    return False


def _field_blob(field):
    return " ".join(
        clean_text(field.get(key))
        for key in ("name", "id", "placeholder", "aria-label")
        if field.get(key)
    )


def find_pan_field_name(html):
    soup = BeautifulSoup(html or "", "html.parser")
    for field in soup.find_all("input"):
        field_type = (field.get("type") or "text").lower()
        if field_type not in {"text", "search"}:
            continue
        if PAN_TOKEN.search(_field_blob(field)):
            return field.get("name") or ""
    for label in soup.find_all("label"):
        if not PAN_TOKEN.search(label.get_text(" ", strip=True)):
            continue
        target = label.get("for")
        field = soup.find(id=target) if target else None
        if field is not None and (field.get("type") or "text").lower() in {"text", "search"}:
            return field.get("name") or ""
    return ""


def _has_sensitive_control(html):
    soup = BeautifulSoup(html or "", "html.parser")
    for field in soup.find_all(["input", "textarea"]):
        field_type = (field.get("type") or "text").lower()
        if field_type in {"hidden", "submit", "button", "image"}:
            continue
        if SENSITIVE_CONTROL.search(_field_blob(field)):
            return True
    return False


def permits_pan_lookup(html):
    """An official page may be queried only when PAN is the open search field."""
    if not html or page_blocks_lookup(html):
        return False
    if not find_pan_field_name(html):
        return False
    if _has_sensitive_control(html):
        return False
    return True


def _label_pairs(html):
    soup = BeautifulSoup(html or "", "html.parser")
    pairs = []
    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if len(cells) >= 2 and cells[0] and cells[1]:
            pairs.append((cells[0], cells[1]))
    for term in soup.find_all("dt"):
        value = term.find_next_sibling("dd")
        if value is not None:
            pairs.append((term.get_text(" ", strip=True), value.get_text(" ", strip=True)))
    return pairs


def parse_authorized_candidates(html):
    """Read registration numbers only from labelled official result fields."""
    if page_blocks_lookup(html):
        return []
    candidates = []
    current = None
    for label, value in _label_pairs(html):
        key = clean_text(label).casefold()
        if "udyam" in key and ("registration" in key or key.endswith("urn")):
            if current and current["urn"]:
                candidates.append(current)
            current = _empty_candidate()
            current["urn"] = usable_urn(value)
            continue
        if current is None:
            continue
        if "owner" in key and "name" in key:
            current["owner_name"] = clean_text(value)
        elif "name" in key:
            current["enterprise_name"] = clean_text(value)
        elif key == "state":
            current["state"] = clean_text(value)
        elif "district" in key:
            current["district"] = clean_text(value)
    if current and current["urn"]:
        candidates.append(current)
    return [item for item in candidates if item["urn"]]


def _empty_candidate():
    return {
        "urn": "",
        "enterprise_name": "",
        "owner_name": "",
        "state": "",
        "district": "",
    }


def names_match(supplied_name, candidate):
    supplied = compact_name(supplied_name)
    if not supplied:
        return False
    return supplied in {
        compact_name(candidate.get("enterprise_name")),
        compact_name(candidate.get("owner_name")),
    }


def candidate_is_verified(candidate, name, state, district):
    return (
        bool(candidate.get("urn"))
        and names_match(name, candidate)
        and places_match(state, candidate.get("state"))
        and places_match(district, candidate.get("district"))
    )


def assign_status(candidates, name, state, district):
    """Choose a status from official candidates. Never select a guessed number."""
    usable = [item for item in candidates if item.get("urn")]
    if not usable:
        return {
            "udyam_registration_number": "",
            "verification_status": NOT_FOUND,
            "remarks": "The authorized source returned no matching registration.",
        }
    verified = [
        item for item in usable if candidate_is_verified(item, name, state, district)
    ]
    if len(verified) == 1:
        return {
            "udyam_registration_number": verified[0]["urn"],
            "verification_status": VERIFIED,
            "remarks": "The registration number matches the supplied name, state, and district.",
        }
    if len(verified) > 1:
        return {
            "udyam_registration_number": "",
            "verification_status": MULTIPLE_MATCHES,
            "remarks": (
                f"The official source returned {len(verified)} records that match "
                "the supplied details. No single number was selected."
            ),
        }
    if len(usable) == 1:
        return {
            "udyam_registration_number": usable[0]["urn"],
            "verification_status": PARTIALLY_VERIFIED,
            "remarks": (
                "A candidate number was returned by the official source, but the "
                "name, state, and district did not all match."
            ),
        }
    return {
        "udyam_registration_number": "",
        "verification_status": MULTIPLE_MATCHES,
        "remarks": (
            f"The official source returned {len(usable)} possible records. "
            "No single number was selected."
        ),
    }


def validate_lookup_inputs(pan, name, state, district):
    _normalised, pan_error = validate_pan(pan)
    if pan_error:
        return pan_error
    if not clean_text(name):
        return "Enterprise or owner name is required."
    if not clean_text(state):
        return "State is required."
    if not clean_text(district) or clean_text(district).casefold() == "all":
        return "A specific district is required."
    return ""


class ScraperPortal:
    """Official Udyam pages used to validate a place and look for a PAN search."""

    def __init__(self, scraper=None):
        self.scraper = scraper or UdyamScraper()

    def close(self):
        self.scraper.close()

    def confirm_location(self, state, district):
        search_page = self.scraper._get(config.SEARCH_URL)
        state_code, state_label = self.scraper._option_value(search_page, STATE_SELECT, state)
        district_page = self.scraper._select_state(search_page, state_code, keyword="")
        _district_code, district_label = self.scraper._option_value(
            district_page, DISTRICT_SELECT, district
        )
        return standardise_place_name(state_label), standardise_place_name(district_label)

    def official_pages(self):
        return [(url, self.scraper._get(url)) for url in config.OFFICIAL_LOOKUP_PAGES]

    def submit_pan_lookup(self, url, html, pan, name, state, district):
        """POST a PAN only to a page already accepted by permits_pan_lookup."""
        field_name = find_pan_field_name(html)
        if not field_name or not permits_pan_lookup(html):
            raise UdyamAccessError(
                "Stopped before submitting PAN because the page is not an open PAN lookup."
            )
        payload = form_payload(html)
        payload[field_name] = pan
        del name, state, district
        return self.scraper._post(url, payload)


class UdyamLookup:
    def __init__(self, portal=None, on_date=None):
        self.portal = portal or ScraperPortal()
        self.on_date = on_date or date.today

    def close(self):
        self.portal.close()

    def lookup(self, pan, name, state, district, excel_path=None):
        cleaned_name = clean_text(name)
        supplied_state = clean_text(state)
        supplied_district = clean_text(district)
        normalised_pan, pan_error = validate_pan(pan)
        input_error = pan_error or validate_lookup_inputs(
            normalised_pan, cleaned_name, supplied_state, supplied_district
        )
        masked = mask_pan(normalised_pan or pan)
        if input_error:
            LOGGER.info("Udyam lookup rejected invalid input")
            return self._finish(
                self._result(
                    masked,
                    cleaned_name,
                    standardise_place_name(supplied_state),
                    standardise_place_name(supplied_district),
                    "",
                    INVALID_INPUT,
                    "Input check",
                    input_error,
                ),
                excel_path,
            )

        LOGGER.info("Udyam lookup started for PAN %s", masked)
        try:
            confirmed_state, confirmed_district = self.portal.confirm_location(
                supplied_state, supplied_district
            )
        except LookupError as exc:
            return self._finish(
                self._result(
                    masked,
                    cleaned_name,
                    standardise_place_name(supplied_state),
                    standardise_place_name(supplied_district),
                    "",
                    INVALID_INPUT,
                    OFFICIAL_SOURCE,
                    str(exc),
                ),
                excel_path,
            )
        except UdyamAccessError:
            return self._access_result(
                masked, cleaned_name, supplied_state, supplied_district, excel_path
            )
        except (UdyamRequestError, TimeoutError):
            return self._unavailable_result(
                masked, cleaned_name, supplied_state, supplied_district, excel_path
            )

        try:
            pages = self.portal.official_pages()
        except UdyamAccessError:
            return self._access_result(
                masked, cleaned_name, confirmed_state, confirmed_district, excel_path
            )
        except (UdyamRequestError, TimeoutError):
            return self._unavailable_result(
                masked, cleaned_name, confirmed_state, confirmed_district, excel_path
            )

        permitted = [(url, html) for url, html in pages if permits_pan_lookup(html)]
        if not permitted:
            return self._finish(
                self._result(
                    masked,
                    cleaned_name,
                    confirmed_state,
                    confirmed_district,
                    "",
                    UNVERIFIABLE,
                    OFFICIAL_SOURCE,
                    UNVERIFIABLE_REMARK,
                ),
                excel_path,
            )

        url, html = permitted[0]
        try:
            result_html = self.portal.submit_pan_lookup(
                url, html, normalised_pan, cleaned_name, confirmed_state, confirmed_district
            )
        except UdyamAccessError:
            return self._access_result(
                masked, cleaned_name, confirmed_state, confirmed_district, excel_path
            )
        except (UdyamRequestError, TimeoutError):
            return self._unavailable_result(
                masked, cleaned_name, confirmed_state, confirmed_district, excel_path
            )

        if page_blocks_lookup(result_html):
            return self._access_result(
                masked, cleaned_name, confirmed_state, confirmed_district, excel_path
            )
        decision = assign_status(
            parse_authorized_candidates(result_html),
            cleaned_name,
            confirmed_state,
            confirmed_district,
        )
        return self._finish(
            self._result(
                masked,
                cleaned_name,
                confirmed_state,
                confirmed_district,
                decision["udyam_registration_number"],
                decision["verification_status"],
                OFFICIAL_SOURCE,
                decision["remarks"],
            ),
            excel_path,
        )

    def _result(self, masked, name, state, district, number, status, source, remarks):
        return {
            "input_pan_masked": masked,
            "input_name": name,
            "state": state,
            "district": district,
            "udyam_registration_number": number,
            "verification_status": status,
            "verification_source": source,
            "verification_date": self.on_date().isoformat(),
            "remarks": remarks,
        }

    def _finish(self, result, excel_path):
        result["excel_path"] = str(export.append_lookup_export(result, excel_path))
        return result

    def _access_result(self, masked, name, state, district, excel_path):
        return self._finish(
            self._result(
                masked,
                name,
                standardise_place_name(state),
                standardise_place_name(district),
                "",
                UNVERIFIABLE,
                OFFICIAL_SOURCE,
                "The official portal restricted access. Automated collection stopped, "
                "and no registration number was guessed.",
            ),
            excel_path,
        )

    def _unavailable_result(self, masked, name, state, district, excel_path):
        return self._finish(
            self._result(
                masked,
                name,
                standardise_place_name(state),
                standardise_place_name(district),
                "",
                UNVERIFIABLE,
                OFFICIAL_SOURCE,
                "The official portal did not respond after the allowed retries. "
                "No registration number was guessed.",
            ),
            excel_path,
        )


def format_lookup_report(result):
    number = result["udyam_registration_number"] or "NOT FOUND"
    return "\n".join(
        [
            "========================================",
            "UDYAM REGISTRATION VERIFICATION",
            "========================================",
            "",
            f"Enterprise Name: {result['input_name']}",
            f"State: {result['state']}",
            f"District: {result['district']}",
            "",
            f"Udyam Registration Number: {number}",
            "",
            f"Verification Status: {result['verification_status']}",
            "",
            f"Verification Source: {result['verification_source']}",
            "",
            f"Remarks: {result['remarks']}",
            "",
            f"Excel: {result['excel_path']}",
            "========================================",
        ]
    )
