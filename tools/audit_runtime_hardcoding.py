import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


EXCLUDED_DIRECTORIES = {
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "tests",
    "tools",
    "golden",
}


SUSPICIOUS_STRINGS = {
    "Blue Prism",

    "public.erp_customer_details",
    "public.erp_invoice_details",
    "public.erp_payment_receipt",

    "erp_customer_details",
    "erp_invoice_details",
    "erp_payment_receipt",

    "payment_amount",
    "customer_id",
    "invoice_id",

    "Customer",
    "Invoice",
    "PaymentReceipt",
}


class RuntimeHardcodingVisitor(ast.NodeVisitor):

    def __init__(
        self,
        source_lines: list[str],
    ) -> None:

        self.source_lines = source_lines

        self.findings = []

        self._docstring_nodes = set()

    # ==================================================
    # Docstring detection
    # ==================================================

    def visit_Module(
        self,
        node: ast.Module,
    ) -> None:

        self._capture_docstring(
            node
        )

        self.generic_visit(
            node
        )

    def visit_ClassDef(
        self,
        node: ast.ClassDef,
    ) -> None:

        self._capture_docstring(
            node
        )

        self.generic_visit(
            node
        )

    def visit_FunctionDef(
        self,
        node: ast.FunctionDef,
    ) -> None:

        self._capture_docstring(
            node
        )

        self.generic_visit(
            node
        )

    def visit_AsyncFunctionDef(
        self,
        node: ast.AsyncFunctionDef,
    ) -> None:

        self._capture_docstring(
            node
        )

        self.generic_visit(
            node
        )

    def _capture_docstring(
        self,
        node,
    ) -> None:

        if not getattr(
            node,
            "body",
            None,
        ):
            return

        first = node.body[0]

        if not isinstance(
            first,
            ast.Expr,
        ):
            return

        value = first.value

        if (
            isinstance(
                value,
                ast.Constant,
            )
            and isinstance(
                value.value,
                str,
            )
        ):

            self._docstring_nodes.add(
                value
            )

    # ==================================================
    # String literals
    # ==================================================

    def visit_Constant(
        self,
        node: ast.Constant,
    ) -> None:

        if node in self._docstring_nodes:
            return

        if not isinstance(
            node.value,
            str,
        ):
            return

        value = node.value

        for suspicious in (
            SUSPICIOUS_STRINGS
        ):

            if (
                suspicious.lower()
                in value.lower()
            ):

                self.findings.append(
                    {
                        "line":
                            getattr(
                                node,
                                "lineno",
                                0,
                            ),

                        "value":
                            suspicious,

                        "literal":
                            self._shorten(
                                value
                            ),
                    }
                )

    # ==================================================
    # Helpers
    # ==================================================

    @staticmethod
    def _shorten(
        value: str,
        limit: int = 160,
    ) -> str:

        value = (
            " ".join(
                value.split()
            )
        )

        if len(value) <= limit:
            return value

        return (
            value[:limit]
            + "..."
        )


def should_skip(
    path: Path,
) -> bool:

    return any(
        part in EXCLUDED_DIRECTORIES
        for part in path.parts
    )


def scan_file(
    path: Path,
) -> list[dict]:

    source = path.read_text(
        encoding="utf-8"
    )

    try:

        tree = ast.parse(
            source,
            filename=str(path),
        )

    except SyntaxError as exc:

        return [
            {
                "line":
                    exc.lineno or 0,

                "value":
                    "PARSE_ERROR",

                "literal":
                    str(exc),
            }
        ]

    visitor = (
        RuntimeHardcodingVisitor(
            source.splitlines()
        )
    )

    visitor.visit(
        tree
    )

    return visitor.findings


def main() -> None:

    results = []

    for path in PROJECT_ROOT.rglob(
        "*.py"
    ):

        if should_skip(
            path
        ):
            continue

        findings = scan_file(
            path
        )

        for finding in findings:

            results.append(
                {
                    "file":
                        str(
                            path.relative_to(
                                PROJECT_ROOT
                            )
                        ),

                    **finding,
                }
            )

    results.sort(
        key=lambda item: (
            item["file"],
            item["line"],
            item["value"],
        )
    )

    print(
        "=" * 100
    )

    print(
        "EXECUTABLE RUNTIME HARD-CODING AUDIT"
    )

    print(
        "=" * 100
    )

    if not results:

        print()
        print(
            "NO SUSPICIOUS EXECUTABLE "
            "RUNTIME LITERALS FOUND"
        )

    else:

        current_file = None

        for finding in results:

            if (
                finding["file"]
                != current_file
            ):

                current_file = (
                    finding["file"]
                )

                print()
                print(
                    f"[{current_file}]"
                )

                print(
                    "-" * 100
                )

            print(
                f"Line "
                f"{finding['line']:<5} "
                f"{finding['value']:<35} "
                f"{finding['literal']}"
            )

    print()
    print(
        "=" * 100
    )

    print(
        "Executable findings:",
        len(results),
    )

    print(
        "=" * 100
    )


if __name__ == "__main__":
    main()