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
    return json.dumps({"clientId": cid, "loanCount": len(loans), "loans": loans}, ensure_ascii=False)

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
        "product": data.get("loanProductName"),
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
    return json.dumps(summary, ensure_ascii=False)

if __name__ == "__main__":
    app.run(transport="streamable-http")
