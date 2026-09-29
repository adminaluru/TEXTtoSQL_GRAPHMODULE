from golden.models import (
    GoldenQuestion,
)


GOLDEN_QUESTIONS: list[
    GoldenQuestion
] = [

    # ======================================================
    # GQ001
    #
    # Proven end-to-end SUM(attribute) capability.
    #
    # Verified through:
    #
    # - deterministic tests
    # - FastAPI
    # - real OpenAI
    # - Neo4j ontology
    # - PostgreSQL Golden Data
    # ======================================================

    GoldenQuestion(
        id="GQ001",

        question=(
            "How much has Blue Prism paid?"
        ),

        domain="Payments",

        capability="SUPPORTED",

        intent="aggregate",

        concepts=[
            "Customer",
            "PaymentReceipt",
        ],

        result_concept="Customer",

        measure_concept="PaymentReceipt",

        measure_attribute="payment amount",

        group_by_concepts=[],

        entities=[
            {
                "concept": "Customer",
                "attribute": "name",
                "value": "Blue Prism",
            }
        ],

        aggregation="SUM",

        notes=(
            "Proven end-to-end SUM(attribute) "
            "capability using dynamic ontology "
            "resolution and deterministic SQL."
        ),
    ),

    # ======================================================
    # GQ002
    #
    # Proven end-to-end COUNT(entity) capability.
    #
    # Important semantic behavior:
    #
    # COUNT Invoice entity
    #
    #     measure_concept   = Invoice
    #     measure_attribute = None
    #
    # QueryPlanBuilder resolves the physical entity
    # identity dynamically from Neo4j metadata.
    # ======================================================

    GoldenQuestion(
        id="GQ002",

        question=(
            "How many invoices does "
            "Blue Prism have?"
        ),

        domain="Billing",

        capability="SUPPORTED",

        intent="count",

        concepts=[
            "Customer",
            "Invoice",
        ],

        result_concept="Customer",

        measure_concept="Invoice",

        measure_attribute=None,

        group_by_concepts=[],

        entities=[
            {
                "concept": "Customer",
                "attribute": "name",
                "value": "Blue Prism",
            }
        ],

        aggregation="COUNT",

        notes=(
            "Proven end-to-end COUNT(entity) "
            "capability. Entity identity is "
            "resolved dynamically from Neo4j "
            "rather than modeled as a business "
            "measure attribute."
        ),
    ),

    # ======================================================
    # GQ003
    #
    # List/projection support is intentionally not
    # implemented in the current SQL compiler.
    # ======================================================

    GoldenQuestion(
        id="GQ003",

        question=(
            "Show invoices for Blue Prism."
        ),

        domain="Billing",

        capability="SAFE_REJECT",

        intent="list",

        concepts=[
            "Customer",
            "Invoice",
        ],

        result_concept="Invoice",

        measure_concept=None,

        measure_attribute=None,

        group_by_concepts=[],

        entities=[
            {
                "concept": "Customer",
                "attribute": "name",
                "value": "Blue Prism",
            }
        ],

        aggregation=None,

        notes=(
            "Must fail closed until explicit "
            "projection support exists."
        ),
    ),

    # ======================================================
    # GQ004
    #
    # GROUP BY capability.
    # ======================================================

    GoldenQuestion(
        id="GQ004",

        question=(
            "What is the total payment "
            "amount by customer?"
        ),

        domain="Payments",

        capability="SUPPORTED",

        intent="aggregate",

        concepts=[
            "Customer",
            "PaymentReceipt",
        ],

        result_concept="Customer",

        measure_concept="PaymentReceipt",

        measure_attribute="payment amount",

        group_by_concepts=[
            "Customer"
        ],

        entities=[],

        aggregation="SUM",

        notes=(
            "Proven end-to-end GROUP BY(entity) + "
            "SUM(attribute) capability. Entity identity "
            "and display columns are resolved dynamically "
            "from ontology metadata, with grouped results "
            "validated through PostgreSQL, Mock E2E, "
            "and real OpenAI /ask."
        ),
    ),

    # ======================================================
    # GQ005
    #
    # Claims branch / multi-hop semantic planning.
    # ======================================================

    GoldenQuestion(
        id="GQ005",

        question=(
            "What is the total claim amount "
            "for Blue Prism?"
        ),

        domain="ClaimsAndAdjustments",

        capability="SUPPORTED",

        intent="aggregate",

        concepts=[
            "Customer",
            "InvoiceClaim",
        ],

        result_concept="Customer",

        measure_concept="InvoiceClaim",

        measure_attribute="claim amount",

        group_by_concepts=[],

        entities=[
            {
                "concept": "Customer",
                "attribute": "name",
                "value": "Blue Prism",
            }
        ],

        aggregation="SUM",

        notes=(
            "Proven end-to-end multi-hop SUM(attribute) "
            "capability. GraphPlanner dynamically discovers "
            "and semantically validates the Invoice bridge "
            "between Customer and InvoiceClaim."
        ),
    ),

    # ======================================================
    # GQ006
    #
    # Collections branch.
    # ======================================================

    GoldenQuestion(
        id="GQ006",

        question=(
            "Which invoices have broken "
            "promises to pay?"
        ),

        domain="Collections",

        capability="SUPPORTED",

        intent="list",

        concepts=[
            "Invoice",
            "BrokenPromiseToPay",
        ],

        result_concept="Invoice",

        measure_concept=None,

        measure_attribute=None,

        group_by_concepts=[],

        entities=[],

        aggregation=None,

        notes=(
    "Proven end-to-end LIST entity capability "
    "using a correlated EXISTS / semi-join. "
    "BrokenPromiseToPay qualifies Invoice entities "
    "without multiplying outer result rows."
        ),
    ),

    # ======================================================
    # GQ007
    #
    # Adjustments branch.
    # ======================================================

    GoldenQuestion(
        id="GQ007",

        question=(
            "What is the total invoice "
            "adjustment amount?"
        ),

        domain="ClaimsAndAdjustments",

        capability="SUPPORTED",

        intent="aggregate",

        concepts=[
            "InvoiceAdjustment",
        ],

        result_concept="InvoiceAdjustment",

        measure_concept="InvoiceAdjustment",

        measure_attribute="adjustment amount",

        group_by_concepts=[],

        entities=[],

        aggregation="SUM",

        notes=(
            "Proven end-to-end single-concept "
            "SUM(attribute) capability for "
            "InvoiceAdjustment."
        ),
    ),

    # ======================================================
    # GQ008
    #
    # GST branch.
    # ======================================================

    GoldenQuestion(
        id="GQ008",

        question=(
            "Show GST information for invoices."
        ),

        domain="TaxAndGST",

        capability="SUPPORTED",

        intent="list",

        concepts=[
            "Invoice",
            "GSTInvoiceInfo",
        ],

        result_concept="GSTInvoiceInfo",

        measure_concept=None,

        measure_attribute=None,

        group_by_concepts=[],

        entities=[],

        aggregation=None,

       notes=(
    "Proven end-to-end LIST capability for "
    "GSTInvoiceInfo using ontology-driven bounded "
    "default projections and a confirmed Invoice "
    "relationship."
    ),
    ),

    # ======================================================
    # GQ009
    #
    # Multi-hop relationship.
    # ======================================================

    GoldenQuestion(
        id="GQ009",

        question=(
            "Which customers have invoices "
            "with claims?"
        ),

        domain="CrossDomain",

        capability="SUPPORTED",

        intent="list",

        concepts=[
            "Customer",
            "Invoice",
            "InvoiceClaim",
        ],

        result_concept="Customer",

        measure_concept=None,

        measure_attribute=None,

        group_by_concepts=[],

        entities=[],

        aggregation=None,

       notes=(
    "Proven end-to-end LIST capability using a "
    "semantically validated multi-hop correlated "
    "EXISTS path from Customer through Invoice to "
    "InvoiceClaim, preserving Customer result grain."
    ),
    ),

    # ======================================================
    # GQ010
    #
    # Branch/intersection reasoning.
    # ======================================================

    GoldenQuestion(
        id="GQ010",

        question=(
            "Which customers have both claims "
            "and broken promises?"
        ),

        domain="CrossDomain",

        capability="SUPPORTED",

        intent="list",

        concepts=[
            "Customer",
            "InvoiceClaim",
            "BrokenPromiseToPay",
        ],

        result_concept="Customer",

        measure_concept=None,

        measure_attribute=None,

        group_by_concepts=[],

        entities=[],

        aggregation=None,

       notes=(
    "Proven end-to-end branch/intersection LIST capability "
    "using two independently planned correlated EXISTS "
    "branches for InvoiceClaim and BrokenPromiseToPay "
    "at Customer result grain."
    ),
    ),
]