"""Public Udyam search and listing collector.

The search form is an ASP.NET postback. District options are returned only
after a state postback. NIC counts link to SearchRegDetail.aspx, which
renders the enterprise table in the first HTML response. DataTables pages
that table in the browser, so there is no server page to request.

Social category, gender, and the hidden business-query email are present in
the listing HTML and are intentionally ignored.
"""

import logging
import re
import time
from datetime import date
from urllib.parse import parse_qs, urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config
from data_cleaning import clean_record, remove_duplicates, standardise_place_name

LOGGER = logging.getLogger("tci_msme")

STATE_SELECT = "ctl00$ContentPlaceHolder1$ddlPState"
DISTRICT_SELECT = "ctl00$ContentPlaceHolder1$ddlPDistrict"
KEYWORD_INPUT = "ctl00$ContentPlaceHolder1$txtsearchNic"
SEARCH_BUTTON = "ctl00$ContentPlaceHolder1$btnSearch"
REGISTRATION_TYPE = "ctl00$ContentPlaceHolder1$rblType"


class UdyamAccessError(RuntimeError):
    """The portal refused the request. Callers must not retry this."""


class UdyamRequestError(RuntimeError):
    """The portal did not return a page after the allowed retries."""


class UdyamScraper:
    def __init__(self, delay_seconds=None):
        self.delay_seconds = (
            config.REQUEST_DELAY_SECONDS if delay_seconds is None else delay_seconds
        )
        self.session = self._build_session()
        self._last_request_at = 0.0

    def close(self):
        self.session.close()

    def collect(self, state, district, keyword, nic_code=None):
        """Run one public search and return cleaned enterprise rows."""
        keyword = keyword.strip()
        nic_code = (nic_code or "").strip()
        if not keyword:
            raise ValueError("A business activity keyword is required.")
        if not state:
            raise ValueError("A state is required.")

        search_page = self._get(config.SEARCH_URL)
        state_code, state_label = self._option_value(search_page, STATE_SELECT, state)
        state_label = standardise_place_name(state_label)
        district_page = self._select_state(search_page, state_code, keyword)
        if district.strip().casefold() in {"", "all"}:
            district_code, district_label = "0", "All"
        else:
            district_code, district_label = self._option_value(
                district_page, DISTRICT_SELECT, district
            )
            district_label = standardise_place_name(district_label)
        result_page = self._submit_search(
            district_page, state_code, district_code, keyword
        )
        summaries = parse_nic_results(result_page, config.SEARCH_URL)
        if nic_code:
            summaries = [item for item in summaries if item["nic_code"] == nic_code]
            if not summaries:
                raise LookupError(
                    f"NIC {nic_code} was not in the public results for this search."
                )

        collected_on = date.today().isoformat()
        records = []
        for summary in summaries:
            pages = self._listing_pages(summary["detail_url"])
            page_rows = []
            for page_url, html in pages:
                page_rows.extend(
                    parse_detail_listing(
                        html,
                        page_url,
                        keyword,
                        summary["nic_code"],
                        summary["nic_description"],
                        collected_on,
                    )
                )
            unique_rows, duplicate_count = remove_duplicates(page_rows)
            summary["collected_count"] = len(unique_rows)
            if duplicate_count:
                LOGGER.info(
                    "Dropped %s duplicate rows for NIC %s",
                    duplicate_count,
                    summary["nic_code"],
                )
            records.extend(unique_rows)
            LOGGER.info(
                "NIC %s portal count %s, collected %s",
                summary["nic_code"],
                summary["portal_count"],
                summary["collected_count"],
            )

        records, duplicate_count = remove_duplicates(records)
        if duplicate_count:
            LOGGER.info("Dropped %s duplicate rows across NIC listings", duplicate_count)
        return {
            "state": state_label,
            "district": district_label,
            "keyword": keyword,
            "nic_filter": nic_code,
            "portal_msme_count": sum(item["portal_count"] for item in summaries),
            "summaries": summaries,
            "records": records,
        }

    def _build_session(self):
        retry = Retry(
            total=config.MAX_RETRIES,
            connect=config.MAX_RETRIES,
            read=config.MAX_RETRIES,
            status=config.MAX_RETRIES,
            backoff_factor=config.BACKOFF_FACTOR,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            respect_retry_after_header=False,
            raise_on_status=False,
        )
        session = requests.Session()
        session.headers["User-Agent"] = config.USER_AGENT
        session.verify = config.VERIFY_SSL
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        if not config.VERIFY_SSL:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            LOGGER.warning(
                "TLS verification is disabled because the portal issuer is not "
                "in the local trust store. Responses from any other host are rejected."
            )
        return session

    def _pause(self):
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.delay_seconds - elapsed
        if self._last_request_at and remaining > 0:
            time.sleep(remaining)

    def _check_response(self, response):
        host = urlparse(response.url).hostname or ""
        if host.casefold() != config.ALLOWED_HOST:
            raise UdyamAccessError(
                f"Stopped because the response host was {host or 'unknown'}."
            )
        if response.status_code in config.DENIED_STATUS_CODES:
            raise UdyamAccessError(
                f"Portal returned HTTP {response.status_code}. "
                "Automated access was not retried."
            )
        sample = response.text[:150000].casefold()
        if "recaptcha" in sample or 'id="captcha' in sample or "cf-error-details" in sample:
            raise UdyamAccessError(
                "Portal presented an access challenge. Automated collection stopped."
            )
        response.raise_for_status()

    def _get(self, url):
        self._pause()
        LOGGER.info("GET %s", url.split("?")[0])
        try:
            response = self.session.get(url, timeout=config.TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise UdyamRequestError(f"Request failed after retries: {exc}") from exc
        self._last_request_at = time.monotonic()
        self._check_response(response)
        return response.text

    def _post(self, url, payload):
        self._pause()
        LOGGER.info("POST %s", url.split("?")[0])
        try:
            response = self.session.post(url, data=payload, timeout=config.TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise UdyamRequestError(f"Request failed after retries: {exc}") from exc
        self._last_request_at = time.monotonic()
        self._check_response(response)
        return response.text

    def _select_state(self, html, state_code, keyword):
        payload = form_payload(html)
        payload["__EVENTTARGET"] = STATE_SELECT
        payload["__EVENTARGUMENT"] = ""
        payload[REGISTRATION_TYPE] = config.UDYAM_REGISTRATION_TYPE
        payload[STATE_SELECT] = state_code
        payload[DISTRICT_SELECT] = "0"
        payload[KEYWORD_INPUT] = keyword
        return self._post(config.SEARCH_URL, payload)

    def _submit_search(self, html, state_code, district_code, keyword):
        payload = form_payload(html)
        payload["__EVENTTARGET"] = ""
        payload["__EVENTARGUMENT"] = ""
        payload[REGISTRATION_TYPE] = config.UDYAM_REGISTRATION_TYPE
        payload[STATE_SELECT] = state_code
        payload[DISTRICT_SELECT] = district_code
        payload[KEYWORD_INPUT] = keyword
        payload[SEARCH_BUTTON] = "Search"
        return self._post(config.SEARCH_URL, payload)

    def _listing_pages(self, detail_url):
        """Yield listing HTML. Follows server pagination only when the page has it."""
        html = self._get(detail_url)
        yield detail_url, html
        seen = {detail_url}
        pending = server_pagination_urls(html, detail_url)
        while pending:
            if len(seen) >= config.MAX_LISTING_PAGES:
                LOGGER.warning("Stopped at %s listing pages.", config.MAX_LISTING_PAGES)
                break
            page_url = pending.pop(0)
            if page_url in seen:
                continue
            seen.add(page_url)
            page_html = self._get(page_url)
            yield page_url, page_html
            for next_url in server_pagination_urls(page_html, page_url):
                if next_url not in seen:
                    pending.append(next_url)

    def _option_value(self, html, select_name, label):
        soup = BeautifulSoup(html, "html.parser")
        select = soup.find("select", attrs={"name": select_name})
        if select is None:
            raise LookupError(f"The portal form did not include {select_name}.")
        wanted = label.strip().casefold()
        available = []
        for option in select.find_all("option"):
            english = english_label(option.get_text(" ", strip=True))
            available.append(english)
            if english.casefold() == wanted:
                return option.get("value", ""), english
        raise LookupError(
            f"'{label}' was not in the portal list. Examples: {', '.join(available[:8])}."
        )


def english_label(value):
    text = re.sub(r"\s+", " ", value or "").strip()
    english = text.split("/")[0]
    english = re.sub(r"^\d+\.\s*", "", english).strip(" -")
    return english


def form_payload(html):
    """Hidden and selected form fields, excluding email inputs and submit buttons."""
    soup = BeautifulSoup(html, "html.parser")
    payload = {}
    for field in soup.find_all("input"):
        name = field.get("name")
        if not name or "hdnemail" in name.casefold():
            continue
        field_type = (field.get("type") or "").lower()
        if field_type in {"submit", "button", "image"}:
            continue
        if field_type == "radio" and not field.has_attr("checked"):
            continue
        payload[name] = field.get("value") or ""
    return payload


def parse_nic_results(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    table = _table_with_headers(soup, {"nic 5 digit code and description", "msme count"})
    if table is None:
        LOGGER.info("The search returned no NIC table.")
        return []
    summaries = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        nic_text = cells[1].get_text(" ", strip=True)
        if "-" not in nic_text:
            continue
        nic_code, nic_description = nic_text.split("-", 1)
        link = cells[2].find("a", href=True)
        if link is None:
            continue
        count_text = re.sub(r"\D", "", link.get_text(" ", strip=True))
        detail_url = urljoin(page_url, link["href"])
        summaries.append(
            {
                "nic_code": nic_code.strip(),
                "nic_description": nic_description.strip(),
                "portal_count": int(count_text or "0"),
                "detail_url": detail_url,
                "collected_count": 0,
            }
        )
    return summaries


def parse_detail_listing(html, source_url, keyword, nic_code, nic_description, collected_on):
    soup = BeautifulSoup(html, "html.parser")
    for field in soup.find_all("input"):
        field.decompose()
    table = soup.find("table", id="example1")
    if table is None:
        table = _table_with_headers(soup, {"enterprise name", "address", "pin code"})
    if table is None:
        LOGGER.warning("Listing page had no enterprise table: %s", source_url.split("?")[0])
        return []

    header_cells = table.find("tr").find_all("th")
    headers = [cell.get_text(" ", strip=True) for cell in header_cells]
    indexes = _allowed_indexes(headers)
    heading_code, heading_description = _heading_nic(soup)
    records = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        values = {}
        for header, index in indexes.items():
            if index < len(cells):
                values[header] = cells[index].get_text(" ", strip=True)
        if not values.get("Enterprise Name"):
            continue
        values["NIC Code"] = nic_code or heading_code
        values["NIC Description"] = nic_description or heading_description
        values["Search Keyword"] = keyword
        values["Source URL"] = source_url
        values["Collection Date"] = collected_on
        records.append(clean_record(values))
    return records


def server_pagination_urls(html, page_url):
    """Return real next-page URLs. Browser-only DataTables controls are ignored."""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    current = urlparse(page_url)
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if href.startswith("javascript:"):
            match = re.search(r"Page\$(\d+)", href)
            if not match:
                continue
            found.append(f"{page_url}#page-{match.group(1)}")
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.hostname and parsed.hostname.casefold() != config.ALLOWED_HOST:
            continue
        if parsed.path.casefold() != current.path.casefold():
            continue
        query = parse_qs(parsed.query)
        if any(key.casefold() in {"page", "pageno", "pageindex"} for key in query):
            found.append(absolute)
    # DataTables page buttons are not anchors to another server page.
    return [url for url in found if not url.startswith(page_url + "#")]


def _allowed_indexes(headers):
    indexes = {}
    for index, header in enumerate(headers):
        key = re.sub(r"\s+", " ", header).strip().casefold()
        if any(marker in key for marker in config.BLOCKED_HEADER_MARKERS):
            continue
        if key in config.ALLOWED_DETAIL_HEADERS:
            indexes[config.ALLOWED_DETAIL_HEADERS[key]] = index
    return indexes


def _heading_nic(soup):
    strong = soup.find("strong")
    if strong is None:
        return "", ""
    text = strong.get_text(" ", strip=True)
    if "-" not in text:
        return "", text
    code, description = text.split("-", 1)
    return code.strip(), description.strip()


def _table_with_headers(soup, required_headers):
    for table in soup.find_all("table"):
        headers = {
            re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip().casefold()
            for cell in table.find_all("th")
        }
        if required_headers.issubset(headers):
            return table
    return None


def configure_logging():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(config.LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)
    LOGGER.addHandler(stream_handler)
