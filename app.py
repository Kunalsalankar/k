"""Streamlit view of enterprises already stored locally."""

from io import BytesIO
from pathlib import Path

import streamlit as st

import config
import database
import export


def excel_bytes(frame):
    buffer = BytesIO()
    frame.to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


st.set_page_config(page_title="TCI MSME Collector", layout="wide")
st.title("TCI MSME business data")
st.caption(
    "Enterprise rows are the records saved from the public listing. "
    "The portal-reported MSME count is the figure shown on the Udyam search "
    "page for the same NIC codes. Those two numbers measure different things."
)

if not Path(config.DATABASE_PATH).exists():
    st.info("No local database yet. Run main.py to collect a public search.")
    st.stop()

connection = database.connect()
database.initialise(connection)
try:
    states = database.distinct_values(connection, "state_name")
    districts = database.distinct_values(connection, "district_name")
    keywords = database.distinct_values(connection, "search_keyword")
    nic_codes = database.distinct_values(connection, "nic_code")

    with st.sidebar:
        st.header("Filter stored records")
        state = st.selectbox("State", ["All"] + states)
        district = st.selectbox("District", ["All"] + districts)
        keyword = st.selectbox("Business activity", ["All"] + keywords)
        nic_code = st.selectbox("NIC code", ["All"] + nic_codes)
        text = st.text_input("Search name, address, or NIC description")

    state_filter = "" if state == "All" else state
    district_filter = "" if district == "All" else district
    keyword_filter = "" if keyword == "All" else keyword
    nic_filter = "" if nic_code == "All" else nic_code

    rows = database.fetch_enterprises(
        connection,
        state=state_filter,
        district=district_filter,
        keyword=keyword_filter,
        nic_code=nic_filter,
        text=text.strip(),
    )
    summaries = database.fetch_nic_summaries(
        connection,
        state=state_filter,
        district=district_filter,
        keyword=keyword_filter,
        nic_code=nic_filter,
    )
    frame = export.rows_to_frame(rows)
    portal_count = sum(item["portal_count"] for item in summaries)

    metric_columns = st.columns(3)
    metric_columns[0].metric("Enterprise rows collected", int(len(frame)))
    metric_columns[1].metric("Portal-reported MSME count", int(portal_count))
    metric_columns[2].metric("NIC activities in this filter", len(summaries))

    if text.strip():
        st.info(
            "The free-text search filters collected rows only. "
            "The portal-reported count still refers to the full NIC listing."
        )
    elif portal_count != len(frame):
        st.warning(
            f"The portal reported {portal_count} MSME units. "
            f"{len(frame)} enterprise rows are stored for this filter."
        )

    if summaries:
        st.subheader("Portal count and collected rows by NIC")
        st.dataframe(
            [
                {
                    "NIC Code": item["nic_code"],
                    "NIC Description": item["nic_description"],
                    "Portal-reported MSME count": item["portal_count"],
                    "Collected rows": item["collected_count"],
                }
                for item in summaries
            ],
            width="stretch",
            hide_index=True,
        )

    if frame.empty:
        st.write("No stored rows match this filter.")
    else:
        left, right = st.columns(2)
        state_counts = (
            frame["State"].value_counts().rename_axis("State").reset_index(name="Enterprises")
        )
        district_counts = (
            frame["District"]
            .value_counts()
            .rename_axis("District")
            .reset_index(name="Enterprises")
        )
        activity_counts = (
            frame.groupby(["Search Keyword", "NIC Code"], dropna=False)
            .size()
            .reset_index(name="Enterprises")
        )
        with left:
            st.subheader("State-wise collected rows")
            st.bar_chart(state_counts, x="State", y="Enterprises")
        with right:
            st.subheader("District-wise collected rows")
            st.bar_chart(district_counts, x="District", y="Enterprises")
        st.subheader("Business activity")
        st.dataframe(activity_counts, width="stretch", hide_index=True)
        st.subheader("Collected enterprises")
        st.dataframe(frame, width="stretch", hide_index=True)
        st.download_button(
            "Download Excel",
            data=excel_bytes(frame),
            file_name="tci_msme_enterprises.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
finally:
    connection.close()
