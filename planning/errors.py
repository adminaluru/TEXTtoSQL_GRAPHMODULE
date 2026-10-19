class UnsupportedPredicateError(ValueError):
    """A condition in the question cannot be applied faithfully yet.

    Never repaired: retrying could drop the condition and answer a broader
    question. The message is shown to the user, so it must not contain
    physical table or column names.
    """
