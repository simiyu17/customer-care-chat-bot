import os
import re
import json
import httpx
from mcp.server.fastmcp import FastMCP

# Served over streamable HTTP at http://<host>:5000/mcp so the agent container can reach it
app = FastMCP("Fineract Loans API Server", host="0.0.0.0", port=5000)

API_URL = os.getenv("API_URL", "https://localhost:8443/fineract-provider/api/v1").rstrip("/")
BEARER_TOKEN = os.getenv("BEARER_TOKEN", "mock_secret_token")
TENANT_ID = os.getenv("FINERACT_TENANT_ID", "default")
# Local Fineract usually runs with a self-signed certificate; set FINERACT_VERIFY_SSL=false for that
VERIFY_SSL = os.getenv("FINERACT_VERIFY_SSL", "true").lower() not in ("false", "0", "no")

def _date(value):
    """Fineract returns dates as [yyyy, m, d] (optionally with h, m, s); convert to ISO strings."""
    if isinstance(value, list) and len(value) >= 3:
        y, m, d = value[:3]
        if len(value) >= 6:
            return f"{y:04d}-{m:02d}-{d:02d} {value[3]:02d}:{value[4]:02d}:{value[5]:02d}"
        return f"{y:04d}-{m:02d}-{d:02d}"
    return value

def _compact(data: dict) -> dict:
    """Drop keys with no value so the model only sees populated fields."""
    return {k: v for k, v in data.items() if v is not None and v != [] and v != {}}

def _valid_id(value: str) -> str | None:
    value = str(value).strip()
    return value if re.fullmatch(r"\d+", value) else None

async def _fineract_get(path: str, params: dict | None = None) -> tuple[dict | None, str | None]:
    """GET a Fineract endpoint. Returns (json, None) on success or (None, error message)."""
    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Fineract-Platform-TenantId": TENANT_ID,
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(verify=VERIFY_SSL, timeout=15.0) as client:
        try:
            response = await client.get(f"{API_URL}{path}", headers=headers, params=params)
        except Exception as e:
            return None, f"Failed to connect to the loans service: {str(e)}"

    if response.status_code == 200:
        return response.json(), None
    if response.status_code == 404:
        return None, "NOT_FOUND"
    if response.status_code in (401, 403):
        return None, f"The loans service rejected our credentials (HTTP {response.status_code}). The access token may have expired."
    return None, f"Loans service error: HTTP {response.status_code}."

def _amount(value) -> str:
    """12000 -> '12,000'; keeps decimals only when present; missing -> '–'."""
    if value is None:
        return "–"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}" if value % 1 else f"{int(value):,}"
    return str(value)

def _cell(value) -> str:
    if value is None or value == "":
        return "–"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).replace("|", "\\|").replace("\n", " ")

def _md_table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)

def _respond(display: str, data: dict) -> str:
    """Tool output: a ready-to-show markdown block plus the full data for follow-up questions."""
    return f"<display>\n{display}\n</display>\n\n<data>\n{json.dumps(data, ensure_ascii=False)}\n</data>"

def _render_client_loans(client_id: str, loans: list[dict]) -> str:
    if not loans:
        return f"**Client {client_id}** has no loan accounts."
    currencies = {l["currency"] for l in loans if l.get("currency")}
    title = f"**Client {client_id} — {len(loans)} loan{'s' if len(loans) != 1 else ''}**"
    if len(currencies) == 1:
        title += f" (amounts in {currencies.pop()})"
    rows = [
        [
            l.get("loanId"),
            l.get("product"),
            l.get("status"),
            _amount(l.get("approvedPrincipal") or l.get("proposedPrincipal") or l.get("originalLoan")),
            _amount(l.get("amountPaid")),
            l.get("expectedMaturityOn"),
        ]
        for l in loans
    ]
    return title + "\n\n" + _md_table(["Loan ID", "Product", "Status", "Principal", "Paid", "Maturity"], rows)

def _installment_status(p: dict) -> str:
    if p.get("complete"):
        return f"Paid ({p['paidOn']})" if p.get("paidOn") else "Paid"
    if p.get("overdue"):
        return f"Overdue ({_amount(p['overdue'])})"
    return "Upcoming"

def _render_loan_details(loan: dict) -> str:
    totals = loan.get("totals", {})
    delinquency = loan.get("delinquency", {})
    delivery = loan.get("delivery", {})

    title = f"**Loan {loan.get('loanId')}**"
    if loan.get("product"):
        title += f" — {loan['product']}"
    if loan.get("clientName"):
        title += f"  \nClient: {loan['clientName']}"
        if loan.get("clientAccountNo"):
            title += f" (account {loan['clientAccountNo']})"
    if loan.get("currency"):
        title += f"  \nAmounts in {loan['currency']}"

    overview = [
        ["Status", loan.get("status")],
        ["Loan type", loan.get("loanType")],
        ["Account no.", loan.get("accountNo")],
        ["Principal", _amount(loan.get("principal"))],
        ["Net disbursed", _amount(loan.get("netDisbursalAmount"))],
        ["Expected repayment", _amount(totals.get("expectedRepayment"))],
        ["Repaid", _amount(totals.get("repaid"))],
        ["Outstanding", _amount(totals.get("outstanding"))],
        ["Paid in advance", _amount(totals.get("paidInAdvance"))],
        ["In arrears", loan.get("inArrears")],
        ["Days past due", delinquency.get("pastDueDays")],
        ["Term", f"{loan['loanTermInDays']} days" if loan.get("loanTermInDays") else None],
    ]
    if delivery:
        overview.append(["Delivery", " · ".join(str(v) for v in (delivery.get("status"), delivery.get("groupName"), delivery.get("dateDelivered")) if v)])
    # Three tables: loan details, repayment schedule, transactions
    sections = [title, "**Loan details**\n\n" + _md_table(["Detail", "Value"], [r for r in overview if r[1] is not None])]

    schedule = loan.get("repaymentSchedule") or []
    if schedule:
        rows = [
            [
                p.get("period") or "Upfront",
                p.get("dueDate"),
                _amount(p.get("due")),
                _amount(p.get("paid")),
                _amount(p.get("outstanding")),
                _installment_status(p),
            ]
            for p in schedule
        ]
        sections.append("**Repayment schedule**\n\n" + _md_table(["#", "Due date", "Due", "Paid", "Outstanding", "Status"], rows))

    transactions = loan.get("transactions") or []
    if transactions:
        rows = [
            [
                t.get("date"),
                t.get("type") + (" (reversed)" if t.get("reversed") else "") if t.get("type") else None,
                _amount(t.get("amount")),
                _amount(t.get("balanceAfter")),
                " · ".join(str(v) for v in (t.get("paymentType"), t.get("receiptNumber") and f"receipt {t['receiptNumber']}") if v)
                or t.get("description"),
            ]
            for t in transactions
        ]
        sections.append("**Transactions**\n\n" + _md_table(["Date", "Type", "Amount", "Balance after", "Payment"], rows))

    return "\n\n".join(sections)

def _summarize_loan_account(account: dict) -> dict:
    timeline = account.get("timeline") or {}
    return _compact({
        "loanId": account.get("id"),
        "accountNo": account.get("accountNo"),
        "product": account.get("productName"),
        "loanType": (account.get("loanType") or {}).get("name"),
        "status": (account.get("status") or {}).get("value"),
        "inArrears": account.get("inArrears"),
        "loanCycle": account.get("loanCycle"),
        "currency": (account.get("currency") or {}).get("code"),
        "proposedPrincipal": account.get("proposedPrincipal"),
        "approvedPrincipal": account.get("approvedPrincipal"),
        "originalLoan": account.get("originalLoan"),
        "amountPaid": account.get("amountPaid"),
        "loanBalance": account.get("loanBalance"),
        "submittedOn": _date(timeline.get("submittedOnDate")),
        "approvedOn": _date(timeline.get("approvedOnDate")),
        "expectedDisbursementOn": _date(timeline.get("expectedDisbursementDate")),
        "disbursedOn": _date(timeline.get("actualDisbursementDate")),
        "expectedMaturityOn": _date(timeline.get("expectedMaturityDate")),
        "closedOn": _date(timeline.get("closedOnDate")),
        "applicationCycleStatus": (account.get("applicationCycle") or {}).get("status"),
    })

@app.tool()
async def get_client_loans(client_id: str) -> str:
    """Lists all loan accounts for a client (status, principal, amount paid, key dates) by Fineract client ID."""
    cid = _valid_id(client_id)
    if not cid:
        return f"'{client_id}' is not a valid client ID. Client IDs are numeric."

    data, error = await _fineract_get(f"/clients/{cid}/accounts")
    if error == "NOT_FOUND":
        return f"No client was found with ID {cid}."
    if error:
        return error

    loans = [_summarize_loan_account(a) for a in data.get("loanAccounts") or []]
    return _respond(_render_client_loans(cid, loans), {"clientId": cid, "loanCount": len(loans), "loans": loans})

@app.tool()
async def get_loan_details(loan_id: str) -> str:
    """Fetches full details of one loan by loan ID: balances, repayment schedule, transactions and delinquency."""
    lid = _valid_id(loan_id)
    if not lid:
        return f"'{loan_id}' is not a valid loan ID. Loan IDs are numeric."

    data, error = await _fineract_get(f"/loans/{lid}", params={"associations": "all"})
    if error == "NOT_FOUND":
        return f"No loan was found with ID {lid}."
    if error:
        return error

    schedule = data.get("repaymentSchedule") or {}
    installments = [
        _compact({
            "period": p.get("period"),
            "dueDate": _date(p.get("dueDate")),
            "due": p.get("totalDueForPeriod"),
            "paid": p.get("totalPaidForPeriod"),
            "outstanding": p.get("totalOutstandingForPeriod"),
            "overdue": p.get("totalOverdue"),
            "complete": p.get("complete"),
            "paidOn": _date(p.get("obligationsMetOnDate")),
        })
        # Period-0 rows mark disbursement; keep only those that carry an amount (e.g. a down payment)
        for p in schedule.get("periods") or []
        if p.get("period", 0) > 0 or p.get("totalDueForPeriod")
    ]

    transactions = []
    for t in data.get("transactions") or []:
        tx_type = t.get("type") or {}
        if tx_type.get("accrual"):
            continue  # Internal accounting entries, not money movements
        payment = t.get("paymentDetailData") or {}
        transactions.append(_compact({
            "id": t.get("id"),
            "date": _date(t.get("date")),
            "type": tx_type.get("value"),
            "amount": t.get("amount"),
            "principalPortion": t.get("principalPortion"),
            "interestPortion": t.get("interestPortion"),
            "feeChargesPortion": t.get("feeChargesPortion"),
            "penaltyChargesPortion": t.get("penaltyChargesPortion"),
            "balanceAfter": t.get("outstandingLoanBalance"),
            "paymentType": (payment.get("paymentType") or {}).get("name"),
            "receiptNumber": payment.get("receiptNumber") or None,
            "description": (t.get("transfer") or {}).get("transferDescription"),
            "reversed": t.get("manuallyReversed") or None,
        }))

    delinquent = data.get("delinquent") or {}
    delivery = (data.get("loanDeliveryDetails") or {}).get("delivery") or {}

    summary = _compact({
        "loanId": data.get("id"),
        "accountNo": data.get("accountNo") or schedule.get("loanAccountNo"),
        "clientName": data.get("clientName"),
        "clientAccountNo": data.get("clientAccountNo"),
        "product": data.get("loanProductName") or data.get("productName"),
        "loanType": (data.get("loanType") or {}).get("name"),
        "status": (data.get("status") or {}).get("value"),
        "currency": (data.get("currency") or {}).get("code"),
        "principal": data.get("principal"),
        "approvedPrincipal": data.get("approvedPrincipal"),
        "netDisbursalAmount": data.get("netDisbursalAmount"),
        "inArrears": data.get("inArrears"),
        "isNPA": data.get("isNPA"),
        "loanTermInDays": schedule.get("loanTermInDays"),
        "totals": _compact({
            "expectedRepayment": schedule.get("totalRepaymentExpected"),
            "repaid": schedule.get("totalRepayment"),
            "outstanding": schedule.get("totalOutstanding"),
            "interestCharged": schedule.get("totalInterestCharged"),
            "feesCharged": schedule.get("totalFeeChargesCharged"),
            "penaltiesCharged": schedule.get("totalPenaltyChargesCharged"),
            "waived": schedule.get("totalWaived"),
            "writtenOff": schedule.get("totalWrittenOff"),
            "paidInAdvance": schedule.get("totalPaidInAdvance"),
            "paidLate": schedule.get("totalPaidLate"),
        }),
        "delinquency": _compact({
            "pastDueDays": delinquent.get("pastDueDays"),
            "delinquentDays": delinquent.get("delinquentDays"),
            "delinquentAmount": delinquent.get("delinquentAmount"),
        }),
        "delivery": _compact({
            "groupName": delivery.get("groupName"),
            "status": delivery.get("deliveryStatus"),
            "dateDelivered": delivery.get("dateDelivered"),
        }),
        "repaymentSchedule": installments,
        "transactions": transactions,
    })
    return _respond(_render_loan_details(summary), summary)

if __name__ == "__main__":
    app.run(transport="streamable-http")
