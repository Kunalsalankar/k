"""Runtime settings for the TCI MSME collector.

Values that affect the portal are kept here so the scraper, storage, and
dashboard share one configuration. No credentials are used.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent

DATA_DIR = ROOT / "data"
DATABASE_DIR = ROOT / "database"
LOG_DIR = ROOT / "logs"

CSV_PATH = DATA_DIR / "companies.csv"
EXCEL_PATH = DATA_DIR / "companies.xlsx"
LOOKUP_EXCEL_PATH = DATA_DIR / "udyam_lookups.xlsx"
DATABASE_PATH = DATABASE_DIR / "msme_data.db"
LOG_PATH = LOG_DIR / "scraper.log"

BASE_URL = "https://udyamregistration.gov.in/"
SEARCH_URL = "https://udyamregistration.gov.in/searchregistration.aspx"
VERIFY_PAGE_URL = "https://udyamregistration.gov.in/Udyam_Verify.aspx"
FORGOT_URN_URL = "https://udyamregistration.gov.in/ForgotRegNo.aspx"
LOGIN_URL = "https://udyamregistration.gov.in/Udyam_Login.aspx"
UAM_VERIFY_URL = "https://udyamregistration.gov.in/UA/UA_VerifyUAM.aspx"
OFFICIAL_LOOKUP_PAGES = (
    VERIFY_PAGE_URL,
    FORGOT_URN_URL,
    LOGIN_URL,
    UAM_VERIFY_URL,
)
ALLOWED_HOST = "udyamregistration.gov.in"

# Udyam Registration radio value on the public search form. Udyog Aadhaar is 1.
UDYAM_REGISTRATION_TYPE = "2"

# The portal certificate is issued by eMudhra. This workstation's trust store
# does not currently include that issuer, so certificate verification fails
# before any page is returned. The client still refuses any response whose
# final host is not ALLOWED_HOST. Set this to True where the issuer is trusted.
VERIFY_SSL = False

USER_AGENT = "TCI-MSME-Collector/1.0 (internal public-listing research)"
TIMEOUT_SECONDS = 90
MAX_RETRIES = 3
BACKOFF_FACTOR = 1.0
REQUEST_DELAY_SECONDS = 2.0

# Server-side listing pages only. The current detail page puts every row in
# one response and pages it in the browser, so this cap is not used today.
MAX_LISTING_PAGES = 25

# Status codes that mean the portal is refusing automated access.
DENIED_STATUS_CODES = frozenset({401, 403, 429})

LOOKUP_COLUMNS = [
    "Input PAN (masked)",
    "Input Name",
    "State",
    "District",
    "Udyam Registration Number",
    "Verification Status",
    "Verification Source",
    "Verification Date",
    "Remarks",
]

EXPORT_COLUMNS = [
    "Enterprise Name",
    "Business Address",
    "State",
    "District",
    "PIN Code",
    "NIC Code",
    "NIC Description",
    "Search Keyword",
    "Source URL",
    "Collection Date",
    "Verification Status",
]

# Detail-table headers the collector is allowed to read.
ALLOWED_DETAIL_HEADERS = {
    "enterprise name": "Enterprise Name",
    "address": "Business Address",
    "state": "State",
    "district": "District",
    "pin code": "PIN Code",
}

# Headers that must never be copied, even though the listing renders them.
BLOCKED_HEADER_MARKERS = (
    "social category",
    "gender",
    "email",
    "mobile",
    "phone",
    "gstin",
    "pan",
    "udyam",
    "aadhaar",
    "aadhar",
)
