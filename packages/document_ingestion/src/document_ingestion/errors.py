class DocumentRejected(ValueError):
    """
    The file cannot be ingested.

    `code` is stable for programs; `message` is written for the person who
    uploaded the file and never contains file content or internal details.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def __reduce__(self):
        # Keeps both fields when the error crosses a process boundary.
        return (DocumentRejected, (self.code, self.message))


DAMAGED = "The file could not be read. It may be damaged."
