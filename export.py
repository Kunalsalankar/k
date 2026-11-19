"""CSV and Excel export of the permitted public columns."""

import pandas as pd

import config


def records_to_frame(records):
    frame = pd.DataFrame(records)
    for column in config.EXPORT_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[config.EXPORT_COLUMNS]


def rows_to_frame(rows):
    records = [
        {
            "Enterprise Name": row["enterprise_name"],
            "Business Address": row["business_address"],
            "State": row["state_name"],
            "District": row["district_name"],
            "PIN Code": row["pin_code"],
            "NIC Code": row["nic_code"],
            "NIC Description": row["nic_description"],
            "Search Keyword": row["search_keyword"],
            "Source URL": row["source_url"],
            "Collection Date": row["collection_date"],
            "Verification Status": row["verification_status"],
        }
        for row in rows
    ]
    return records_to_frame(records)


def write_csv(frame, path=None):
    destination = path or config.CSV_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False, encoding="utf-8-sig")
    return destination


def write_excel(frame, path=None):
    destination = path or config.EXCEL_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_excel(destination, index=False, engine="openpyxl")
    return destination


def export_records(records, csv_path=None, excel_path=None):
    frame = records_to_frame(records)
    return write_csv(frame, csv_path), write_excel(frame, excel_path)


def append_lookup_export(result, path=None):
    """Append one Udyam lookup. The caller supplies a masked PAN, never the raw value."""
    destination = path or config.LOOKUP_EXCEL_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "Input PAN (masked)": result.get("input_pan_masked", ""),
        "Input Name": result.get("input_name", ""),
        "State": result.get("state", ""),
        "District": result.get("district", ""),
        "Udyam Registration Number": result.get("udyam_registration_number", ""),
        "Verification Status": result.get("verification_status", ""),
        "Verification Source": result.get("verification_source", ""),
        "Verification Date": result.get("verification_date", ""),
        "Remarks": result.get("remarks", ""),
    }
    frame = pd.DataFrame([row], columns=config.LOOKUP_COLUMNS)
    if destination.exists():
        existing = pd.read_excel(destination, dtype=str).fillna("")
        for column in config.LOOKUP_COLUMNS:
            if column not in existing.columns:
                existing[column] = ""
        existing = existing[config.LOOKUP_COLUMNS]
        frame = pd.concat([existing, frame], ignore_index=True)
    frame.to_excel(destination, index=False, engine="openpyxl")
    return destination
