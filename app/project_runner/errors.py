"""Errors that mean "tell the model and let it try again", shared across the runner."""


class ReplyFormatError(ValueError):
    """A final reply or plan the runner cannot use; returned to the model, not a verdict on the work."""
