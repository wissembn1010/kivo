from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestSaleReversal(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Reversal Test Business {suffix}"}
		).insert()
		self.customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"Reversal Test Customer {suffix}",
				"business": self.business.name,
			}
		).insert()
		self.product = self.make_product(f"REVERSAL-TEST-{suffix}")
		self.second_product = self.make_product(f"REVERSAL-TEST-SECOND-{suffix}")

	def make_product(self, reference):
		return frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business.name,
				"product_name": reference,
				"reference": reference,
			}
		).insert()

	def add_stock(self, product, quantity):
		movement = frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business.name,
				"product": product,
				"direction": "IN",
				"quantity": quantity,
				"movement_reason": "OPENING_STOCK",
				"business_date": frappe.utils.today(),
			}
		).insert()
		movement.submit()

	def make_sale(
		self,
		include_second_item=False,
		submit=True,
		sale_mode="CREDIT",
		amount_paid="0",
		customer=True,
	):
		self.add_stock(self.product.name, "10")
		if include_second_item:
			self.add_stock(self.second_product.name, "8")
		sale = frappe.get_doc(
			{
				"doctype": "Sale",
				"business": self.business.name,
				"business_date": frappe.utils.today(),
				"sale_mode": sale_mode,
				"amount_paid": amount_paid,
			}
		)
		if customer:
			sale.customer = self.customer.name
		sale.append(
			"items",
			{"product": self.product.name, "quantity": "3", "unit_price": "2"},
		)
		if include_second_item:
			sale.append(
				"items",
				{"product": self.second_product.name, "quantity": "2", "unit_price": "4"},
			)
		sale.insert()
		if submit:
			sale.submit()
		return sale

	def make_reversal(self, sale):
		return frappe.get_doc(
			{
				"doctype": "Sale Reversal",
				"original_sale": sale.name,
				"business": sale.business,
				"business_date": frappe.utils.today(),
				"reason": "Test reversal",
			}
		)

	def get_stock(self, product):
		result = frappe.db.sql(
			"""
			SELECT COALESCE(
				SUM(CASE WHEN direction = 'IN' THEN quantity ELSE -quantity END),
				0
			)
			FROM `tabStock Movement`
			WHERE business = %s AND product = %s AND docstatus = 1
			""",
			(self.business.name, product),
		)
		return Decimal(str(result[0][0] or 0))

	def test_successful_reversal_is_append_only(self):
		sale = self.make_sale(include_second_item=True)
		original_sale = frappe.get_doc("Sale", sale.name).as_dict()
		original_transaction = frappe.get_all(
			"Credit Transaction",
			filters={"source_sale": sale.name, "docstatus": 1},
			fields=["name", "modified", "docstatus"],
		)[0]
		original_movements = frappe.get_all(
			"Stock Movement",
			filters={"source_sale": sale.name, "docstatus": 1},
			fields=["name", "modified", "docstatus", "product", "quantity"],
			order_by="creation asc",
		)
		balance_before_reversal = Decimal(self.customer.get_ledger_balance())

		reversal = self.make_reversal(sale)
		reversal.insert()
		reversal.submit()

		transactions = frappe.get_all(
			"Credit Transaction",
			filters={"source_sale_reversal": reversal.name},
			fields=["docstatus", "transaction_type", "direction", "amount", "reversal_of"],
		)
		self.assertEqual(len(transactions), 1)
		self.assertEqual(transactions[0].docstatus, 1)
		self.assertEqual(transactions[0].transaction_type, "RETURN")
		self.assertEqual(transactions[0].direction, "CREDIT")
		self.assertEqual(transactions[0].reversal_of, original_transaction.name)
		self.assertEqual(
			Decimal(str(transactions[0].amount)), Decimal(str(sale.total_amount))
		)

		reversal_movements = frappe.get_all(
			"Stock Movement",
			filters={"source_sale_reversal": reversal.name},
			fields=["docstatus", "direction", "movement_reason", "product", "reversal_of"],
		)
		self.assertEqual(len(reversal_movements), 2)
		original_by_name = {movement.name: movement for movement in original_movements}
		for movement in reversal_movements:
			self.assertEqual(movement.docstatus, 1)
			self.assertEqual(movement.direction, "IN")
			self.assertEqual(movement.movement_reason, "REVERSAL")
			self.assertIn(movement.reversal_of, original_by_name)

		self.assertEqual(
			Decimal(self.customer.get_ledger_balance()),
			balance_before_reversal - Decimal(str(sale.total_amount)),
		)
		self.assertEqual(Decimal(self.customer.get_ledger_balance()), Decimal("0.000"))
		self.assertEqual(self.get_stock(self.product.name), Decimal("10"))
		self.assertEqual(self.get_stock(self.second_product.name), Decimal("8"))
		self.assertEqual(frappe.get_doc("Sale", sale.name).as_dict(), original_sale)
		self.assertEqual(
			frappe.db.get_value(
				"Credit Transaction", original_transaction.name, ["modified", "docstatus"]
			),
			(original_transaction.modified, original_transaction.docstatus),
		)
		for original in original_movements:
			self.assertEqual(
				frappe.db.get_value(
					"Stock Movement", original.name, ["modified", "docstatus"]
				),
				(original.modified, original.docstatus),
			)

	def test_second_reversal_is_rejected(self):
		sale = self.make_sale()
		first = self.make_reversal(sale)
		first.insert()
		first.submit()

		with self.assertRaises(frappe.ValidationError):
			self.make_reversal(sale).insert()

	def test_draft_sale_is_rejected(self):
		sale = self.make_sale(submit=False)
		with self.assertRaises(frappe.ValidationError):
			self.make_reversal(sale).insert()

	def test_generated_record_failure_has_no_partial_reversal(self):
		sale = self.make_sale(include_second_item=True)
		reversal = self.make_reversal(sale)
		reversal.insert()

		from creditflow.creditflow.doctype.stock_movement.stock_movement import (
			StockMovement,
		)

		with patch.object(
			StockMovement,
			"submit",
			side_effect=frappe.ValidationError("Forced reversal movement failure"),
		):
			with self.assertRaises(frappe.ValidationError):
				reversal.submit()

		self.assertEqual(
			frappe.db.count(
				"Credit Transaction", {"source_sale_reversal": reversal.name}
			),
			0,
		)
		self.assertEqual(
			frappe.db.count("Stock Movement", {"source_sale_reversal": reversal.name}),
			0,
		)
		self.assertEqual(
			frappe.db.get_value("Sale Reversal", reversal.name, "docstatus"), 0
		)

	def test_submitted_reversal_cannot_be_cancelled(self):
		sale = self.make_sale()
		reversal = self.make_reversal(sale)
		reversal.insert()
		reversal.submit()
		with self.assertRaises(frappe.ValidationError):
			reversal.cancel()

	def test_cash_sale_reversal_requires_sale_return(self):
		sale = self.make_sale(customer=False, sale_mode="CASH", amount_paid="6")
		with self.assertRaisesRegex(frappe.ValidationError, "Sale Return"):
			self.make_reversal(sale).insert().submit()
		self.assertEqual(frappe.db.count("Credit Transaction", {"business": self.business.name}), 0)
		self.assertEqual(self.get_stock(self.product.name), Decimal("7"))

	def test_mixed_sale_reversal_preserves_debt_and_requires_sale_return(self):
		sale = self.make_sale(sale_mode="MIXED", amount_paid="2")
		with self.assertRaisesRegex(frappe.ValidationError, "Sale Return"):
			self.make_reversal(sale).insert().submit()
		self.assertEqual(Decimal(self.customer.get_ledger_balance()), Decimal("4.000"))
		self.assertEqual(self.get_stock(self.product.name), Decimal("7"))
