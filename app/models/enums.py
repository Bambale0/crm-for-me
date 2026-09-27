"""Domain enums shared across models and services."""

from __future__ import annotations

import enum


class ProjectStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class TaskStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class BillingStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class InvoiceItemSource(str, enum.Enum):
    TASK = "TASK"
    RECURRING_CHARGE = "RECURRING_CHARGE"
    MANUAL = "MANUAL"
    ADJUSTMENT = "ADJUSTMENT"


class RecurringFrequency(str, enum.Enum):
    MONTHLY = "MONTHLY"


class ReminderStatus(str, enum.Enum):
    CANCELLED = "CANCELLED"
    PENDING = "PENDING"
    DONE = "DONE"


# Allowed status transitions for tasks.
TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.NEW: {TaskStatus.IN_PROGRESS, TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.DONE, TaskStatus.CANCELLED},
    TaskStatus.DONE: set(),
    TaskStatus.CANCELLED: set(),
}
