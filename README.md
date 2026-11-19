# TCI MSME business data collector

Internal tool for collecting enterprise rows that the official Udyam portal
already shows on its public Search MSME Units pages. It is for TCI lead
generation, supplier discovery, CRM, and SRM. It does not register enterprises
and it does not log in.

## What the portal actually does

Inspection of https://udyamregistration.gov.in/searchregistration.aspx on
18 November 2026:

- `robots.txt` is not published. The server returns its normal missing-page
  message.
- The search form is server-rendered ASP.NET. There is no CAPTCHA on the
  public search or on the enterprise listing.
- Choosing a state requires a form postback. The district list is in that
  response. District codes are read from the live page.
- Search returns an HTML table of NIC codes, descriptions, and an MSME count.
  The count links to `SearchRegDetail.aspx?cod=<NIC>&ty=2&si=<state>&di=<district>`.
- The listing is one HTML table, `table#example1`. Columns on the page are
  serial number, enterprise name, address, state, district, PIN, social
  category, gender, and a business-query button.
- DataTables paginates that table in the browser. The rows are already in the
  first response, so the collector does not request extra pages unless the
  HTML contains a real next-page link.
- The business-query button is backed by a hidden email field. Social
  category, gender, email, mobile, GSTIN, PAN, and Udyam registration numbers
  are not read or stored.

The portal certificate is issued by eMudhra and is not in this workstation's
trust store. `config.py` sets `VERIFY_SSL = False` for that reason. The client
still stops if a response comes from any host other than
`udyamregistration.gov.in`. Set `VERIFY_SSL = True` on a machine that trusts
the issuer.

## Collected columns

Enterprise name, business address, state, district, PIN code, NIC code, NIC
description, search keyword, source URL, collection date, and a local
verification status.

Verification status is a format check of the public row:

- `Format checked` — name, address, and a 6-digit PIN are present
- `Missing value` — name or address is blank
- `Invalid PIN` — the PIN is not a 6-digit code

It is not a government verification of the enterprise.

The portal MSME count and the number of stored rows are kept separate. A count
on the search page is not the same thing as a row in the listing table.

## Setup

```bash
cd TCI_MSME_Scraper
python -m pip install -r requirements.txt
```

## Collect one search

```bash
python main.py --state Maharashtra --district Pune --keyword Bamboo
```

An optional NIC code limits the run to one activity:

```bash
python main.py --state Maharashtra --district Pune --keyword Bamboo --nic 16294
```

The command waits 2 seconds between portal requests, retries timeouts and
HTTP 502/503/504 with backoff, and stops without further retries on HTTP 401,
403, 429, or an access challenge.

Output:

- `data/companies.csv`
- `data/companies.xlsx`
- `database/msme_data.db`
- `logs/scraper.log`

The database file is `msme_data.db`. The earlier folder sketch had a space in
that filename.

## Dashboard

```bash
streamlit run app.py
```

The dashboard reads the local database. It shows collected rows, the
portal-reported count, state and district charts, an activity breakdown, and
an Excel download for the current filter.

## Tests

```bash
python -m unittest tests.test_cleaning
```

## Access limits

The collector uses the same public form a browser uses. It does not bypass
CAPTCHA, OTP, or login. If the portal starts refusing automated requests,
stop the run and do not increase the retry count.
