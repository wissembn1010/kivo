import csv
import io
import json
import time
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.onboarding import commit_csv, preview_csv
from creditflow.creditflow.report.customer_balances.customer_balances import execute as customer_balances
from creditflow.creditflow.report.customer_statement.customer_statement import execute as customer_statement
from creditflow.creditflow.report.stock_on_hand.stock_on_hand import execute as stock_on_hand
from creditflow.creditflow.report.supplier_balances.supplier_balances import execute as supplier_balances
from creditflow.creditflow.report.supplier_statement.supplier_statement import execute as supplier_statement


class IntegrationTestOnboardingMigrationDestructive(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.token = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Migration Audit {self.token}"}
		).insert()
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"migration-owner-{self.token}@example.com",
				"first_name": "Migration Owner",
				"send_welcome_email": 0,
				"creditflow_business": self.business.name,
			}
		).insert(ignore_permissions=True)
		user.add_roles("OWNER")
		self.owner = user.name
		frappe.set_user(self.owner)

	def tearDown(self):
		frappe.set_user("Administrator")

	def csv_text(self, headers, rows):
		stream = io.StringIO()
		writer = csv.writer(stream)
		writer.writerow(headers)
		writer.writerows(rows)
		return stream.getvalue()

	def test_duplicate_rows_are_identified_during_preview(self):
		for import_type, csv_text in (
			("CUSTOMERS", "customer_name\nRepeated Customer\nRepeated Customer\n"),
			("SUPPLIERS", "supplier_name\nRepeated Supplier\nRepeated Supplier\n"),
			(
				"PRODUCTS",
				f"product_name,reference,base_uom\nRepeated Product,DUP-{self.token},UNIT\nRepeated Product,DUP-{self.token},UNIT\n",
			),
		):
			preview = preview_csv(import_type, csv_text)
			self.assertEqual(preview["valid_count"], 1, import_type)
			self.assertEqual(preview["invalid_count"], 1, import_type)
			self.assertIn("duplicate", preview["invalid_rows"][0]["error"].lower())

	def test_surplus_csv_values_are_rejected_as_malformed(self):
		csv_text = "customer_name,phone\nSafe Name,123,UNDECLARED VALUE\n"
		preview = preview_csv("CUSTOMERS", csv_text)
		self.assertEqual(preview["valid_count"], 0)
		self.assertEqual(preview["invalid_count"], 1)
		self.assertEqual(preview["invalid_rows"][0]["row"], 2)
		self.assertIn("more values than declared columns", preview["invalid_rows"][0]["error"])

	def test_normalized_duplicate_values_are_deterministic(self):
		preview = preview_csv("CUSTOMERS", "customer_name\n  Ahmed  \nahmed\n")
		self.assertEqual((preview["valid_count"], preview["invalid_count"]), (1, 1))
		self.assertIn("first appeared on row 2", preview["invalid_rows"][0]["error"])

	def test_unicode_normalization_does_not_corrupt_names(self):
		preview = preview_csv("CUSTOMERS", "customer_name\nSociété Générale\nSOCIÉTÉ GÉNÉRALE\nمحمد بن علي\n")
		self.assertEqual((preview["valid_count"], preview["invalid_count"]), (2, 1))
		self.assertEqual(preview["valid_rows"][1]["data"]["customer_name"], "محمد بن علي")

	def test_unknown_declared_column_is_still_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Unsupported CSV column"):
			preview_csv("CUSTOMERS", "customer_name,legacy_secret\nSafe,discard-me\n")

	def test_duplicate_opening_reference_is_rejected_before_commit(self):
		customer = commit_csv("CUSTOMERS", f"customer_name\nOpening Customer {self.token}\n")["created"][0]
		preview = preview_csv("OPENING_CUSTOMER_DEBT", f"customer,amount,reference\n{customer},10,LEG-{self.token}\n{customer},20,leg-{self.token}\n")
		self.assertEqual((preview["valid_count"], preview["invalid_count"]), (1, 1))
		self.assertIn("duplicate legacy reference", preview["invalid_rows"][0]["error"])

	def test_exact_financial_file_cannot_be_replayed(self):
		customer = commit_csv("CUSTOMERS", f"customer_name\nReplay Customer {self.token}\n")["created"][0]
		csv_text = f"customer,amount,reference\n{customer},100.500,REPLAY-{self.token}\n"
		commit_csv("OPENING_CUSTOMER_DEBT", csv_text)
		with self.assertRaisesRegex(frappe.ValidationError, "already imported"):
			commit_csv("OPENING_CUSTOMER_DEBT", csv_text)
		self.assertEqual(
			frappe.db.count("Credit Transaction", {"customer": customer, "transaction_type": "OPENING_BALANCE", "docstatus": 1}),
			1,
		)

	def test_batch_preserves_required_audit_evidence(self):
		result = commit_csv("CUSTOMERS", f"customer_name\nAudit Customer {self.token}\n", filename="customers.csv")
		batch = frappe.get_doc("Onboarding Import Batch", result["batch"])
		for fieldname in ("filename", "status", "success_count", "failure_count", "errors", "created_records"):
			self.assertTrue(batch.meta.has_field(fieldname), fieldname)
		self.assertEqual(batch.business, self.business.name)
		self.assertEqual(batch.imported_by, self.owner)
		self.assertTrue(batch.imported_on)
		self.assertEqual((batch.filename, batch.status, batch.input_row_count, batch.success_count, batch.failure_count), ("customers.csv", "SUCCESS", 1, 1, 0))
		self.assertEqual(len(batch.created_records), 1)
		self.assertEqual((batch.created_records[0].source_row, batch.created_records[0].result_status, batch.created_records[0].created_doctype, batch.created_records[0].created_document), (2, "SUCCESS", "Customer", result["created"][0]))

	def test_failed_atomic_import_records_no_committed_documents(self):
		csv_text = "customer_name,credit_limit\nValid Candidate,10\nInvalid Candidate,-1\n"
		preview = preview_csv("CUSTOMERS", csv_text, filename="failed.csv")
		with self.assertRaisesRegex(frappe.ValidationError, "row 3"):
			commit_csv("CUSTOMERS", csv_text, filename="failed.csv")
		batch = frappe.get_doc("Onboarding Import Batch", {"import_key": preview["import_key"]})
		self.assertEqual((batch.status, batch.input_row_count, batch.success_count, batch.failure_count), ("FAILED", 2, 0, 1))
		self.assertFalse(frappe.db.exists("Customer", {"business": self.business.name, "customer_name": "Valid Candidate"}))
		self.assertTrue(all(not row.created_document for row in batch.created_records))

	def test_clean_bulk_import_counts_and_reconciliation(self):
		customers = self.csv_text(["customer_name"], [[f"Clean Customer {self.token} {i}"] for i in range(100)])
		suppliers = self.csv_text(["supplier_name"], [[f"Clean Supplier {self.token} {i}"] for i in range(50)])
		products = self.csv_text(["product_name", "reference", "base_uom"], [[f"Clean Product {i}", f"CLEAN-{self.token}-{i}", "UNIT"] for i in range(200)])
		customer_result = commit_csv("CUSTOMERS", customers, filename="customers-100.csv")
		supplier_result = commit_csv("SUPPLIERS", suppliers, filename="suppliers-50.csv")
		product_result = commit_csv("PRODUCTS", products, filename="products-200.csv")
		customer_debt = self.csv_text(["customer", "amount", "reference"], [[name, "1.250", f"CD-{self.token}-{i}"] for i, name in enumerate(customer_result["created"])])
		supplier_debt = self.csv_text(["supplier", "amount", "reference"], [[name, "2.500", f"SD-{self.token}-{i}"] for i, name in enumerate(supplier_result["created"])])
		stock = self.csv_text(["product", "quantity", "reference"], [[name, "3.5", f"ST-{self.token}-{i}"] for i, name in enumerate(product_result["created"])])
		commit_csv("OPENING_CUSTOMER_DEBT", customer_debt, filename="customer-debt.csv")
		commit_csv("OPENING_SUPPLIER_DEBT", supplier_debt, filename="supplier-debt.csv")
		commit_csv("OPENING_STOCK", stock, filename="stock.csv")
		self.assertEqual((len(customer_result["created"]), len(supplier_result["created"]), len(product_result["created"])), (100, 50, 200))
		self.assertEqual(frappe.db.count("Customer", {"business": self.business.name}), 100)
		self.assertEqual(frappe.db.count("Supplier", {"business": self.business.name}), 50)
		self.assertEqual(frappe.db.count("Product", {"business": self.business.name}), 200)
		customer_total = frappe.db.sql("SELECT COALESCE(SUM(amount),0) FROM `tabCredit Transaction` WHERE business=%s AND transaction_type='OPENING_BALANCE' AND docstatus=1", self.business.name)[0][0]
		supplier_total = frappe.db.sql("SELECT COALESCE(SUM(amount),0) FROM `tabSupplier Transaction` WHERE business=%s AND transaction_type='OPENING_BALANCE' AND docstatus=1", self.business.name)[0][0]
		stock_total = frappe.db.sql("SELECT COALESCE(SUM(quantity),0) FROM `tabStock Movement` WHERE business=%s AND movement_reason='OPENING_STOCK' AND docstatus=1", self.business.name)[0][0]
		self.assertEqual((Decimal(str(customer_total)), Decimal(str(supplier_total)), Decimal(str(stock_total))), (Decimal("125.000"), Decimal("125.000"), Decimal("700.0")))
		for result, count in ((customer_result, 100), (supplier_result, 50), (product_result, 200)):
			batch = frappe.get_doc("Onboarding Import Batch", result["batch"])
			self.assertEqual((batch.status, batch.input_row_count, batch.success_count, len(batch.created_records)), ("SUCCESS", count, count, count))

	def test_large_import_completes_without_truncation_or_duplicates(self):
		start = time.perf_counter()
		customers = commit_csv("CUSTOMERS", self.csv_text(["customer_name"], [[f"Large Customer {self.token} {i:04d}"] for i in range(1000)]), filename="large-customers.csv")
		products = commit_csv("PRODUCTS", self.csv_text(["product_name", "reference", "base_uom"], [[f"Large Product {i:04d}", f"LARGE-{self.token}-{i:04d}", "UNIT"] for i in range(500)]), filename="large-products.csv")
		suppliers = commit_csv("SUPPLIERS", self.csv_text(["supplier_name"], [[f"Large Supplier {self.token} {i:04d}"] for i in range(250)]), filename="large-suppliers.csv")
		runtime = time.perf_counter() - start
		print(f"LARGE_IMPORT_RUNTIME_SECONDS={runtime:.3f}")
		self.assertEqual((len(customers["created"]), len(products["created"]), len(suppliers["created"])), (1000, 500, 250))
		self.assertEqual(len(set(customers["created"])), 1000)
		self.assertEqual(len(set(products["created"])), 500)
		self.assertEqual(len(set(suppliers["created"])), 250)
		self.assertEqual((frappe.db.count("Customer", {"business": self.business.name}), frappe.db.count("Product", {"business": self.business.name}), frappe.db.count("Supplier", {"business": self.business.name})), (1000, 500, 250))

	def test_opening_stock_same_file_duplicate_and_all_financial_replays(self):
		customer = commit_csv("CUSTOMERS", f"customer_name\nIdempotent Customer {self.token}\n")["created"][0]
		supplier = commit_csv("SUPPLIERS", f"supplier_name\nIdempotent Supplier {self.token}\n")["created"][0]
		product = commit_csv("PRODUCTS", f"product_name,reference,base_uom\nIdempotent Product,IDEM-{self.token},UNIT\n")["created"][0]
		duplicate_stock = f"product,quantity,reference\n{product},5,STOCK-{self.token}\n{product},6,stock-{self.token}\n"
		preview = preview_csv("OPENING_STOCK", duplicate_stock)
		self.assertEqual((preview["valid_count"], preview["invalid_count"]), (1, 1))
		files = (("OPENING_CUSTOMER_DEBT", f"customer,amount,reference\n{customer},10,C-{self.token}\n", "Credit Transaction"), ("OPENING_SUPPLIER_DEBT", f"supplier,amount,reference\n{supplier},20,S-{self.token}\n", "Supplier Transaction"), ("OPENING_STOCK", f"product,quantity,reference\n{product},7,P-{self.token}\n", "Stock Movement"))
		for kind, text, doctype in files:
			commit_csv(kind, text)
			with self.assertRaisesRegex(frappe.ValidationError, "already imported"): commit_csv(kind, text)
			self.assertEqual(frappe.db.count(doctype, {"business": self.business.name, "docstatus": 1}), 1)

	def test_partially_bad_csv_is_atomic_and_reports_every_bad_row(self):
		text = self.csv_text(["customer_name", "credit_limit"], [["Valid One", "10"], ["", "20"], ["Bad Decimal", "oops"], ["Duplicate", "1"], [" duplicate ", "2"]])
		preview = preview_csv("CUSTOMERS", text, filename="mixed.csv")
		self.assertEqual((preview["valid_count"], preview["invalid_count"]), (2, 3))
		with self.assertRaises(frappe.ValidationError): commit_csv("CUSTOMERS", text, filename="mixed.csv")
		self.assertEqual(frappe.db.count("Customer", {"business": self.business.name}), 0)
		batch = frappe.get_doc("Onboarding Import Batch", {"import_key": preview["import_key"]})
		self.assertEqual((batch.status, batch.success_count, batch.failure_count), ("FAILED", 0, 3))
		self.assertEqual(len(json.loads(batch.errors)), 3)
		bad_product = preview_csv("PRODUCTS", f"product_name,reference,base_uom\nBad UOM,BAD-{self.token},UNKNOWN\n")
		self.assertEqual(bad_product["invalid_count"], 1)

	def test_decimal_format_policy_is_unambiguous(self):
		customer = commit_csv("CUSTOMERS", f"customer_name\nDecimal Customer {self.token}\n")["created"][0]
		accepted = ("100", "100.5", "100.500")
		for index, value in enumerate(accepted):
			preview = preview_csv("OPENING_CUSTOMER_DEBT", self.csv_text(["customer", "amount", "reference"], [[customer, value, f"DEC-OK-{index}-{self.token}"]]))
			self.assertEqual((preview["valid_count"], preview["invalid_count"]), (1, 0), value)
		for index, value in enumerate(("1,250.500", "1250,500", "0", "-20", "")):
			preview = preview_csv("OPENING_CUSTOMER_DEBT", self.csv_text(["customer", "amount", "reference"], [[customer, value, f"DEC-BAD-{index}-{self.token}"]]))
			self.assertEqual((preview["valid_count"], preview["invalid_count"]), (0, 1), value)

	def test_unicode_text_whitespace_quotes_commas_newlines_and_formula_are_safe(self):
		names = ["Société Générale", "Électricité du Sahel", "محمد بن علي", "L'Atelier", "  Client Test  ", '=SUM(1,2)', 'Quoted "Client", North', "Line One\nLine Two"]
		result = commit_csv("CUSTOMERS", self.csv_text(["customer_name"], [[name] for name in names]), filename="unicode.csv")
		stored = [frappe.db.get_value("Customer", name, "customer_name") for name in result["created"]]
		self.assertEqual(stored[:4], names[:4])
		self.assertIn("Client Test", stored)
		self.assertIn('=SUM(1,2)', stored)
		self.assertIn('Quoted "Client", North', stored)
		self.assertIn("Line One\nLine Two", stored)
		long_text = "X" * 1000
		with self.assertRaises(Exception): commit_csv("CUSTOMERS", self.csv_text(["customer_name"], [[long_text]]), filename="too-long.csv")

	def test_opening_balances_stock_and_reports_reconcile(self):
		customer = commit_csv("CUSTOMERS", f"customer_name\nRecon Customer {self.token}\n")["created"][0]
		supplier = commit_csv("SUPPLIERS", f"supplier_name\nRecon Supplier {self.token}\n")["created"][0]
		product = commit_csv("PRODUCTS", f"product_name,reference,base_uom\nRecon Product,RECON-{self.token},UNIT\n")["created"][0]
		commit_csv("OPENING_CUSTOMER_DEBT", f"customer,amount,reference\n{customer},500.250,RC-{self.token}\n")
		commit_csv("OPENING_SUPPLIER_DEBT", f"supplier,amount,reference\n{supplier},300.125,RS-{self.token}\n")
		commit_csv("OPENING_STOCK", f"product,quantity,reference\n{product},25.5,RST-{self.token}\n")
		_, customer_rows = customer_statement({"customer": customer}); _, customer_balance_rows = customer_balances({"customer": customer, "show_zero_balances": 1})
		supplier_rows = supplier_statement({"supplier": supplier})[1]; _, supplier_balance_rows = supplier_balances({"supplier": supplier, "show_zero_balances": 1})
		_, stock_rows = stock_on_hand({"product": product, "show_zero_stock": 1})
		self.assertEqual(Decimal(frappe.get_doc("Customer", customer).get_ledger_balance()), Decimal("500.250"))
		self.assertEqual((customer_rows[-1].running_balance, customer_balance_rows[0].outstanding_balance), (Decimal("500.250"), Decimal("500.250")))
		self.assertEqual(Decimal(frappe.get_doc("Supplier", supplier).get_ledger_balance()), Decimal("300.125"))
		self.assertEqual((supplier_rows[-1].running_balance, supplier_balance_rows[0].outstanding_balance), (Decimal("300.125"), Decimal("300.125")))
		self.assertEqual(Decimal(str(stock_rows[0].stock_on_hand)), Decimal("25.5"))

	def test_supported_and_unsupported_uoms_and_zero_negative_policy(self):
		supported = ("UNIT", "KG", "M", "L", "BOX", "PACK", "ROLL", "OTHER")
		products = commit_csv("PRODUCTS", self.csv_text(["product_name", "reference", "base_uom"], [[f"UOM {uom}", f"UOM-{uom}-{self.token}", uom] for uom in supported]))
		stock = self.csv_text(["product", "quantity", "reference"], [[name, index + 1, f"UOM-ST-{index}-{self.token}"] for index, name in enumerate(products["created"])])
		commit_csv("OPENING_STOCK", stock)
		for name, uom in zip(products["created"], supported): self.assertEqual(frappe.db.get_value("Product", name, "primary_unit"), uom)
		for alias in ("PIECE", "METER"):
			preview = preview_csv("PRODUCTS", f"product_name,reference,base_uom\nAlias,ALIAS-{alias}-{self.token},{alias}\n")
			self.assertEqual(preview["invalid_count"], 1)
			self.assertIn("does not exist or is inactive", preview["invalid_rows"][0]["error"])
		customer = commit_csv("CUSTOMERS", f"customer_name\nZero Customer {self.token}\n")["created"][0]
		supplier = commit_csv("SUPPLIERS", f"supplier_name\nZero Supplier {self.token}\n")["created"][0]
		for kind, target_header, target in (("OPENING_CUSTOMER_DEBT", "customer", customer), ("OPENING_SUPPLIER_DEBT", "supplier", supplier), ("OPENING_STOCK", "product", products["created"][0])):
			value_header = "quantity" if kind == "OPENING_STOCK" else "amount"
			for value in ("0", "-1", ""):
				self.assertEqual(preview_csv(kind, self.csv_text([target_header, value_header], [[target, value]]))["invalid_count"], 1)
		self.assertEqual(preview_csv("PRODUCTS", f"product_name,reference,selling_price,base_uom\nFree,FREE-{self.token},0,UNIT\n")["valid_count"], 1)
		self.assertEqual(preview_csv("PRODUCTS", f"product_name,reference,selling_price,base_uom\nNegative,NEG-{self.token},-1,UNIT\n")["invalid_count"], 1)

	def test_headers_order_empty_files_and_no_side_effects(self):
		before = frappe.db.count("Customer", {"business": self.business.name})
		missing = preview_csv("CUSTOMERS", "phone\n123\n")
		self.assertEqual(missing["invalid_count"], 1)
		with self.assertRaises(frappe.ValidationError): commit_csv("CUSTOMERS", "phone\n123\n")
		with self.assertRaisesRegex(frappe.ValidationError, "CSV header row"): preview_csv("CUSTOMERS", "")
		with self.assertRaisesRegex(frappe.ValidationError, "no data rows"): commit_csv("CUSTOMERS", "customer_name\n")
		reordered = commit_csv("SUPPLIERS", f"notes,supplier_name,phone\nReordered,Order Supplier {self.token},123\n")
		self.assertEqual(frappe.db.get_value("Supplier", reordered["created"][0], ["supplier_name", "notes"]), (f"Order Supplier {self.token}", "Reordered"))
		self.assertEqual(frappe.db.count("Customer", {"business": self.business.name}), before)

	def test_import_security_owner_staff_batch_and_business_tampering(self):
		frappe.set_user("Administrator")
		other = frappe.get_doc({"doctype": "Business", "business_name": f"Other Migration {self.token}"}).insert()
		staff = frappe.get_doc({"doctype": "User", "email": f"migration-staff-{self.token}@example.com", "first_name": "Staff", "send_welcome_email": 0, "creditflow_business": self.business.name}).insert(ignore_permissions=True); staff.add_roles("STAFF")
		frappe.set_user(self.owner)
		with self.assertRaises(frappe.PermissionError): commit_csv("CUSTOMERS", "customer_name\nAttack\n", business=other.name)
		with self.assertRaises(frappe.PermissionError): frappe.get_doc({"doctype": "Onboarding Import Batch", "business": other.name, "import_type": "CUSTOMERS", "file_hash": "x", "import_key": f"attack-{self.token}"}).insert()
		frappe.set_user(staff.name)
		for kind in ("CUSTOMERS", "SUPPLIERS", "PRODUCTS", "OPENING_STOCK", "OPENING_CUSTOMER_DEBT", "OPENING_SUPPLIER_DEBT"):
			with self.assertRaises(frappe.PermissionError): preview_csv(kind, "x\n1\n")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Customer", {"business": other.name}), 0)

	def test_forced_mid_batch_failure_rolls_back_and_audits(self):
		customer_a = commit_csv("CUSTOMERS", f"customer_name\nRollback A {self.token}\n")["created"][0]
		customer_b = commit_csv("CUSTOMERS", f"customer_name\nRollback B {self.token}\n")["created"][0]
		text = f"customer,amount,reference\n{customer_a},10,RB-A-{self.token}\n{customer_b},20,RB-B-{self.token}\n"
		preview = preview_csv("OPENING_CUSTOMER_DEBT", text, filename="rollback.csv")
		from creditflow import onboarding
		real = onboarding._commit_row
		calls = {"count": 0}
		def fail_second(*args, **kwargs):
			calls["count"] += 1
			if calls["count"] == 2: raise RuntimeError("forced row failure")
			return real(*args, **kwargs)
		with patch("creditflow.onboarding._commit_row", side_effect=fail_second):
			with self.assertRaisesRegex(RuntimeError, "forced row failure"): commit_csv("OPENING_CUSTOMER_DEBT", text, filename="rollback.csv")
		self.assertEqual(frappe.db.count("Credit Transaction", {"business": self.business.name, "transaction_type": "OPENING_BALANCE"}), 0)
		batch = frappe.get_doc("Onboarding Import Batch", {"import_key": preview["import_key"]})
		self.assertEqual((batch.status, batch.success_count, batch.failure_count), ("FAILED", 0, 2))
		self.assertTrue(all(row.result_status == "ROLLED BACK" and not row.created_document for row in batch.created_records))

	def test_post_import_sale_payment_purchase_and_supplier_payment(self):
		customer = commit_csv("CUSTOMERS", f"customer_name\nOps Customer {self.token}\n")["created"][0]
		supplier = commit_csv("SUPPLIERS", f"supplier_name\nOps Supplier {self.token}\n")["created"][0]
		product = commit_csv("PRODUCTS", f"product_name,reference,selling_price,base_uom\nOps Product,OPS-{self.token},50,UNIT\n")["created"][0]
		commit_csv("OPENING_STOCK", f"product,quantity,reference\n{product},10,OPS-ST-{self.token}\n")
		sale = frappe.get_doc({"doctype":"Sale","business":self.business.name,"customer":customer,"business_date":frappe.utils.today(),"sale_mode":"CREDIT","amount_paid":0,"items":[{"product":product,"quantity":2,"unit_price":50}]}).insert();sale.submit()
		payment = frappe.get_doc({"doctype":"Payment","business":self.business.name,"customer":customer,"business_date":frappe.utils.today(),"amount":40,"payment_method":"CASH"}).insert();payment.submit()
		purchase = frappe.get_doc({"doctype":"Purchase","business":self.business.name,"supplier":supplier,"business_date":frappe.utils.today(),"payment_status":"CREDIT","items":[{"product":product,"quantity":1,"unit_cost":30}]}).insert();purchase.submit()
		supplier_payment = frappe.get_doc({"doctype":"Supplier Payment","business":self.business.name,"supplier":supplier,"business_date":frappe.utils.today(),"amount":10,"payment_method":"CASH"}).insert();supplier_payment.submit()
		self.assertEqual(Decimal(frappe.get_doc("Customer", customer).get_ledger_balance()), Decimal("60.000"))
		self.assertEqual(Decimal(frappe.get_doc("Supplier", supplier).get_ledger_balance()), Decimal("20.000"))
		_, stocks = stock_on_hand({"product": product, "show_zero_stock": 1})
		self.assertEqual(Decimal(str(stocks[0].stock_on_hand)), Decimal("9"))
