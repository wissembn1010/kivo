from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import frappe
from frappe import _
from frappe.model.document import Document


MONEY_PRECISION = Decimal("0.001")


class SupplierPayment(Document):
	def validate(self):
		self.validate_business_and_supplier()
		self.validate_amount()
		self.validate_payment_method()
		self.validate_source_purchase()
		self.validate_not_overpayment()
		self.validate_withholding()

	def validate_business_and_supplier(self):
		if not self.business or not frappe.db.exists("Business", self.business):
			frappe.throw(_("Business does not exist."))
		if not self.supplier or not frappe.db.exists("Supplier", self.supplier):
			frappe.throw(_("Supplier does not exist."))
		supplier_business = frappe.db.get_value("Supplier", self.supplier, "business")
		if supplier_business != self.business:
			frappe.throw(_("Supplier must belong to the same Business as the Supplier Payment."))

	def validate_amount(self):
		try:
			amount = Decimal(str(self.amount)).quantize(
				MONEY_PRECISION, rounding=ROUND_HALF_UP
			)
		except (InvalidOperation, TypeError, ValueError):
			frappe.throw(_("Amount must be a valid number."))
		if not amount.is_finite() or amount <= 0:
			frappe.throw(_("Amount must be greater than 0."))
		self.amount = amount

	def validate_withholding(self):
		amount = Decimal(str(self.amount)).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
		if not self.withholding_enabled:
			self.withholding_base = Decimal("0.000")
			self.withholding_rate = Decimal("0.00")
			self.withholding_amount = Decimal("0.000")
			self.net_paid = amount
			self.operation_amount_ht = Decimal("0.000")
			self.vat_rate = Decimal("0.00")
			self.vat_amount = Decimal("0.000")
			self.tej_operation_code = None
			return
		try:
			base = Decimal(str(self.withholding_base)).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
			rate = Decimal(str(self.withholding_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
			ht = Decimal(str(self.operation_amount_ht)).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
			vat_rate = Decimal(str(self.vat_rate or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
			vat = Decimal(str(self.vat_amount or 0)).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
		except (InvalidOperation, TypeError, ValueError):
			frappe.throw(_("Withholding values must be valid numbers."))
		if base < 0 or rate < 0 or rate > 100 or ht < 0 or vat < 0 or vat_rate < 0:
			frappe.throw(_("Withholding base, rate, HT and VAT values cannot be negative; rate cannot exceed 100%."))
		if base == 0 or rate == 0 or ht == 0:
			frappe.throw(_("Withholding Base, Rate and Operation Amount Excluding Tax must be greater than zero."))
		withheld = (base * rate / Decimal("100")).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
		net = (amount - withheld).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
		if withheld > amount or net < 0:
			frappe.throw(_("Withholding Amount cannot exceed the supplier debt settlement amount."))
		if not self.tej_operation_code or not frappe.db.exists("TEJ Operation Code", self.tej_operation_code):
			frappe.throw(_("A valid official TEJ Operation Code is required for withholding."))
		self.withholding_base, self.withholding_rate = base, rate
		self.withholding_amount, self.net_paid = withheld, net
		self.operation_amount_ht, self.vat_rate, self.vat_amount = ht, vat_rate, vat

	def validate_payment_method(self):
		allowed = {"CASH", "BANK_TRANSFER", "CHEQUE", "TRAITE", "OTHER"}
		if self.payment_method and self.payment_method not in allowed:
			frappe.throw(_("Payment Method is invalid."))

	def validate_source_purchase(self):
		if not self.source_purchase:
			return
		if not self.flags.get("creditflow_purchase_generated"):
			frappe.throw(_("Purchase-linked Supplier Payments can only be created by a Purchase."))

		purchase = frappe.get_doc("Purchase", self.source_purchase)
		if (
			purchase.docstatus == 2
			or purchase.business != self.business
			or purchase.supplier != self.supplier
			or Decimal(str(purchase.paid_amount or 0)) != Decimal(str(self.amount))
		):
			frappe.throw(_("Supplier Payment must match its source Purchase."))

		filters = {"source_purchase": self.source_purchase, "docstatus": ["!=", 2]}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		if frappe.db.exists("Supplier Payment", filters):
			frappe.throw(_("A Supplier Payment already exists for this Purchase."))

	def get_outstanding_debt(self):
		result = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(CASE WHEN direction = 'DEBIT' THEN amount
			                         WHEN direction = 'CREDIT' THEN -amount ELSE 0 END), 0)
			FROM `tabSupplier Transaction` FORCE INDEX (supplier_index)
			WHERE business = %s AND supplier = %s AND docstatus = 1
			FOR UPDATE
			""",
			(self.business, self.supplier),
		)
		return Decimal(str(result[0][0] or 0)).quantize(
			MONEY_PRECISION, rounding=ROUND_HALF_UP
		)

	def validate_not_overpayment(self):
		# Every payment for this supplier shares the same lock before the ledger read.
		frappe.db.sql("SELECT name FROM `tabSupplier` WHERE name = %s FOR UPDATE", self.supplier)
		outstanding = self.get_outstanding_debt()
		if Decimal(str(self.amount)) > outstanding:
			frappe.throw(
				_("Supplier Payment amount {0} exceeds the supplier's outstanding debt of {1}.").format(
					self.amount, outstanding
				)
			)

	def before_submit(self):
		frappe.db.sql("SELECT name FROM `tabSupplier` WHERE name = %s FOR UPDATE", self.supplier)
		self.validate()
		save_point = "supplier_payment_supplier_transaction"
		frappe.db.savepoint(save_point)
		try:
			self.create_supplier_transaction()
			if self.withholding_enabled:
				self.create_withholding_tax()
		except Exception:
			frappe.db.rollback(save_point=save_point)
			raise

	def create_supplier_transaction(self):
		transaction = frappe.get_doc(
			{
				"doctype": "Supplier Transaction",
				"business": self.business,
				"supplier": self.supplier,
				"transaction_type": "SUPPLIER_PAYMENT",
				"direction": "CREDIT",
				"amount": self.amount,
				"transaction_date": self.business_date,
				"source_supplier_payment": self.name,
			}
		)
		transaction.flags.creditflow_system_generated = True
		transaction.insert(ignore_permissions=True)
		transaction.submit()

	def create_withholding_tax(self):
		supplier = frappe.get_doc("Supplier", self.supplier)
		business = frappe.get_doc("Business", self.business)
		tax = frappe.get_doc({
			"doctype": "Withholding Tax", "business": self.business, "supplier": self.supplier,
			"source_supplier_payment": self.name, "source_purchase": self.source_purchase,
			"transaction_date": self.business_date, "settlement_amount": self.amount,
			"withholding_base": self.withholding_base, "withholding_rate": self.withholding_rate,
			"withholding_amount": self.withholding_amount, "net_paid": self.net_paid,
			"operation_amount_ht": self.operation_amount_ht, "vat_rate": self.vat_rate,
			"vat_amount": self.vat_amount, "tej_operation_code": self.tej_operation_code,
			"convention_non_double_taxation": self.convention_non_double_taxation,
			"tax_borne_by_payer": self.tax_borne_by_payer,
			"status": "ACTIVE", "certificate_state": "READY", "tej_state": "NOT_EXPORTED",
			"declarant_name": business.business_name, "declarant_tax_identifier": business.tax_identifier,
			"declarant_category": business.taxpayer_category,
			"declarant_address": business.address, "declarant_email": business.email, "declarant_phone": business.phone,
			"beneficiary_name": supplier.supplier_name, "beneficiary_identifier_type": supplier.tej_identifier_type,
			"beneficiary_identifier": supplier.tax_identifier, "beneficiary_category": supplier.taxpayer_category,
			"beneficiary_resident": supplier.resident, "beneficiary_birth_date": supplier.date_of_birth,
			"beneficiary_country_code": supplier.tej_country_code, "beneficiary_address": supplier.address,
			"beneficiary_email": supplier.email, "beneficiary_phone": supplier.phone,
			"beneficiary_activity": supplier.activity,
		})
		tax.flags.creditflow_system_generated = True
		tax.insert(ignore_permissions=True)
		tax.submit()

	def before_cancel(self):
		frappe.throw(
			_(
				"Posted Supplier Payments cannot be cancelled. They must be reversed using a reversal workflow."
			)
		)

	def before_delete(self):
		if self.docstatus == 1:
			frappe.throw(_("Submitted Supplier Payments cannot be deleted. Use a reversal workflow instead."))
