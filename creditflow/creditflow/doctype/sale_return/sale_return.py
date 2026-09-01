from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document

MONEY = Decimal("0.001")
REFUND_METHODS = {"CASH", "BANK_TRANSFER", "OTHER"}


def money(value):
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


class SaleReturn(Document):
    def validate(self):
        sale = self.get_original_sale()
        self.validate_source(sale)
        self.calculate_from_original(sale)
        if self.docstatus == 1:
            self.validate_refund_update()

    def get_original_sale(self):
        if not self.original_sale or not frappe.db.exists("Sale", self.original_sale):
            frappe.throw(_("Original Sale does not exist."))
        return frappe.get_doc("Sale", self.original_sale)

    def validate_source(self, sale):
        if sale.docstatus != 1:
            frappe.throw(_("Only a submitted Sale can be returned."))
        if self.business != sale.business:
            frappe.throw(_("Sale Return Business must match the Original Sale Business."))
        if frappe.db.exists("Sale Reversal", {"original_sale": sale.name, "docstatus": 1}):
            frappe.throw(_("A fully reversed Sale cannot be partially returned."))
        self.customer = sale.customer
        if not self.items:
            frappe.throw(_("Sale Return must contain at least one item."))

    def calculate_from_original(self, sale):
        originals = {row.name: row for row in sale.items}
        seen = set()
        totals = [Decimal("0"), Decimal("0"), Decimal("0")]
        for row in self.items:
            if row.original_sale_item in seen:
                frappe.throw(_("Original Sale Item {0} is duplicated in this return.").format(row.original_sale_item))
            seen.add(row.original_sale_item)
            original = originals.get(row.original_sale_item)
            if not original:
                frappe.throw(_("Return row {0} must reference an item from the Original Sale.").format(row.idx))
            try:
                qty = Decimal(str(row.quantity))
            except (InvalidOperation, TypeError, ValueError):
                frappe.throw(_("Return Quantity in row {0} must be valid.").format(row.idx))
            if not qty.is_finite() or qty <= 0:
                frappe.throw(_("Return Quantity in row {0} must be greater than 0.").format(row.idx))
            original_qty = Decimal(str(original.quantity))
            if qty > original_qty:
                frappe.throw(_("Return Quantity exceeds the quantity originally sold in row {0}.").format(row.idx))
            ratio = qty / original_qty
            row.product = original.product
            row.product_name = original.product_name
            row.uom = original.uom
            row.conversion_factor = original.conversion_factor
            row.base_quantity = qty * Decimal(str(original.conversion_factor))
            row.unit_price = original.unit_price
            row.tva_rate = original.tva_rate
            row.return_ht = money(Decimal(str(original.line_ht)) * ratio)
            row.return_tva = money(Decimal(str(original.tva_amount)) * ratio)
            row.return_ttc = money(row.return_ht + row.return_tva)
            totals[0] += money(row.return_ht); totals[1] += money(row.return_tva); totals[2] += money(row.return_ttc)
        self.return_ht, self.return_tva, self.return_ttc = map(money, totals)

    def before_submit(self):
        frappe.db.sql("SELECT name FROM `tabSale` WHERE name = %s FOR UPDATE", self.original_sale)
        self.validate()
        self.validate_cumulative_quantities()
        self.calculate_financial_effect()
        frappe.db.set_value("Sale Return", self.name, {
            "debt_reduction": self.debt_reduction, "refund_amount": self.refund_amount,
            "refund_status": self.refund_status, "refund_method": self.refund_method,
            "refund_reference": self.refund_reference, "refund_date": self.refund_date,
        }, update_modified=False)
        self.create_debt_credit()
        self.create_stock_movements()

    def validate_cumulative_quantities(self):
        for row in self.items:
            returned = frappe.db.sql("""
                SELECT COALESCE(SUM(item.quantity), 0)
                FROM `tabSale Return Item` item
                INNER JOIN `tabSale Return` parent ON parent.name = item.parent
                WHERE parent.docstatus = 1 AND parent.original_sale = %s
                  AND item.original_sale_item = %s
            """, (self.original_sale, row.original_sale_item))[0][0]
            original_qty = frappe.db.get_value("Sale Item", row.original_sale_item, "quantity")
            if Decimal(str(returned or 0)) + Decimal(str(row.quantity)) > Decimal(str(original_qty)):
                frappe.throw(_("Return Quantity exceeds the remaining returnable quantity for row {0}.").format(row.idx))

    def calculate_financial_effect(self):
        sale = self.get_original_sale()
        previous = frappe.db.sql("""
            SELECT COALESCE(SUM(debt_reduction), 0)
            FROM `tabSale Return` WHERE original_sale = %s AND docstatus = 1
        """, self.original_sale)[0][0]
        original_debt_remaining = max(Decimal("0"), Decimal(str(sale.outstanding_amount or 0)) - Decimal(str(previous or 0)))
        customer_debt = Decimal("0")
        if sale.customer:
            customer_debt = max(Decimal("0"), Decimal(str(frappe.get_doc("Customer", sale.customer).get_ledger_balance())))
        self.debt_reduction = money(min(Decimal(str(self.return_ttc)), original_debt_remaining, customer_debt))
        self.refund_amount = money(Decimal(str(self.return_ttc)) - Decimal(str(self.debt_reduction)))
        self.refund_status = "REFUND_DUE" if self.refund_amount > 0 else "NO_REFUND"
        self.refund_method = ""; self.refund_reference = ""; self.refund_date = None

    def create_debt_credit(self):
        if not self.debt_reduction:
            return
        txn = frappe.get_doc({"doctype":"Credit Transaction","business":self.business,"customer":self.customer,"transaction_type":"RETURN","direction":"CREDIT","amount":self.debt_reduction,"transaction_date":self.return_date,"source_sale_return":self.name})
        txn.flags.creditflow_system_generated = True
        txn.insert(ignore_permissions=True); txn.submit()

    def create_stock_movements(self):
        for row in self.items:
            movement = frappe.get_doc({"doctype":"Stock Movement","business":self.business,"product":row.product,"direction":"IN","quantity":row.base_quantity,"movement_reason":"CUSTOMER_RETURN","business_date":self.return_date,"source_sale_return":self.name,"source_sale_return_item":row.name})
            movement.flags.creditflow_system_generated = True
            movement.insert(ignore_permissions=True); movement.submit()

    def validate_refund_update(self):
        old = self.get_doc_before_save()
        if not old or old.docstatus != 1:
            return
        protected = ("business","original_sale","customer","return_date","reason","notes","return_ht","return_tva","return_ttc","debt_reduction","refund_amount")
        if any(self.get(field) != old.get(field) for field in protected) or self.items != old.items:
            frappe.throw(_("Submitted Sale Return economic data cannot be modified."))
        if self.refund_status == "REFUNDED":
            if self.refund_amount <= 0 or self.refund_method not in REFUND_METHODS or not self.refund_date:
                frappe.throw(_("Refund Method and Refund Date are required to mark a refund completed."))
        elif self.refund_status != old.refund_status:
            frappe.throw(_("Refund Status can only move from Refund Due to Refunded."))

    def before_cancel(self):
        frappe.throw(_("Submitted Sale Returns cannot be cancelled."))
