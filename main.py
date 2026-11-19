"""Command-line collection and Udyam registration lookup."""

import argparse
import sys

import config
import database
import export
from scraper import UdyamAccessError, UdyamRequestError, UdyamScraper, configure_logging
from udyam_lookup import UdyamLookup, format_lookup_report


def build_parser():
    parser = argparse.ArgumentParser(
        description="Collect public MSME listing rows from the Udyam portal."
    )
    parser.add_argument("--state", required=True, help="State name, for example Maharashtra")
    parser.add_argument(
        "--district",
        default="All",
        help="District name, for example Pune. Use All for every district.",
    )
    parser.add_argument("--keyword", required=True, help="Product or activity, for example Bamboo")
    parser.add_argument("--nic", default="", help="Optional 5-digit NIC code")
    return parser


def build_lookup_parser():
    parser = argparse.ArgumentParser(
        prog="main.py lookup-udyam",
        description=(
            "Look up an Udyam Registration Number from the official portal. "
            "The command does not guess a number when the portal cannot confirm one."
        ),
    )
    parser.add_argument("--pan", required=True, help="PAN of the enterprise or owner")
    parser.add_argument("--name", required=True, help="Enterprise name or owner name")
    parser.add_argument("--state", required=True, help="State name, for example Haryana")
    parser.add_argument("--district", required=True, help="District name, for example Gurugram")
    return parser


def run_lookup(argv):
    args = build_lookup_parser().parse_args(argv)
    configure_logging()
    lookup = UdyamLookup()
    try:
        result = lookup.lookup(
            pan=args.pan,
            name=args.name,
            state=args.state,
            district=args.district,
        )
    finally:
        lookup.close()
    print(format_lookup_report(result))
    return 0


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "lookup-udyam":
        return run_lookup(arguments[1:])
    args = build_parser().parse_args(arguments)
    configure_logging()
    scraper = UdyamScraper()
    try:
        result = scraper.collect(
            state=args.state,
            district=args.district,
            keyword=args.keyword,
            nic_code=args.nic,
        )
    except UdyamAccessError as exc:
        print(exc)
        return 2
    except (LookupError, ValueError, TimeoutError, UdyamRequestError) as exc:
        print(exc)
        return 1
    finally:
        scraper.close()

    connection = database.connect()
    try:
        database.initialise(connection)
        saved = database.save_collection(
            connection,
            keyword=result["keyword"],
            state_name=result["state"],
            district_name=result["district"],
            nic_filter=result["nic_filter"],
            summaries=result["summaries"],
            records=result["records"],
        )
        rows = database.fetch_enterprises(connection)
        frame = export.rows_to_frame(rows)
        csv_path, excel_path = export.write_csv(frame), export.write_excel(frame)
    finally:
        connection.close()

    print(f"State: {result['state']}")
    print(f"District: {result['district']}")
    print(f"Keyword: {result['keyword']}")
    print(f"Portal-reported MSME count: {saved['portal_msme_count']}")
    print(f"Enterprise rows collected this run: {len(result['records'])}")
    print(f"New rows stored: {saved['inserted']}")
    print(f"CSV: {csv_path}")
    print(f"Excel: {excel_path}")
    print(f"SQLite: {config.DATABASE_PATH}")
    for summary in result["summaries"]:
        print(
            f"NIC {summary['nic_code']}: portal {summary['portal_count']}, "
            f"collected {summary['collected_count']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
