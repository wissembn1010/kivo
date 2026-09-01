import frappe
from frappe import _
from frappe.model.document import Document


class TEJExportBatch(Document):
	def validate(self):
		previous = self.get_doc_before_save()
		if previous and previous.status != "DRAFT":
			protected = ("business", "declaration_year", "declaration_month", "declaration_type", "correction_of")
			if any(self.get(field) != previous.get(field) for field in protected) or [r.as_dict() for r in self.records] != [r.as_dict() for r in previous.records]:
				frappe.throw(_("Generated TEJ batch fiscal content is immutable; create a corrective batch."))
		if self.declaration_month and not str(self.declaration_month).startswith(str(self.declaration_year)):
			frappe.throw(_("Declaration Month must belong to Declaration Year."))
		seen = set()
		for row in self.records:
			if row.withholding_tax in seen:
				frappe.throw(_("A withholding record cannot appear twice in one TEJ batch."))
			seen.add(row.withholding_tax)
			tax = frappe.db.get_value("Withholding Tax", row.withholding_tax, ["business", "transaction_date", "docstatus"], as_dict=True)
			if not tax or tax.business != self.business or tax.docstatus != 1:
				frappe.throw(_("Every TEJ batch record must be a submitted withholding of the same Business."))

	def before_delete(self):
		if self.status != "DRAFT":
			frappe.throw(_("Generated TEJ batches are audit records and cannot be deleted."))

	@frappe.whitelist()
	def generate_xml(self):
		from creditflow.tej import generate_batch_xml
		return generate_batch_xml(self.name)
