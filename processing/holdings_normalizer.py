def classify_holding(stock_ticker, cusip, security_name):
    ticker = (stock_ticker or "").strip().upper()
    cusip = (cusip or "").strip().upper()
    name = (security_name or "").strip().upper()

    # Cash position
    if ticker == "CASH&OTHER" or cusip == "CASH&OTHER" or name == "CASH & OTHER":
        return "CASH"

    # Currency positions
    if cusip.startswith("CASH"):
        return "CURRENCY"

    # Funds / money market funds
    if ticker.endswith("XX") or "FUND" in name:
        return "FUND"

    # Normal company/equity holding
    if ticker and cusip:
        return "COMPANY"

    # Unknown / needs review
    return "OTHER"