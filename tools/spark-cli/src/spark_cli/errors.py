class SparkError(Exception):
    """A failure the user can act on. `hint` says what to do about it."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
