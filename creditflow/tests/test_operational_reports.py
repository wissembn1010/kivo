from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.creditflow.report.customer_balances.customer_balances import (
	execute as execute_customer_balances,
)
from creditflow.creditflow.report.stock_on_hand.stock_on_hand import (
	execute as execute_stock_on_hand,
)


class IntegrationTestOperationalReports(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:10]
		self.suffix = suffix
		self.business = self.make_business(f"Report Business {suffix}")
		self.customer = self.make_customer(self.business.name, f"Report Customer {suffix}")
		self.product = self.make_product(self.business.name, f"REPORT-{suffix}")
		self.supplier = self.make_supplier(self.business.name, f"Report Supplier {suffix}")

	def tearDown(self):
		frappe.set_user("Administrator")

	def make_business(self, name):
		return frappe.get_doc({"doctype": "Business", "business_name": name}).insert()

	def make_customer(self, business, name):
		return frappe.get_doc(
			{"doctype": "Customer", "business": business, "customer_name": name}
		).insert()

	def make_product(self, business, reference):
		return frappe.get_doc(
			{
				"doctype": "Product",
				"business": business,
				"product_name": reference,
				"reference": reference,
				"primary_unit": "UNIT",
			}
		).insert()

	def make_supplier(self, business, name):
		return frappe.get_doc(
			{"doctype": "Supplier", "business": business, "supplier_name": name}
		).insert()

	def make_user(self, business):
		email = f"report-user-{uuid4().hex[:10]}@example.com"
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Report User",
				"send_welcome_email": 0,
				"creditflow_business": business,
			}
		).insert(ignore_permissions=True)
		user.add_roles("STAFF")
		return user.name

	def post_transaction(self, direction, amount):
		transaction = frappe.get_doc(
			{
				"doctype": "Credit Transaction",
				"business": self.business.name,
				"customer": self.customer.name,
				"transaction_type": "OPENING_BALANCE",
				"direction": direction,
				"amount": amount,
				"transaction_date": frappe.utils.today(),
			}
		).insert()
		transaction.submit()
		return transaction

	def post_stock(self, quantity):
		movement = frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business.name,
				"product": self.product.name,
				"direction": "IN",
				"quantity": quantity,
				"movement_reason": "OPENING_STOCK",
				"business_date": frappe.utils.today(),
			}
		).insert()
		movement.submit()
		return movement

	def make_payment(self, amount):
		payment = frappe.get_doc(
			{
				"doctype": "Payment",
				"business": self.business.name,
				"customer": self.customer.name,
				"amount": amount,
				"business_date": frappe.utils.today(),
			}
		).insert()
		payment.submit()
		return payment

	def make_sale(self, quantity, unit_price):
		sale = frappe.get_doc(
			{
				"doctype": "Sale",
				"business": self.business.name,
				"customer": self.customer.name,
				"business_date": frappe.utils.today(),
				"items": [
					{
						"product": self.product.name,
						"quantity": quantity,
						"unit_price": unit_price,
					}
				],
			}
		).insert()
		sale.submit()
		return sale

	def make_purchase(self, quantity):
		purchase = frappe.get_doc(
			{
				"doctype": "Purchase",
				"business": self.business.name,
				"supplier": self.supplier.name,
				"business_date": frappe.utils.today(),
				"items": [
					{
						"product": self.product.name,
						"quantity": quantity,
						"unit_cost": "1",
					}
				],
			}
		).insert()
		purchase.submit()
		return purchase

	def balance(self, customer=None):
		_, rows = execute_customer_balances(
			{"customer": customer or self.customer.name, "show_zero_balances": 1}
		)
		return rows[0] if rows else None

	def stock(self, product=None):
		_, rows = execute_stock_on_hand(
			{"product": product or self.product.name, "show_zero_stock": 1}
		)
		return rows[0] if rows else None

	def test_customer_balance_tracks_payments_sales_and_reversals(self):
		zero_customer = self.make_customer(
			self.business.name, f"Zero Balance Customer {self.suffix}"
		)
		self.assertEqual(self.balance(zero_customer.name).outstanding_balance, Decimal("0.000"))

		self.post_transaction("DEBIT", "20.125")
		self.post_transaction("CREDIT", "0.125")
		row = self.balance()
		self.assertEqual(row.total_debit, Decimal("20.125"))
		self.assertEqual(row.total_credit, Decimal("0.125"))
		self.assertEqual(row.outstanding_balance, Decimal("20.000"))

		payment = self.make_payment("5")
		self.assertEqual(self.balance().outstanding_balance, Decimal("15.000"))

		self.post_stock("10")
		sale = self.make_sale("2", "3")
		self.assertEqual(self.balance().outstanding_balance, Decimal("21.000"))

		frappe.get_doc(
			{
				"doctype": "Sale Reversal",
				"business": self.business.name,
				"original_sale": sale.name,
				"business_date": frappe.utils.today(),
				"reason": "Report test",
			}
		).insert().submit()
		self.assertEqual(self.balance().outstanding_balance, Decimal("15.000"))

		frappe.get_doc(
			{
				"doctype": "Payment Reversal",
				"business": self.business.name,
				"original_payment": payment.name,
				"business_date": frappe.utils.today(),
				"reason": "Report test",
			}
		).insert().submit()
		self.assertEqual(self.balance().outstanding_balance, Decimal("20.000"))

	def test_stock_tracks_purchases_sales_and_reversals(self):
		zero_product = self.make_product(
			self.business.name, f"REPORT-ZERO-{self.suffix}"
		)
		self.assertEqual(self.stock(zero_product.name).stock_on_hand, Decimal("0"))

		self.post_stock("20")
		self.assertEqual(self.stock().stock_on_hand, Decimal("20"))

		purchase = self.make_purchase("5")
		self.assertEqual(self.stock().stock_on_hand, Decimal("25"))

		sale = self.make_sale("4", "1")
		self.assertEqual(self.stock().stock_on_hand, Decimal("21"))

		frappe.get_doc(
			{
				"doctype": "Sale Reversal",
				"business": self.business.name,
				"original_sale": sale.name,
				"business_date": frappe.utils.today(),
				"reason": "Report test",
			}
		).insert().submit()
		self.assertEqual(self.stock().stock_on_hand, Decimal("25"))

		frappe.get_doc(
			{
				"doctype": "Purchase Reversal",
				"business": self.business.name,
				"original_purchase": purchase.name,
				"business_date": frappe.utils.today(),
				"reason": "Report test",
			}
		).insert().submit()
		self.assertEqual(self.stock().stock_on_hand, Decimal("20"))

	def test_report_business_isolation_and_administrator_bypass(self):
		other_business = self.make_business(f"Other Report Business {self.suffix}")
		other_customer = self.make_customer(
			other_business.name, f"Other Report Customer {self.suffix}"
		)
		other_product = self.make_product(
			other_business.name, f"OTHER-REPORT-{self.suffix}"
		)
		frappe.get_doc(
			{
				"doctype": "Credit Transaction",
				"business": other_business.name,
				"customer": other_customer.name,
				"transaction_type": "OPENING_BALANCE",
				"direction": "DEBIT",
				"amount": "9",
				"transaction_date": frappe.utils.today(),
			}
		).insert().submit()
		frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": other_business.name,
				"product": other_product.name,
				"direction": "IN",
				"quantity": "7",
				"movement_reason": "OPENING_STOCK",
				"business_date": frappe.utils.today(),
			}
		).insert().submit()

		user = self.make_user(self.business.name)
		frappe.set_user(user)
		_, balance_rows = execute_customer_balances({"show_zero_balances": 1})
		_, stock_rows = execute_stock_on_hand({"show_zero_stock": 1})
		self.assertNotIn(other_customer.name, {row.customer for row in balance_rows})
		self.assertNotIn(other_product.name, {row.product for row in stock_rows})
		with self.assertRaises(frappe.PermissionError):
			execute_customer_balances({"business": other_business.name})
		with self.assertRaises(frappe.PermissionError):
			execute_stock_on_hand({"business": other_business.name})

		frappe.set_user("Administrator")
		_, balance_rows = execute_customer_balances(
			{"business": other_business.name, "show_zero_balances": 1}
		)
		_, stock_rows = execute_stock_on_hand(
			{"business": other_business.name, "show_zero_stock": 1}
		)
		self.assertIn(other_customer.name, {row.customer for row in balance_rows})
		self.assertIn(other_product.name, {row.product for row in stock_rows})
