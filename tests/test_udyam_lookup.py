"""Tests for PAN checks, official-source matching, and lookup export."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from scraper import UdyamAccessError, UdyamRequestError
from udyam_lookup import (
    INVALID_INPUT,
    MULTIPLE_MATCHES,
    NOT_FOUND,
    PARTIALLY_VERIFIED,
    UNVERIFIABLE,
    VERIFIED,
    UdyamLookup,
    assign_status,
    format_lookup_report,
    mask_pan,
    parse_authorized_candidates,
    permits_pan_lookup,
    usable_urn,
    validate_pan,
)

PAN = "ABCPD1234F"
MASKED_PAN = "AB*******F"

OPEN_PAN_FORM = """
<html><body>
<form>
  <label for="pan">PAN</label>
  <input id="pan" name="txtPAN" type="text" />
  <input type="submit" value="Search" />
</form>
</body></html>
"""

OTP_PAGE = """
<html><body>
<h1>Forgot Udyam Registration Number</h1>
<p>Enter OTP (one time password) sent to the registered mobile.</p>
<p>Example: UDYAM-XX-00-0000000</p>
<p>UDYAM-HR-07-0012345</p>
<input name="txtOtp" type="text" placeholder="Enter OTP" />
</body></html>
"""

CAPTCHA_PAGE = """
<html><body>
<label for="uam">UAM No.</label>
<input id="uam" name="txtUAN" type="text" />
<input name="txtCaptcha" type="text" placeholder="Verification Code" />
</body></html>
"""

VERIFIED_RESULT = """
<table>
  <tr><th>Udyam Registration Number</th><td>UDYAM-HR-07-0012345</td></tr>
  <tr><th>Enterprise Name</th><td>ABC Enterprises</td></tr>
  <tr><th>State</th><td>HARYANA</td></tr>
  <tr><th>District</th><td>GURUGRAM</td></tr>
</table>
"""

PARTIAL_RESULT = """
<table>
  <tr><th>Udyam Registration Number</th><td>UDYAM-HR-07-0012345</td></tr>
  <tr><th>Enterprise Name</th><td>ABC Enterprises</td></tr>
  <tr><th>State</th><td>Haryana</td></tr>
  <tr><th>District</th><td>Faridabad</td></tr>
</table>
"""

MULTIPLE_RESULT = """
<table>
  <tr><th>Udyam Registration Number</th><td>UDYAM-HR-07-0012345</td></tr>
  <tr><th>Enterprise Name</th><td>ABC Enterprises</td></tr>
  <tr><th>State</th><td>Haryana</td></tr>
  <tr><th>District</th><td>Gurugram</td></tr>
  <tr><th>Udyam Registration Number</th><td>UDYAM-HR-07-0099999</td></tr>
  <tr><th>Owner Name</th><td>ABC Enterprises</td></tr>
  <tr><th>State</th><td>Haryana</td></tr>
  <tr><th>District</th><td>Gurugram</td></tr>
</table>
"""

STRAY_NUMBER = """
<p>See the sample certificate UDYAM-HR-07-0012345 before you apply.</p>
<p>Example format UDYAM-XX-00-0000000.</p>
"""


class FakePortal:
    def __init__(self, pages=None, result_html="", location_error=None, request_error=None):
        self.pages = pages if pages is not None else [OTP_PAGE, CAPTCHA_PAGE]
        self.result_html = result_html
        self.location_error = location_error
        self.request_error = request_error
        self.submitted = False
        self.submitted_pan = ""

    def confirm_location(self, state, district):
        if self.location_error:
            raise self.location_error
        if self.request_error:
            raise self.request_error
        return "Haryana", "Gurugram"

    def official_pages(self):
        if self.request_error and self.location_error is None:
            return [("https://udyamregistration.gov.in/Udyam_Verify.aspx", page) for page in self.pages]
        return [("https://udyamregistration.gov.in/Udyam_Verify.aspx", page) for page in self.pages]

    def submit_pan_lookup(self, url, html, pan, name, state, district):
        self.submitted = True
        self.submitted_pan = pan
        return self.result_html

    def close(self):
        return None


class PanValidationTests(unittest.TestCase):
    def test_pan_format_accepts_holder_type_and_rejects_the_sample(self):
        normalised, error = validate_pan(" abcpd1234f ")
        self.assertEqual(normalised, PAN)
        self.assertEqual(error, "")
        rejected, message = validate_pan("ABCDE1234F")
        self.assertEqual(rejected, "")
        self.assertIn("fourth character", message)

    def test_mask_hides_the_middle_of_the_pan(self):
        self.assertEqual(mask_pan(PAN), MASKED_PAN)

    def test_required_name_state_and_district(self):
        lookup = UdyamLookup(portal=FakePortal(), on_date=lambda: date(2026, 11, 18))
        missing_name = lookup.lookup(PAN, "  ", "Haryana", "Gurugram", excel_path=self.excel)
        self.assertEqual(missing_name["verification_status"], INVALID_INPUT)
        self.assertIn("name", missing_name["remarks"])
        all_districts = lookup.lookup(PAN, "ABC Enterprises", "Haryana", "All", excel_path=self.excel)
        self.assertEqual(all_districts["verification_status"], INVALID_INPUT)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.excel = Path(self.directory.name) / "lookups.xlsx"

    def tearDown(self):
        self.directory.cleanup()


class MatchingTests(unittest.TestCase):
    def test_placeholder_and_loose_text_are_not_registration_numbers(self):
        self.assertEqual(usable_urn("UDYAM-XX-00-0000000"), "")
        self.assertEqual(usable_urn("UDYAM-HR-00-0000000"), "")
        self.assertEqual(parse_authorized_candidates(STRAY_NUMBER), [])
        self.assertEqual(parse_authorized_candidates(OTP_PAGE), [])
        self.assertFalse(permits_pan_lookup(OTP_PAGE))
        self.assertFalse(permits_pan_lookup(CAPTCHA_PAGE))

    def test_labelled_official_result_can_be_verified(self):
        candidates = parse_authorized_candidates(VERIFIED_RESULT)
        decision = assign_status(candidates, "ABC Enterprises", "Haryana", "Gurugram")
        self.assertEqual(decision["verification_status"], VERIFIED)
        self.assertEqual(decision["udyam_registration_number"], "UDYAM-HR-07-0012345")

    def test_name_match_with_a_different_district_is_partial(self):
        decision = assign_status(
            parse_authorized_candidates(PARTIAL_RESULT),
            "ABC Enterprises",
            "Haryana",
            "Gurugram",
        )
        self.assertEqual(decision["verification_status"], PARTIALLY_VERIFIED)
        self.assertEqual(decision["udyam_registration_number"], "UDYAM-HR-07-0012345")

    def test_two_full_matches_do_not_select_a_number(self):
        decision = assign_status(
            parse_authorized_candidates(MULTIPLE_RESULT),
            "ABC Enterprises",
            "Haryana",
            "Gurugram",
        )
        self.assertEqual(decision["verification_status"], MULTIPLE_MATCHES)
        self.assertEqual(decision["udyam_registration_number"], "")

    def test_empty_authorised_result_is_not_found(self):
        decision = assign_status([], "ABC Enterprises", "Haryana", "Gurugram")
        self.assertEqual(decision["verification_status"], NOT_FOUND)
        self.assertEqual(decision["udyam_registration_number"], "")


class LookupWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.excel = Path(self.directory.name) / "lookups.xlsx"

    def tearDown(self):
        self.directory.cleanup()

    def lookup(self, portal, pan=PAN):
        tool = UdyamLookup(portal=portal, on_date=lambda: date(2026, 11, 18))
        return tool.lookup(pan, "ABC Enterprises", "Haryana", "Gurugram", excel_path=self.excel)

    def test_otp_and_captcha_pages_are_unverifiable_and_submit_nothing(self):
        portal = FakePortal()
        result = self.lookup(portal)
        self.assertEqual(result["verification_status"], UNVERIFIABLE)
        self.assertEqual(result["udyam_registration_number"], "")
        self.assertFalse(portal.submitted)
        self.assertNotIn("UDYAM-", result["remarks"])
        report = format_lookup_report(result)
        self.assertIn("Udyam Registration Number: NOT FOUND", report)
        self.assertIn("Verification Status: UNVERIFIABLE", report)

    def test_pan_is_not_turned_into_a_registration_number(self):
        first = self.lookup(FakePortal(), pan=PAN)
        second = self.lookup(FakePortal(), pan="ABCCD1234G")
        self.assertEqual(first["udyam_registration_number"], "")
        self.assertEqual(second["udyam_registration_number"], "")
        self.assertNotEqual(first["input_pan_masked"], second["input_pan_masked"])

    def test_open_pan_form_verifies_the_labelled_number(self):
        portal = FakePortal(pages=[OPEN_PAN_FORM], result_html=VERIFIED_RESULT)
        result = self.lookup(portal)
        self.assertTrue(portal.submitted)
        self.assertEqual(portal.submitted_pan, PAN)
        self.assertEqual(result["verification_status"], VERIFIED)
        self.assertEqual(result["udyam_registration_number"], "UDYAM-HR-07-0012345")

    def test_open_pan_form_with_no_rows_is_not_found(self):
        portal = FakePortal(pages=[OPEN_PAN_FORM], result_html="<table></table>")
        result = self.lookup(portal)
        self.assertEqual(result["verification_status"], NOT_FOUND)

    def test_unknown_district_is_invalid_input(self):
        portal = FakePortal(location_error=LookupError("District was not in the portal list."))
        result = self.lookup(portal)
        self.assertEqual(result["verification_status"], INVALID_INPUT)
        self.assertFalse(portal.submitted)

    def test_access_restriction_and_timeout_stay_unverifiable(self):
        restricted = self.lookup(FakePortal(location_error=UdyamAccessError("restricted")))
        unavailable = self.lookup(FakePortal(request_error=UdyamRequestError("timeout")))
        self.assertEqual(restricted["verification_status"], UNVERIFIABLE)
        self.assertEqual(unavailable["verification_status"], UNVERIFIABLE)
        self.assertIn("restricted", restricted["remarks"].lower() + "access")
        self.assertIn("did not respond", unavailable["remarks"])

    def test_excel_contains_masked_pan_and_the_registration_number(self):
        portal = FakePortal(pages=[OPEN_PAN_FORM], result_html=VERIFIED_RESULT)
        self.lookup(portal)
        self.lookup(FakePortal())
        frame = pd.read_excel(self.excel, dtype=str).fillna("")
        self.assertEqual(list(frame.columns), [
            "Input PAN (masked)",
            "Input Name",
            "State",
            "District",
            "Udyam Registration Number",
            "Verification Status",
            "Verification Source",
            "Verification Date",
            "Remarks",
        ])
        self.assertEqual(frame.iloc[0]["Udyam Registration Number"], "UDYAM-HR-07-0012345")
        self.assertEqual(frame.iloc[0]["Verification Status"], VERIFIED)
        self.assertEqual(frame.iloc[1]["Verification Status"], UNVERIFIABLE)
        blob = frame.to_csv(index=False)
        self.assertNotIn(PAN, blob)
        self.assertIn(MASKED_PAN, blob)


if __name__ == "__main__":
    unittest.main()
