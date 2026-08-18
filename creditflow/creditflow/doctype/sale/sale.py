from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document


MONEY_PRECISION = Decimal("0.001")
VALID_SALE_MODES = {"CASH", "CREDIT", "MIXED"}
VALID_PAYMENT_METHODS = {"CASH", "BANK_TRANSFER", "CHEQUE", "OTHER"}


class Sale(Document):
	def validate(self):
		self.validate_items_and_calculate_total()
		self.validate_business_and_customer()
		self.validate_sale_payment_terms()

	def validate_business_and_customer(self):
		if not self.business or not frappe.db.exists("Business", self.business):
			frappe.throw(_("Business does not exist."))

		if self.get_effective_sale_mode() == "CASH" and not self.customer:
			return

		if not self.customer or not frappe.db.exists("Customer", self.customer):
			frappe.throw(_("Customer does not exist."))

		customer_business = frappe.db.get_value("Customer", self.customer, "business")
		if customer_business != self.business:
			frappe.throw(_("Customer must belong to the same Business as the Sale."))

	def before_submit(self):
		self.validate()
		required_by_product = self.get_required_quantity_by_product()
		self.lock_products(required_by_product)
		self.validate_available_stock(required_by_product)
		self.create_credit_transaction()
		self.create_stock_movements()

	def before_cancel(self):
		frappe.throw(
			_("Posted Sales cannot be cancelled. They must be reversed using a reversal workflow.")
		)

	def validate_items_and_calculate_total(self):
		if not self.items:
			frappe.throw(_("Sale must contain at least one item."))

		total = Decimal("0")

		for row in self.items:
			product_business = frappe.db.get_value("Product", row.product, "business")
			if not product_business:
				frappe.throw(_("Product in row {0} does not exist.").format(row.idx))
			if product_business != self.business:
				frappe.throw(
					_("Product in row {0} must belong to the same Business as the Sale.").format(
						row.idx
					)
				)

			quantity = self.to_decimal(row.quantity, _("Quantity"), row.idx)
			unit_price = self.get_unit_price(row, row.idx)
			row.unit_price = unit_price.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)

			if quantity <= 0:
				frappe.throw(_("Quantity in row {0} must be greater than 0.").format(row.idx))
			if unit_price < 0:
				frappe.throw(
					_("Unit Price in row {0} must be greater than or equal to 0.").format(row.idx)
				)

			total += quantity * unit_price

		self.total_amount = total.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)

	def validate_sale_payment_terms(self):
		sale_mode = self.get_effective_sale_mode()
		if sale_mode not in VALID_SALE_MODES:
			frappe.throw(_("Sale Mode is not supported."))

		total = Decimal(str(self.total_amount or 0)).quantize(
			MONEY_PRECISION, rounding=ROUND_HALF_UP
		)
		amount_paid = self.to_money_decimal(self.amount_paid or 0, _("Amount Paid"))

		if sale_mode == "CASH":
			if amount_paid != total:
				frappe.throw(_("Cash Sales must have Amount Paid equal to Total Amount."))
			if self.payment_method and self.payment_method != "CASH":
				frappe.throw(_("Cash Sales must use the CASH payment method."))
			self.amount_paid = total
			self.outstanding_amount = Decimal("0").quantize(
				MONEY_PRECISION, rounding=ROUND_HALF_UP
			)
			if amount_paid > 0 and not self.payment_method:
				self.payment_method = "CASH"
		elif sale_mode == "CREDIT":
			if amount_paid != 0:
				frappe.throw(_("Credit Sales must have Amount Paid equal to 0."))
			if not self.customer:
				frappe.throw(_("Customer is required for Credit Sales."))
			self.amount_paid = Decimal("0").quantize(
				MONEY_PRECISION, rounding=ROUND_HALF_UP
			)
			self.outstanding_amount = total
		else:
			if not self.customer:
				frappe.throw(_("Customer is required for Mixed Sales."))
			if amount_paid <= 0:
				frappe.throw(_("Mixed Sales must have Amount Paid greater than 0."))
			if amount_paid >= total:
				frappe.throw(_("Mixed Sales must have Amount Paid less than Total Amount."))
			if amount_paid < 0:
				frappe.throw(_("Amount Paid must be greater than or equal to 0."))
			if not self.payment_method:
				frappe.throw(_("Payment Method is required for Mixed Sales."))
			self.amount_paid = amount_paid
			self.outstanding_amount = (total - amount_paid).quantize(
				MONEY_PRECISION, rounding=ROUND_HALF_UP
			)

		if amount_paid > total:
			frappe.throw(_("Amount Paid cannot be greater than Total Amount."))

		if self.amount_paid and self.payment_method and self.payment_method not in VALID_PAYMENT_METHODS:
			frappe.throw(_("Payment Method is not supported."))

		if self.amount_paid and self.amount_paid > 0 and not self.payment_method:
			self.payment_method = "CASH" if sale_mode == "CASH" else self.payment_method

		if not self.amount_paid:
			self.payment_reference = self.payment_reference or ""

		self.sale_mode = sale_mode
		self.amount_paid = self.to_money_decimal(self.amount_paid or 0, _("Amount Paid"))
		self.outstanding_amount = self.to_money_decimal(
			self.outstanding_amount or 0, _("Outstanding Amount")
		)

	def get_required_quantity_by_product(self):
		required_by_product = {}
		for row in self.items:
			required_by_product.setdefault(row.product, Decimal("0"))
			required_by_product[row.product] += self.to_decimal(
				row.quantity, _("Quantity"), row.idx
			)
		return required_by_product

	def lock_products(self, required_by_product):
		for product in sorted(required_by_product):
			frappe.db.sql(
				"SELECT name FROM `tabProduct` WHERE name = %s FOR UPDATE",
				product,
			)

	def validate_available_stock(self, required_by_product):
		for product, required_quantity in required_by_product.items():
			available_quantity = self.get_available_stock(product)
			if available_quantity < required_quantity:
				frappe.throw(
					_(
						"Insufficient stock for Product {0}: available {1}, required {2}."
					).format(product, available_quantity, required_quantity)
				)

	def get_available_stock(self, product):
		result = frappe.db.sql(
			"""
			SELECT COALESCE(
				SUM(
					CASE
						WHEN direction = 'IN' THEN quantity
						WHEN direction = 'OUT' THEN -quantity
						ELSE 0
					END
				),
				0
			)
			FROM `tabStock Movement`
			WHERE business = %s
			  AND product = %s
			  AND docstatus = 1
			""",
			(self.business, product),
		)
		return Decimal(str(result[0][0] or 0))

	def create_credit_transaction(self):
		unpaid_amount = self.get_unpaid_amount()
		if unpaid_amount <= 0:
			return
		transaction = frappe.get_doc(
			{
				"doctype": "Credit Transaction",
				"business": self.business,
				"customer": self.customer,
				"transaction_type": "SALE",
				"direction": "DEBIT",
				"amount": unpaid_amount,
				"transaction_date": self.business_date,
				"source_sale": self.name,
			}
		)
		transaction.flags.creditflow_system_generated = True
		transaction.insert(ignore_permissions=True)
		transaction.submit()

	def create_stock_movements(self):
		for row in self.items:
			movement = frappe.get_doc(
				{
					"doctype": "Stock Movement",
					"business": self.business,
					"product": row.product,
					"direction": "OUT",
					"quantity": row.quantity,
					"movement_reason": "SALE",
					"business_date": self.business_date,
					"source_sale": self.name,
				}
			)
			movement.flags.creditflow_system_generated = True
			movement.insert(ignore_permissions=True)
			movement.submit()

	@staticmethod
	def to_decimal(value, label, row_index):
		try:
			return Decimal(str(value))
		except (InvalidOperation, TypeError, ValueError):
			frappe.throw(_("{0} in row {1} must be a valid number.").format(label, row_index))

	def to_money_decimal(self, value, label):
		try:
			return Decimal(str(value)).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
		except (InvalidOperation, TypeError, ValueError):
			frappe.throw(_("{0} must be a valid number.").format(label))

	def get_effective_sale_mode(self):
		return (self.sale_mode or "CREDIT").strip().upper()

	def get_unit_price(self, row, row_index):
		if row.unit_price not in (None, ""):
			return self.to_decimal(row.unit_price, _("Unit Price"), row_index)

		unit_price = frappe.db.get_value("Product", row.product, "selling_price")
		return self.to_decimal(unit_price or 0, _("Unit Price"), row_index)

	def get_unpaid_amount(self):
		total = Decimal(str(self.total_amount or 0))
		amount_paid = Decimal(str(self.amount_paid or 0))
		return (total - amount_paid).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
