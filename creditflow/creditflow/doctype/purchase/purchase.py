from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document

from creditflow.uom import resolve_uom_snapshot


MONEY_PRECISION = Decimal("0.001")
PAYMENT_METHOD_MAP = {
	"CASH": "CASH",
	"BANK TRANSFER": "BANK_TRANSFER",
	"CHEQUE": "CHEQUE",
	"TRAITE": "TRAITE",
	"OTHER": "OTHER",
}


class Purchase(Document):
	def validate(self):
		self.validate_business_and_supplier()
		self.validate_items_and_calculate_total()
		self.validate_payment()

	def validate_business_and_supplier(self):
		if not self.business or not frappe.db.exists("Business", self.business):
			frappe.throw(_("Business does not exist."))

		if not self.supplier or not frappe.db.exists("Supplier", self.supplier):
			frappe.throw(_("Supplier does not exist."))

		supplier_business = frappe.db.get_value("Supplier", self.supplier, "business")
		if supplier_business != self.business:
			frappe.throw(_("Supplier must belong to the same Business as the Purchase."))

	def validate_items_and_calculate_total(self):
		if not self.items:
			frappe.throw(_("Purchase must contain at least one item."))

		total = Decimal("0")
		for row in self.items:
			product_business = frappe.db.get_value("Product", row.product, "business")
			if not product_business:
				frappe.throw(_("Product in row {0} does not exist.").format(row.idx))
			if product_business != self.business:
				frappe.throw(
					_(
						"Product in row {0} must belong to the same Business as the Purchase."
					).format(row.idx)
				)

			quantity = self.to_decimal(row.quantity, _("Quantity"), row.idx)
			(
				row.uom,
				row.conversion_factor,
				row.base_quantity,
			) = resolve_uom_snapshot(
				row.product,
				row.uom,
				quantity,
				row.idx,
			)
			unit_cost = self.to_decimal(row.unit_cost, _("Unit Cost"), row.idx)
			if quantity <= 0:
				frappe.throw(_("Quantity in row {0} must be greater than 0.").format(row.idx))
			if unit_cost < 0:
				frappe.throw(
					_("Unit Cost in row {0} must be greater than or equal to 0.").format(
						row.idx
					)
				)

			total += quantity * unit_cost

		self.total_amount = total.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)

	def validate_payment(self):
		self.payment_status = self.payment_status or "CREDIT"
		if self.payment_status not in {"CREDIT", "PAID", "PARTIAL"}:
			frappe.throw(_("Payment Status must be CREDIT, PAID, or PARTIAL."))

		total = Decimal(str(self.total_amount))
		if self.payment_status == "CREDIT":
			self.paid_amount = Decimal("0.000")
			self.payment_method = None
			self.payment_reference = None
			return

		if self.payment_method not in PAYMENT_METHOD_MAP:
			frappe.throw(_("Payment Method is required for PAID and PARTIAL purchases."))

		if self.payment_status == "PAID":
			self.paid_amount = total
			return

		paid_amount = self.to_decimal(self.paid_amount, _("Paid Amount"), 0).quantize(
			MONEY_PRECISION, rounding=ROUND_HALF_UP
		)
		if paid_amount <= 0:
			frappe.throw(_("Partial Paid Amount must be greater than 0."))
		if paid_amount >= total:
			frappe.throw(_("Partial Paid Amount must be less than Total Amount; use PAID instead."))
		self.paid_amount = paid_amount

	def before_submit(self):
		self.validate()
		save_point = "purchase_stock_supplier_and_payment_transactions"
		frappe.db.savepoint(save_point)
		try:
			self.create_stock_movements()
			self.create_supplier_transaction()
			self.create_automatic_supplier_payment()
		except Exception:
			frappe.db.rollback(save_point=save_point)
			raise

	def create_stock_movements(self):
		for row in self.items:
			movement = frappe.get_doc(
				{
					"doctype": "Stock Movement",
					"business": self.business,
					"product": row.product,
					"direction": "IN",
					"quantity": row.base_quantity,
					"movement_reason": "PURCHASE",
					"business_date": self.business_date,
					"source_purchase": self.name,
				}
			)
			movement.flags.creditflow_system_generated = True
			movement.insert(ignore_permissions=True)
			movement.submit()

	def create_supplier_transaction(self):
		transaction = frappe.get_doc(
			{
				"doctype": "Supplier Transaction",
				"business": self.business,
				"supplier": self.supplier,
				"transaction_type": "PURCHASE",
				"direction": "DEBIT",
				"amount": self.total_amount,
				"transaction_date": self.business_date,
				"source_purchase": self.name,
			}
		)
		transaction.flags.creditflow_system_generated = True
		transaction.insert(ignore_permissions=True)
		transaction.submit()

	def create_automatic_supplier_payment(self):
		if self.payment_status == "CREDIT":
			return

		existing = frappe.db.get_value(
			"Supplier Payment",
			{"source_purchase": self.name, "docstatus": ["!=", 2]},
			["name", "docstatus"],
			as_dict=True,
		)
		if existing:
			if existing.docstatus != 1:
				frappe.throw(_("The automatic Supplier Payment for this Purchase is not submitted."))
			self.automatic_supplier_payment = existing.name
			return

		payment = frappe.get_doc(
			{
				"doctype": "Supplier Payment",
				"business": self.business,
				"supplier": self.supplier,
				"business_date": self.business_date,
				"amount": self.paid_amount,
				"payment_method": PAYMENT_METHOD_MAP[self.payment_method],
				"reference": self.payment_reference,
				"source_purchase": self.name,
				"notes": _("Automatically created from Purchase {0}").format(self.name),
			}
		)
		payment.flags.creditflow_purchase_generated = True
		payment.insert(ignore_permissions=True)
		payment.submit()
		self.automatic_supplier_payment = payment.name

	def before_cancel(self):
		frappe.throw(
			_(
				"Posted Purchases cannot be cancelled. They must be reversed using a reversal workflow."
			)
		)

	@staticmethod
	def to_decimal(value, label, row_index):
		try:
			return Decimal(str(value))
		except (InvalidOperation, TypeError, ValueError):
			frappe.throw(_("{0} in row {1} must be a valid number.").format(label, row_index))
