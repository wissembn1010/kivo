import frappe
from frappe import _
from frappe.model.document import Document


class WithholdingTax(Document):
	def before_insert(self):
		if not self.flags.get("creditflow_system_generated"):
			frappe.throw(_("Withholding Tax records can only be created by a Supplier Payment."))

	def validate(self):
		if not self.source_supplier_payment:
			frappe.throw(_("Source Supplier Payment is required."))
		payment = frappe.get_doc("Supplier Payment", self.source_supplier_payment)
		if payment.business != self.business or payment.supplier != self.supplier:
			frappe.throw(_("Withholding Tax must match its source Supplier Payment."))

	def before_submit(self):
		if not self.certificate_reference:
			self.certificate_reference = self.name

	def before_cancel(self):
		frappe.throw(_("Withholding Tax records are append-only and cannot be cancelled."))

	def before_delete(self):
		if self.docstatus == 1:
			frappe.throw(_("Submitted Withholding Tax records cannot be deleted."))
