"""Domain-level errors raised by services."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for business-logic errors."""


class NotFoundError(DomainError):
    pass


class AlreadyExistsError(DomainError):
    pass


class InvalidTransitionError(DomainError):
    pass


class InvalidAmountError(DomainError):
    pass


class InvoiceIssuedError(DomainError):
    """Raised when mutating financial data of an issued (frozen) invoice."""


class OverpaymentError(DomainError):
    pass
