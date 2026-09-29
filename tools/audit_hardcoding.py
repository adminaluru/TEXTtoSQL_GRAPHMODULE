import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


EXCLUDED_DIRECTORIES = {
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
}


# ---------------------------------------------------------
# Values that may indicate BUSINESS / PHYSICAL hard-coding
# ---------------------------------------------------------

SUSPICIOUS_STRINGS = {

    # Demo/customer-specific
    "Blue Prism",

    # Physical tables from current vertical slice
    "public.erp_customer_details",
    "public.erp_invoice_details",
    "public.erp_payment_receipt",

    "erp_customer_details",
    "erp_invoice_details",
    "erp_payment_receipt",

    # Physical columns from vertical slice
    "payment_amount",
    "customer_id",
    "invoice_id",

    # Business concepts used heavily in initial slice
    "Customer",
    "Invoice",
    "PaymentReceipt",
}


# ---------------------------------------------------------
# Infrastructure snapshot constants
# ---------------------------------------------------------

SUSPICIOUS_NUMBERS = {
    40,
    65,
    625,
}


def should_skip(path: Path) -> bool:

    return any(
        part in EXCLUDED_DIRECTORIES
        for part in path.parts
    )


def classify_file(path: Path) -> str:

    parts = set(path.parts)

    if "tests" in parts:
        return "TEST"

    if "tools" in parts:
        return "TOOL"

    if "golden" in parts:
        return "GOLDEN"

    return "RUNTIME"


def scan_python_file(path: Path):

    try:

        source = path.read_text(
            encoding="utf-8"
        )

        tree = ast.parse(
            source,
            filename=str(path),
        )

    except Exception as exc:

        return [
            {
                "file": str(path.relative_to(PROJECT_ROOT)),
                "line": 0,
                "type": "PARSE_ERROR",
                "value": str(exc),
                "classification": classify_file(path),
            }
        ]

    findings = []

    for node in ast.walk(tree):

        # -------------------------------------------------
        # String literals
        # -------------------------------------------------

        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        ):

            value = node.value

            for suspicious in SUSPICIOUS_STRINGS:

                if suspicious.lower() in value.lower():

                    findings.append(
                        {
                            "file": str(
                                path.relative_to(
                                    PROJECT_ROOT
                                )
                            ),

                            "line": getattr(
                                node,
                                "lineno",
                                0,
                            ),

                            "type":
                                "STRING",

                            "value":
                                suspicious,

                            "classification":
                                classify_file(path),
                        }
                    )

        # -------------------------------------------------
        # Numeric snapshot constants
        # -------------------------------------------------

        if (
            isinstance(node, ast.Constant)
            and isinstance(
                node.value,
                int,
            )
            and not isinstance(
                node.value,
                bool,
            )
        ):

            if (
                node.value
                in SUSPICIOUS_NUMBERS
            ):

                findings.append(
                    {
                        "file": str(
                            path.relative_to(
                                PROJECT_ROOT
                            )
                        ),

                        "line": getattr(
                            node,
                            "lineno",
                            0,
                        ),

                        "type":
                            "NUMBER",

                        "value":
                            str(node.value),

                        "classification":
                            classify_file(path),
                    }
                )

    return findings


def main():

    findings = []

    for path in PROJECT_ROOT.rglob(
        "*.py"
    ):

        if should_skip(path):
            continue

        findings.extend(
            scan_python_file(
                path
            )
        )

    findings.sort(
        key=lambda item: (
            item["classification"],
            item["file"],
            item["line"],
        )
    )

    print("=" * 90)
    print("RESEARCH AGENT HARD-CODING AUDIT")
    print("=" * 90)

    current_classification = None

    for finding in findings:

        classification = (
            finding[
                "classification"
            ]
        )

        if (
            classification
            != current_classification
        ):

            current_classification = (
                classification
            )

            print()
            print(
                f"[{classification}]"
            )
            print("-" * 90)

        print(
            f"{finding['file']}:"
            f"{finding['line']}  "
            f"{finding['type']:<8}  "
            f"{finding['value']}"
        )

    print()
    print("=" * 90)

    runtime_findings = [
        finding
        for finding in findings
        if finding[
            "classification"
        ] == "RUNTIME"
    ]

    print(
        "Runtime findings:",
        len(runtime_findings),
    )

    print(
        "Test/tool/golden findings:",
        len(findings)
        - len(runtime_findings),
    )

    print("=" * 90)


if __name__ == "__main__":
    main()