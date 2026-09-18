from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.patches.v16_0.seed_tej_references import execute as seed_tej_references
from creditflow.tej import generate_batch_xml, serialize_batch, to_millimes, validate_xml


class IntegrationTestWithholdingTEJ(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:8]
		self.business = frappe.get_doc({"doctype": "Business", "business_name": f"TEJ Business {suffix}", "tax_identifier": "1234567A", "taxpayer_category": "PM", "address": "1 Tunis", "email": f"business-{suffix}@example.com", "phone": "71111222"}).insert()
		self.supplier = frappe.get_doc({"doctype": "Supplier", "business": self.business.name, "supplier_name": f"TEJ Supplier {suffix}", "phone": "22111222", "email": f"supplier-{suffix}@example.com", "address": "2 Tunis", "tej_identifier_type": "1", "tax_identifier": "7654321B", "taxpayer_category": "PM", "resident": 1, "activity": "Commerce"}).insert()
		# Use the shipped official catalogue, independent of this site's patch history.
		seed_tej_references()
		self.operation_code = "RS1_000001"
		self.assertTrue(frappe.db.exists("TEJ Operation Code", self.operation_code))

	def tearDown(self):
		frappe.set_user("Administrator")

	def opening_debt(self, amount):
		tx = frappe.get_doc({"doctype": "Supplier Transaction", "business": self.business.name, "supplier": self.supplier.name, "transaction_type": "OPENING_BALANCE", "direction": "DEBIT", "amount": amount, "transaction_date": "2026-08-01"}).insert()
		tx.submit()
		return tx

	def balance(self):
		value = frappe.db.sql("""select coalesce(sum(case when direction='DEBIT' then amount else -amount end),0) from `tabSupplier Transaction` where business=%s and supplier=%s and docstatus=1""", (self.business.name, self.supplier.name))[0][0]
		return Decimal(str(value)).quantize(Decimal("0.001"))

	def payment(self, amount="1000", base="1000", rate="1", withholding=True):
		values = {"doctype": "Supplier Payment", "business": self.business.name, "supplier": self.supplier.name, "business_date": "2026-08-15", "amount": amount, "payment_method": "BANK_TRANSFER"}
		if withholding:
			values.update({"withholding_enabled": 1, "withholding_base": base, "withholding_rate": rate, "operation_amount_ht": amount, "vat_rate": 0, "vat_amount": 0, "tej_operation_code": self.operation_code})
		return frappe.get_doc(values)

	def test_01_decimal_calculation_and_millimes(self):
		self.opening_debt("2000")
		payment = self.payment().insert()
		self.assertEqual(Decimal(str(payment.withholding_amount)), Decimal("10.000"))
		self.assertEqual(Decimal(str(payment.net_paid)), Decimal("990.000"))
		payment2 = self.payment(amount="2000", base="2000", rate="3").insert()
		self.assertEqual(Decimal(str(payment2.withholding_amount)), Decimal("60.000"))
		self.assertEqual(Decimal(str(payment2.net_paid)), Decimal("1940.000"))
		self.assertEqual([to_millimes(v) for v in ("1.000", "10.250", "0.001", "1000.000")], [1000, 10250, 1, 1000000])

	def test_02_full_partial_and_ordinary_supplier_balance(self):
		self.opening_debt("1000")
		payment = self.payment().insert(); payment.submit()
		self.assertEqual(self.balance(), Decimal("0.000"))
		ledger = frappe.db.get_value("Supplier Transaction", {"source_supplier_payment": payment.name}, "amount")
		self.assertEqual(Decimal(str(ledger)), Decimal("1000.000"))
		tax = frappe.get_doc("Withholding Tax", {"source_supplier_payment": payment.name})
		self.assertEqual((Decimal(str(tax.withholding_amount)), Decimal(str(tax.net_paid))), (Decimal("10.000"), Decimal("990.000")))

		other_supplier = frappe.get_doc({"doctype": "Supplier", "business": self.business.name, "supplier_name": f"Partial {uuid4().hex[:6]}"}).insert()
		self.supplier = other_supplier
		self.opening_debt("2000")
		partial = self.payment().insert(); partial.submit()
		self.assertEqual(self.balance(), Decimal("1000.000"))
		ordinary = self.payment(amount="100", withholding=False).insert(); ordinary.submit()
		self.assertEqual(Decimal(str(ordinary.net_paid)), Decimal("100.000"))
		self.assertFalse(frappe.db.exists("Withholding Tax", {"source_supplier_payment": ordinary.name}))

	def test_03_reversal_restores_debt_and_preserves_fiscal_history(self):
		self.opening_debt("1000")
		payment = self.payment().insert(); payment.submit()
		reversal = frappe.get_doc({"doctype": "Supplier Payment Reversal", "business": self.business.name, "original_supplier_payment": payment.name, "business_date": "2026-08-20", "reason": "Test"}).insert(); reversal.submit()
		self.assertEqual(self.balance(), Decimal("1000.000"))
		tax = frappe.get_doc("Withholding Tax", {"source_supplier_payment": payment.name})
		self.assertEqual((tax.status, tax.reversed_by, tax.docstatus), ("REVERSED", reversal.name, 1))

	def test_supplier_payments_today_counts_net_cash_after_withholding(self):
		from creditflow import dashboard

		self.opening_debt("1000")
		baseline = dashboard.supplier_payments_today()["value"]
		payment = self.payment()
		payment.business_date = frappe.utils.today()
		payment.insert().submit()
		self.assertEqual(dashboard.supplier_payments_today()["value"], baseline + Decimal("990.000"))

		reversal = frappe.get_doc({"doctype": "Supplier Payment Reversal",
			"business": self.business.name, "original_supplier_payment": payment.name,
			"business_date": frappe.utils.today(), "reason": "Ledger correction without refund"}).insert()
		reversal.submit()
		self.assertEqual(dashboard.supplier_payments_today()["value"], baseline + Decimal("990.000"))

	def test_04_validation_rejects_invalid_values_and_cross_tenant(self):
		self.opening_debt("1000")
		for field, value in (("withholding_rate", "-1"), ("withholding_base", "-1"), ("withholding_rate", "101")):
			doc = self.payment(); doc.set(field, value)
			with self.assertRaises(frappe.ValidationError): doc.insert()
		with self.assertRaises(frappe.ValidationError): self.payment(amount="5", base="1000", rate="1").insert()
		doc = self.payment(); doc.tej_operation_code = "NOT_OFFICIAL"
		with self.assertRaises(frappe.ValidationError): doc.insert()
		business_b = frappe.get_doc({"doctype": "Business", "business_name": f"Other {uuid4().hex[:6]}"}).insert()
		supplier_b = frappe.get_doc({"doctype": "Supplier", "business": business_b.name, "supplier_name": "Other"}).insert()
		doc = self.payment(); doc.supplier = supplier_b.name
		with self.assertRaises(frappe.ValidationError): doc.insert()

	def test_05_official_xsd_export_and_invalid_identity(self):
		self.opening_debt("1000")
		payment = self.payment().insert(); payment.submit()
		tax = frappe.get_doc("Withholding Tax", {"source_supplier_payment": payment.name})
		from frappe.www.printview import get_html_and_style
		certificate = get_html_and_style("Withholding Tax", tax.name, print_format="Withholding Tax Certificate")
		self.assertIn(tax.certificate_reference, certificate["html"])
		batch = frappe.get_doc({"doctype": "TEJ Export Batch", "business": self.business.name, "declaration_year": 2026, "declaration_month": "2026-08-01", "declaration_type": "INITIAL", "records": [{"withholding_tax": tax.name, "certificate_reference": tax.certificate_reference, "action": "ADD"}]}).insert()
		xml = serialize_batch(batch)
		self.assertTrue(validate_xml(xml))
		result = generate_batch_xml(batch.name)
		self.assertTrue(result["xsd_valid"])
		batch.reload(); self.assertEqual((batch.status, batch.xsd_validation_status, batch.record_count), ("EXPORT_READY", "VALID", 1))
		tax.reload(); self.assertEqual(tax.tej_state, "EXPORTED")
		reversal = frappe.get_doc({"doctype": "Supplier Payment Reversal", "business": self.business.name, "original_supplier_payment": payment.name, "business_date": "2026-08-20", "reason": "Exported end-to-end reversal"}).insert(); reversal.submit()
		tax.reload()
		self.assertEqual((self.balance(), tax.status, tax.tej_state, tax.last_tej_export_batch), (Decimal("1000.000"), "REVERSED", "EXPORTED_THEN_REVERSED", batch.name))

		payment2 = self.payment(amount="100", base="100", rate="1").insert(); payment2.submit()
		tax2 = frappe.get_doc("Withholding Tax", {"source_supplier_payment": payment2.name})
		tax2.db_set("beneficiary_email", None, update_modified=False)
		bad = frappe.get_doc({"doctype": "TEJ Export Batch", "business": self.business.name, "declaration_year": 2026, "declaration_month": "2026-08-01", "declaration_type": "CORRECTIVE", "correction_of": batch.name, "records": [{"withholding_tax": tax2.name, "certificate_reference": tax2.certificate_reference, "action": "ADD"}]}).insert()
		with self.assertRaises(frappe.ValidationError): generate_batch_xml(bad.name)
		bad.reload(); self.assertEqual((bad.status, bad.xsd_validation_status), ("VALIDATION_FAILED", "INVALID"))

	def test_06_tenant_known_id_access_and_cross_batch_link_denied(self):
		self.opening_debt("1000")
		payment = self.payment().insert(); payment.submit()
		tax = frappe.get_doc("Withholding Tax", {"source_supplier_payment": payment.name})
		business_b = frappe.get_doc({"doctype": "Business", "business_name": f"Tenant B {uuid4().hex[:6]}"}).insert()
		user = frappe.get_doc({"doctype": "User", "email": f"owner-b-{uuid4().hex[:8]}@example.com", "first_name": "Owner B", "send_welcome_email": 0, "creditflow_business": business_b.name}).insert(ignore_permissions=True); user.add_roles("OWNER")
		frappe.set_user(user.name)
		self.assertFalse(frappe.has_permission("Withholding Tax", "read", tax.name))
		with self.assertRaises(frappe.PermissionError): frappe.get_doc("Withholding Tax", tax.name).check_permission("read")
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			frappe.get_doc({"doctype": "TEJ Export Batch", "business": business_b.name, "declaration_year": 2026, "declaration_month": "2026-08-01", "records": [{"withholding_tax": tax.name, "action": "ADD"}]}).insert()
