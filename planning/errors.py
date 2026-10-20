class UnsupportedPredicateError(ValueError):
    """A condition in the question cannot be applied faithfully yet.

    Never repaired: retrying could drop the condition and answer a broader
    question. The message is shown to the user, so it must not contain
    physical table or column names.
    """


class AmbiguousAttributeError(ValueError):
    """A phrase in the question matches several business attributes equally.

    Unlike UnsupportedPredicateError this may still be repaired (a rephrased
    interpretation can name one attribute). If repair does not resolve it,
    user_message asks the user to choose; it names business labels only,
    never physical table or column names. None when there is no sensible
    choice to offer (the generic message is shown).
    """

    def __init__(self, message: str, user_message: str | None, candidates: tuple = ()):
        super().__init__(message)
        self.user_message = user_message
        # The tied columns (ColumnMetadata), so a filter value can break the tie.
        self.candidates = tuple(candidates)
