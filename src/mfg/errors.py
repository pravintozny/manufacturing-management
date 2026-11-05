"""Domain exceptions. Every rule violation raises a subclass of MfgError."""


class MfgError(Exception):
    """Base class for all domain errors."""


class ValidationError(MfgError):
    """Input is malformed or violates a business constraint."""


class NotFoundError(MfgError):
    """Referenced entity does not exist."""


class DuplicateError(MfgError):
    """Entity with the same natural key already exists."""


class StateError(MfgError):
    """Operation is not allowed in the entity's current state."""


class InsufficientStockError(MfgError):
    """Not enough free stock to satisfy a reservation or consumption."""


class DependencyError(MfgError):
    """BOM cycle, missing BOM, or unfinished prerequisite production order."""


class CapacityError(MfgError):
    """Work center has no capacity to schedule an operation."""
