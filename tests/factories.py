"""Hand-built ParseResult used by the repository round-trip tests.

Values here are fabricated: reserved-for-fiction phone prefixes and example.com
domains only. The full synthetic Kroger report arrives with the generator in M2;
this is the smallest thing that exercises every table.
"""

from unbagged.models import (
    Channel,
    Disclosure,
    DisclosureCategory,
    DisclosureStatus,
    FollowUpAction,
    FollowUpKind,
    Identity,
    IdType,
    Inference,
    InferenceOrigin,
    ParseResult,
    ParseWarning,
    Provenance,
    RequestMeta,
    Scale,
    Scope,
    Severity,
    SourceDocument,
    Transaction,
    TxnItem,
)

DOCUMENT = SourceDocument(
    original_filename="synthetic_report.pdf",
    sha256="a" * 64,
    media_type="application/pdf",
    page_count=48,
)


def sample_result(doc_id: int | None = 1) -> ParseResult:
    prov = Provenance(source_document_id=doc_id, page=3, locator="$.customer[0]")
    return ParseResult(
        request=RequestMeta(
            retailer_id="kroger",
            display_name="Kroger",
            report_reference="SYNTHETIC-0001",
            submitted_at="2026-01-04T00:00:00Z",
            received_at="2026-02-11T00:00:00Z",
            statute="CCPA",
            period_start="2024-02-01T00:00:00Z",
            period_end="2026-01-31T00:00:00Z",
            adapter_schema_version=1,
        ),
        identities=(
            # pii-scan: allow synthetic loyalty number, generated not observed
            Identity(IdType.LOYALTY_CARD, "0000000000001", Scope.INDIVIDUAL,
                     first_seen="2024-02-03T00:00:00Z", provenance=prov),
            Identity(IdType.HOUSEHOLD, "HH-000001", Scope.HOUSEHOLD, provenance=prov),
            Identity(IdType.EMAIL, "shopper@example.com", Scope.INDIVIDUAL, provenance=prov),
        ),
        transactions=(
            Transaction(
                occurred_at="2024-02-03T10:14:00Z",
                external_order_id="000123",
                store_code="00318",
                division_code="016",
                channel=Channel.IN_STORE,
                tender_type="CREDIT",
                total_pre_discount=41.20,
                provenance=Provenance(source_document_id=doc_id, page=5,
                                      locator="$.customer[0].basket[0]"),
                items=(
                    TxnItem("ORGANIC BANANAS", upc="00000004011", quantity=1.0,
                            retail_amt=2.49, loyalty_amt=0.30),
                    # The placeholder row Kroger emits constantly. It is kept, not
                    # filtered, so the count of real products stays honest.
                    # pii-scan: allow known placeholder UPC, not an identifier
                    TxnItem("UNKNOWN", upc="00010000080000", quantity=0.0,
                            retail_amt=0.0, loyalty_amt=0.0),
                ),
            ),
            Transaction(
                occurred_at="2024-02-19T17:02:00Z",
                external_order_id="000124",
                store_code="00318",
                channel=Channel.IN_STORE,
                total_pre_discount=-6.99,
                provenance=Provenance(source_document_id=doc_id, page=6,
                                      locator="$.customer[0].basket[1]"),
                # Returns are real transactions and are never filtered out.
                items=(TxnItem("RETURN WHOLE MILK", upc="00011110001", retail_amt=-6.99),),
            ),
        ),
        inferences=(
            Inference(
                label="Price sensitivity",
                value_raw="High",
                origin=InferenceOrigin.FIRST_PARTY_MODEL,
                scale=Scale.CATEGORICAL,
                subject=Scope.INDIVIDUAL,
                derivable_from_txns=True,
                provenance=prov,
            ),
            Inference(
                label="Estimated household income",
                value_raw="5 - $75,000-$99,999",
                origin=InferenceOrigin.APPENDED_THIRD_PARTY,
                value_num=5.0,
                scale=Scale.ORDINAL_1_7,
                subject=Scope.HOUSEHOLD,
                derivable_from_txns=False,
                provenance=prov,
            ),
        ),
        disclosures=tuple(
            Disclosure(
                category=c,
                status=(
                    DisclosureStatus.PROVIDED
                    if c is DisclosureCategory.SPECIFIC_PIECES
                    else DisclosureStatus.ABSENT
                ),
                evidence=(
                    "Section 1: Specific Pieces of Personal Information Collected"
                    if c is DisclosureCategory.SPECIFIC_PIECES
                    else None
                ),
                notes=(
                    None
                    if c is DisclosureCategory.SPECIFIC_PIECES
                    else "No corresponding section found in the report."
                ),
                provenance=prov,
            )
            for c in DisclosureCategory
        ),
        follow_ups=(
            FollowUpAction(
                kind=FollowUpKind.SUPPLEMENTAL_PERIOD,
                description=(
                    "Report covers 24 months and directs the requester to email the "
                    "privacy office for data back to 2022."
                ),
            ),
        ),
        warnings=(
            ParseWarning(
                message="Basket 37 had a malformed item list and was skipped.",
                severity=Severity.WARNING,
                locator="$.customer[0].basket[37]",
            ),
        ),
    )
