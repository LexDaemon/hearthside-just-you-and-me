"""User-facing collection errors, shared by interchange and scheduling."""


class CollectionError(ValueError):
    """Invalid card, collection, settings or package data."""


class UnsupportedNoteType(CollectionError):
    """The APKG contains note templates we cannot render safely."""


class MissingZstandard(CollectionError):
    """The declared Anki compression dependency is absent."""


class MissingScheduler(CollectionError):
    """The declared scheduling dependency is absent."""
