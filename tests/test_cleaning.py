"""Unit tests for cleaning, duplicate detection, and listing parsing."""

import unittest

from data_cleaning import (
    clean_record,
    duplicate_key,
    remove_duplicates,
    standardise_place_name,
    validate_pin,
    verification_status,
)
from scraper import parse_detail_listing, parse_nic_results, server_pagination_urls

LISTING_HTML = """
<table id="example1">
  <tr>
    <th>SNo.</th>
    <th>Enterprise Name</th>
    <th>Address</th>
    <th>State</th>
    <th>District</th>
    <th>Pin Code</th>
    <th>Social Category</th>
    <th>Gender</th>
    <th></th>
  </tr>
  <tr>
    <td>1</td>
    <td>  Bamboo   Works  </td>
    <td>Market Yard, Pune</td>
    <td>MAHARASHTRA</td>
    <td>PUNE</td>
    <td>411037</td>
    <td>General</td>
    <td>Female</td>
    <td>
      <input type="hidden" name="hdnEmail" value="hidden-not-collected@example.com" />
      <input type="submit" value="Send Business Query" />
    </td>
  </tr>
  <tr>
    <td>2</td>
    <td>Second Unit</td>
    <td>Warje, Pune</td>
    <td>MAHARASHTRA</td>
    <td>PUNE</td>
    <td>4110</td>
    <td>OBC</td>
    <td>Male</td>
    <td><input type="hidden" value="second-hidden@example.com" /></td>
  </tr>
</table>
<script>$("#example1").DataTable({"paging": true});</script>
"""

NIC_HTML = """
<table class="table">
  <tr>
    <th>SNo.</th>
    <th>NIC 5 digit code and description</th>
    <th>MSME Count</th>
  </tr>
  <tr>
    <td>1</td>
    <td>16294-Manufacture of various articles made of bamboo, cane and grass</td>
    <td><a href="SearchRegDetail.aspx?cod=16294&amp;ty=2&amp;si=27&amp;di=479">12</a></td>
  </tr>
</table>
"""


class CleaningTests(unittest.TestCase):
    def test_place_name_drops_index_and_hindi_label(self):
        self.assertEqual(
            standardise_place_name("26. PUNE / पुणे"),
            "Pune",
        )

    def test_pin_keeps_six_digits_and_rejects_short_values(self):
        self.assertEqual(validate_pin(" 411037 "), "411037")
        self.assertEqual(validate_pin("4110"), "")
        self.assertEqual(validate_pin("011037"), "")

    def test_clean_record_sets_status_from_public_fields(self):
        valid = clean_record(
            {
                "Enterprise Name": "Bamboo Works",
                "Business Address": "Market Yard",
                "State": "MAHARASHTRA",
                "District": "PUNE",
                "PIN Code": "411037",
                "NIC Code": "16294",
                "NIC Description": "Bamboo articles",
                "Search Keyword": "Bamboo",
                "Source URL": "https://udyamregistration.gov.in/SearchRegDetail.aspx",
                "Collection Date": "2026-11-18",
            }
        )
        self.assertEqual(valid["State"], "Maharashtra")
        self.assertEqual(valid["Verification Status"], "Format checked")
        invalid = dict(valid)
        invalid["PIN Code"] = ""
        self.assertEqual(verification_status(invalid), "Invalid PIN")

    def test_duplicate_detection_ignores_repeated_listing_rows(self):
        first = clean_record(
            {
                "Enterprise Name": "Bamboo Works",
                "Business Address": "Market Yard",
                "State": "Maharashtra",
                "District": "Pune",
                "PIN Code": "411037",
                "NIC Code": "16294",
                "NIC Description": "Bamboo articles",
                "Search Keyword": "Bamboo",
                "Source URL": "https://example.test/a",
                "Collection Date": "2026-11-18",
            }
        )
        second = dict(first)
        second["Source URL"] = "https://example.test/b"
        unique, duplicates = remove_duplicates([first, second])
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(unique), 1)
        self.assertEqual(duplicate_key(first), duplicate_key(second))


class ParserTests(unittest.TestCase):
    def test_detail_parser_skips_category_gender_and_hidden_email(self):
        rows = parse_detail_listing(
            LISTING_HTML,
            "https://udyamregistration.gov.in/SearchRegDetail.aspx?cod=16294",
            "Bamboo",
            "16294",
            "Manufacture of bamboo articles",
            "2026-11-18",
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["Enterprise Name"], "Bamboo Works")
        self.assertEqual(rows[0]["PIN Code"], "411037")
        self.assertEqual(rows[1]["Verification Status"], "Invalid PIN")
        blob = " ".join(" ".join(row.values()) for row in rows)
        self.assertNotIn("@", blob)
        self.assertNotIn("General", blob)
        self.assertNotIn("Female", blob)
        self.assertNotIn("Social Category", blob)

    def test_nic_parser_reads_code_count_and_detail_link(self):
        rows = parse_nic_results(
            NIC_HTML, "https://udyamregistration.gov.in/searchregistration.aspx"
        )
        self.assertEqual(rows[0]["nic_code"], "16294")
        self.assertEqual(rows[0]["portal_count"], 12)
        self.assertIn("di=479", rows[0]["detail_url"])

    def test_browser_pagination_is_not_treated_as_another_request(self):
        page = "https://udyamregistration.gov.in/SearchRegDetail.aspx?cod=16294"
        self.assertEqual(server_pagination_urls(LISTING_HTML, page), [])


if __name__ == "__main__":
    unittest.main()
