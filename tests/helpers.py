import csv
import io

TX_MIN = ["Date", "Time", "Transaction ID", "Payment ID", "Event Type",
          "Gross Sales", "Net Sales", "Fees", "Total Collected", "Customer Name"]
ITEM_MIN = ["Date", "Time", "Transaction ID", "Event Type", "Category", "Item",
            "Qty", "Gross Sales", "Net Sales", "Customer Name"]


def make_csv(headers, rows, encoding="utf-8", bom=False) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    w.writerows(rows)
    data = buf.getvalue().encode(encoding)
    return b"\xef\xbb\xbf" + data if bom else data


def tx_row(txid="T1", pay="P1", event="Payment", gross="$10.00", net="$10.00",
           fees="-$0.20", total="$10.00", cust="", date="2026-10-02", time="18:30:00"):
    return [date, time, txid, pay, event, gross, net, fees, total, cust]


def item_row(txid="T1", event="Payment", cat="Wine", item="Shiraz", qty="1",
             gross="$10.00", net="$10.00", cust="", date="2026-10-02", time="18:30:00"):
    return [date, time, txid, event, cat, item, qty, gross, net, cust]
