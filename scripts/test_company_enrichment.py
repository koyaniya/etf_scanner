from agents.company_enrichment_agent import (
    build_company_candidate,
    classify_with_llm,
    should_accept_classification,
)


holding = {
    "stock_ticker": "TEST",
    "cusip": "123456789",
    "security_name": "Example Space Technologies Inc",
    "holding_type": "COMPANY",
    "company_id": None,
}


llm_result = classify_with_llm(holding)

print("LLM RESULT")
print(llm_result)


if should_accept_classification(llm_result):
    if llm_result["entity_type"] == "COMPANY":
        candidate = build_company_candidate(
            holding=holding,
            llm_result=llm_result,
        )

        print("\nCOMPANY CANDIDATE")
        print(candidate)
    else:
        print(
            "\nNot a company:",
            llm_result["entity_type"],
        )
else:
    print("\nClassification confidence too low.")