"""What we know about Square's Transactions CSV and Items Detail CSV.

Everything here is keyed by *normalised header name*, never by column position,
because Square adds, removes and reorders columns between accounts and over time.

The reference header rows below are the ones seen in real US seller exports
(54 and 33 columns). They have NOT been checked against an Australian export.
They are used by the demo generator and by tests; the parser itself never
depends on their order or on every column being present.
"""
from __future__ import annotations

import re

KIND_TRANSACTIONS = "transactions"
KIND_ITEMS = "items"
KIND_ITEMS_SUMMARY = "items_summary"
KIND_UNKNOWN = "unknown"


def norm(header: str) -> str:
    """'Gross Sales ' / 'gross_sales' / 'GROSS SALES' -> 'gross sales'."""
    return re.sub(r"[^a-z0-9]+", " ", header.lower()).strip()


# --- Reference header rows (order matters only for the demo generator) --------

TX_HEADERS = (
    "Date,Time,Time Zone,Gross Sales,Discounts,Service Charges,Net Sales,"
    "Gift Card Sales,Tax,Tip,Partial Refunds,Total Collected,Source,Card,"
    "Card Entry Methods,Cash,Square Gift Card,Other Tender,Other Tender Type,"
    "Tender Note,Fees,Net Total,Transaction ID,Payment ID,Card Brand,PAN Suffix,"
    "Device Name,Staff Name,Staff ID,Details,Description,Event Type,Location,"
    "Dining Option,Customer ID,Customer Name,Customer Reference ID,"
    "Device Nickname,Third Party Fees,Deposit ID,Deposit Date,Deposit Details,"
    "Fee Percentage Rate,Fee Fixed Rate,Refund Reason,Discount Name,"
    "Transaction Status,Cash App,Order Reference ID,Fulfillment Note,"
    "Free Processing Applied,Channel,Unattributed Tips,Table Info"
).split(",")

ITEM_HEADERS = (
    "Date,Time,Time Zone,Category,Item,Qty,Price Point Name,SKU,"
    "Modifiers Applied,Gross Sales,Discounts,Net Sales,Tax,Transaction ID,"
    "Payment ID,Device Name,Notes,Details,Event Type,Location,Dining Option,"
    "Customer ID,Customer Name,Customer Reference ID,Unit,Count,GTIN,"
    "Itemization Type,Commission,Employee,Fulfillment Note,Channel,Token"
).split(",")

# --- Field maps: normalised header -> (stored key, kind) ----------------------
# kinds: date, time (consumed by the reader), text, money (-> integer cents,
# keys end in _c), pct, qty.

TX_FIELDS = {
    "date": ("date", "date"),
    "time": ("time", "time"),
    "time zone": ("time_zone", "text"),
    "gross sales": ("gross_sales_c", "money"),
    "discounts": ("discounts_c", "money"),
    "service charges": ("service_charges_c", "money"),
    "net sales": ("net_sales_c", "money"),
    "gift card sales": ("gift_card_sales_c", "money"),
    "tax": ("tax_c", "money"),
    "tip": ("tip_c", "money"),
    "partial refunds": ("partial_refunds_c", "money"),
    "total collected": ("total_collected_c", "money"),
    "source": ("source", "text"),
    "card": ("card_c", "money"),
    "cash": ("cash_c", "money"),
    "square gift card": ("square_gift_card_c", "money"),
    "other tender": ("other_tender_c", "money"),
    "other tender type": ("other_tender_type", "text"),
    "fees": ("fees_c", "money"),
    "net total": ("net_total_c", "money"),
    "transaction id": ("transaction_id", "text"),
    "payment id": ("payment_id", "text"),
    "card brand": ("card_brand", "text"),
    "device name": ("device_name", "text"),
    "staff name": ("staff_name", "text"),
    "staff id": ("staff_id", "text"),
    "event type": ("event_type", "text"),
    "location": ("location", "text"),
    "dining option": ("dining_option", "text"),
    "third party fees": ("third_party_fees_c", "money"),
    "deposit id": ("deposit_id", "text"),
    "deposit date": ("deposit_date", "text"),
    "fee percentage rate": ("fee_pct", "pct"),
    "fee fixed rate": ("fee_fixed_c", "money"),
    "refund reason": ("refund_reason", "text"),
    "discount name": ("discount_name", "text"),
    "transaction status": ("transaction_status", "text"),
    "channel": ("channel", "text"),
    "unattributed tips": ("unattributed_tips_c", "money"),
    "table info": ("table_info", "text"),
}

ITEM_FIELDS = {
    "date": ("date", "date"),
    "time": ("time", "time"),
    "time zone": ("time_zone", "text"),
    "category": ("category", "text"),
    "item": ("item", "text"),
    "qty": ("qty", "qty"),
    "price point name": ("price_point_name", "text"),
    "sku": ("sku", "text"),
    "modifiers applied": ("modifiers_applied", "text"),
    "gross sales": ("gross_sales_c", "money"),
    "discounts": ("discounts_c", "money"),
    "net sales": ("net_sales_c", "money"),
    "tax": ("tax_c", "money"),
    "transaction id": ("transaction_id", "text"),
    "payment id": ("payment_id", "text"),
    "device name": ("device_name", "text"),
    "event type": ("event_type", "text"),
    "location": ("location", "text"),
    "dining option": ("dining_option", "text"),
    "unit": ("unit", "text"),
    "gtin": ("gtin", "text"),
    "itemization type": ("itemization_type", "text"),
    "commission": ("commission_c", "money"),
    "employee": ("employee", "text"),
    "channel": ("channel", "text"),
}

# Recognised but deliberately NEVER stored: customer-identifying data, card
# digits, and free-text note fields that routinely contain customer names.
PRIVATE = frozenset(
    {
        "customer id",
        "customer name",
        "customer reference id",
        "pan suffix",
        "tender note",
        "fulfillment note",
        "notes",
    }
)

# Recognised, harmless, not needed for analysis. Listed so they do not show up
# in the "unmapped columns" warning, which exists to surface *new* columns.
IGNORED = frozenset(
    {
        "details",
        "description",
        "card entry methods",
        "device nickname",
        "deposit details",
        "order reference id",
        "cash app",
        "free processing applied",
        "count",
        "token",
    }
)

# Minimum columns for a file to be usable at all.
REQUIRED = {
    KIND_TRANSACTIONS: {"date", "time", "transaction id", "gross sales"},
    KIND_ITEMS: {"date", "time", "transaction id", "item"},
}

FIELDS = {KIND_TRANSACTIONS: TX_FIELDS, KIND_ITEMS: ITEM_FIELDS}


def classify(normalised_headers: set[str]) -> str:
    """Decide which Square export a header row belongs to."""
    h = normalised_headers
    # Deliberately does NOT require "transaction id": a file that is clearly one
    # of the two exports but lacks it should fail with "missing required
    # column", which tells the user what is wrong, not "not recognised".
    if {"item name", "items sold"} <= h and "transaction id" not in h:
        return KIND_ITEMS_SUMMARY
    if "item" in h and ("qty" in h or "category" in h):
        return KIND_ITEMS
    if "gross sales" in h and (_TX_SIGNALS & h):
        return KIND_TRANSACTIONS
    return KIND_UNKNOWN


# Columns that appear in the Transactions export (tender, fees, payment) but not
# in Items Detail.
_TX_SIGNALS = frozenset(
    {"total collected", "net total", "fees", "tip", "card", "cash", "payment id", "card brand"}
)
