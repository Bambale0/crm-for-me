"""Transfer positions by replacing unpaid invoices, retaining every old snapshot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.billing import BillingPeriod, InvoiceItem
from app.repositories.billing import ItemView
from app.services.billing_service import BillingService
from app.services.validation import lock_client
from app.utils.time import now_utc

TRANSFERABLE = ("DRAFT", "ISSUED")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass
class TransferPreview:
    target: BillingPeriod
    invoices: dict[int, BillingPeriod]
    items: dict[int, list[ItemView]]
    selected: list[ItemView]
    fingerprint: str

    @property
    def total(self) -> Decimal:
        return sum((i.amount for i in self.items[self.target.id] + self.selected), Decimal("0"))


class InvoiceTransferService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.billing = BillingService(session)

    async def _eligible(self, invoice: BillingPeriod) -> None:
        if invoice.status not in TRANSFERABLE or await self.billing.billing.paid_total(invoice.id):
            raise ValueError("Объединять можно только черновики и выставленные счета без оплат.")

    async def candidates(self, target_id: int) -> list[ItemView]:
        target = await self.billing.lock_period(target_id)
        await self._eligible(target)
        result = []
        for invoice in await self.billing.list_for_client(target.client_id):
            if invoice.id == target.id or invoice.status not in TRANSFERABLE:
                continue
            invoice = await self.billing.lock_period(invoice.id)
            if invoice.status not in TRANSFERABLE or await self.billing.billing.paid_total(
                invoice.id
            ):
                continue
            await self.billing.reconcile_draft(invoice)
            result.extend(await self.billing.items(invoice))
        return result

    async def preview(self, target_id: int, item_ids: list[int]) -> TransferPreview:
        target = await self.billing.lock_period(target_id)
        await self._eligible(target)
        selected_ids = set(item_ids)
        if not selected_ids:
            raise ValueError("Выберите хотя бы одну позицию из другого счёта.")
        selected = list(
            (
                await self.session.scalars(
                    select(InvoiceItem)
                    .where(InvoiceItem.id.in_(selected_ids))
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if len(selected) != len(selected_ids):
            raise ValueError("Позиции изменились. Откройте выбор заново.")
        invoices = {target.id: target}
        for pid in sorted({item.billing_period_id for item in selected}):
            invoice = await self.billing.get_period(pid)
            if invoice.client_id != target.client_id:
                raise ValueError("Счета должны принадлежать одному клиенту.")
            if pid == target.id:
                raise ValueError("Эта позиция уже находится в выбранном счёте.")
            invoice = await self.billing.lock_period(pid)
            await self._eligible(invoice)
            invoices[pid] = invoice
        items = {}
        for pid, invoice in invoices.items():
            await self.billing.reconcile_draft(invoice)
            items[pid] = await self.billing.items(invoice)
        current = {i.id: i for values in items.values() for i in values}
        if not selected_ids.issubset(current):
            raise ValueError("Позиции изменились. Откройте выбор заново.")
        fingerprint = digest(
            [
                [
                    pid,
                    invoices[pid].status,
                    [
                        [i.id, i.description, str(i.quantity), str(i.unit_price), str(i.amount)]
                        for i in items[pid]
                    ],
                ]
                for pid in sorted(items)
            ]
        )
        return TransferPreview(
            target, invoices, items, [current[i] for i in sorted(selected_ids)], fingerprint
        )

    async def transfer(
        self, target_id: int, item_ids: list[int], *, key: str, expected: str
    ) -> BillingPeriod:
        if not key or len(key) > 64:
            raise ValueError("Откройте объединение заново.")
        target = await self.billing.get_period(target_id)
        await lock_client(self.session, target.client_id)
        request = digest([target_id, sorted(set(item_ids))])
        existing = await self.session.scalar(
            select(BillingPeriod).where(BillingPeriod.transfer_key == key)
        )
        if existing is not None:
            if existing.transfer_request != request:
                raise ValueError("Эта операция уже использована для другого объединения.")
            return existing
        preview = await self.preview(target_id, item_ids)
        if preview.fingerprint != expected:
            raise ValueError("Счета изменились. Откройте объединение заново и проверьте сумму.")
        # All validation precedes writes. Free draft slots before inserting successors.
        statuses = {pid: invoice.status for pid, invoice in preview.invoices.items()}
        for invoice in preview.invoices.values():
            invoice.status = "SUPERSEDED"
            invoice.closed_at = now_utc()
        await self.session.flush()
        combined = await self._successor(
            target, statuses[target.id], preview.items[target.id] + preview.selected, key, request
        )
        for invoice in preview.invoices.values():
            invoice.replaced_by_id = combined.id
        chosen = set(item_ids)
        for pid, invoice in preview.invoices.items():
            if pid == target.id:
                continue
            remaining = [i for i in preview.items[pid] if i.id not in chosen]
            if remaining:
                await self._successor(invoice, statuses[pid], remaining)
        await self.session.flush()
        return combined

    async def _successor(self, original, status, items, key=None, request=None):
        invoice = BillingPeriod(
            client_id=original.client_id,
            year=original.year,
            month=original.month,
            status=status,
            issued_at=now_utc() if status == "ISSUED" else None,
            transfer_key=key,
            transfer_request=request,
        )
        await self.billing.billing.add(invoice)
        for item in items:
            await self.billing.billing.add_item(
                InvoiceItem(
                    billing_period_id=invoice.id,
                    source_type="TRANSFER",
                    source_id=item.id,
                    origin_item_id=item.id,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    amount=item.amount,
                )
            )
        return invoice

    async def replacements(self, period_id: int) -> list[BillingPeriod]:
        original = await self.billing.get_period(period_id)
        origin = aliased(InvoiceItem)
        ids = set(
            (
                await self.session.scalars(
                    select(InvoiceItem.billing_period_id)
                    .join(origin, InvoiceItem.origin_item_id == origin.id)
                    .where(origin.billing_period_id == period_id)
                )
            ).all()
        )
        if original.replaced_by_id is not None:
            ids.add(original.replaced_by_id)
        return list(
            (
                await self.session.scalars(
                    select(BillingPeriod)
                    .where(BillingPeriod.id.in_(ids))
                    .order_by(BillingPeriod.id)
                )
            ).all()
        )
