"""Read each file in a separate process with a time limit.

A damaged or deliberately crafted file can make a reader library loop or
crash. In a separate process that is contained: after the time limit the
process is stopped, and the file is reported as too complex to process.
The API process itself is never affected.
"""
import multiprocessing

from document_ingestion import DocumentRejected, ExtractedDocument, IngestionLimits, extract_document

TOO_SLOW = "The file took too long to process."


def _extract_in_child(connection, data: bytes, filename: str, limits: IngestionLimits) -> None:
    try:
        connection.send(("done", extract_document(data, filename, limits)))
    except DocumentRejected as refusal:
        connection.send(("rejected", refusal.code, refusal.message))
    except Exception as exc:
        connection.send(("error", type(exc).__name__))
    finally:
        connection.close()


class IsolatedExtractor:
    def __init__(self, timeout_seconds: float = 120.0, target=_extract_in_child):
        """target runs in the child process; replaceable in tests only."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout = timeout_seconds
        self._target = target
        # "spawn" starts a clean interpreter on every platform.
        self._context = multiprocessing.get_context("spawn")

    def __call__(self, data: bytes, filename: str, limits: IngestionLimits) -> ExtractedDocument:
        receiver, sender = self._context.Pipe(duplex=False)
        child = self._context.Process(
            target=self._target, args=(sender, data, filename, limits), daemon=True)
        child.start()
        sender.close()
        try:
            if not receiver.poll(self._timeout):
                raise DocumentRejected("too_complex", TOO_SLOW)
            try:
                result = receiver.recv()
            except EOFError as exc:
                # The child died without answering (e.g. out of memory).
                raise RuntimeError("document reader process ended unexpectedly") from exc
        finally:
            if child.is_alive():
                child.terminate()
            child.join(5)
            receiver.close()

        if result[0] == "done":
            return result[1]
        if result[0] == "rejected":
            raise DocumentRejected(result[1], result[2])
        raise RuntimeError(f"document reader failed: {result[1]}")
