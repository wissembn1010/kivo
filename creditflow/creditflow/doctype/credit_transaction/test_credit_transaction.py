from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestCreditTransaction(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:10]
		self.business = self.make_business(f"Credit Integrity {suffix}")
		self.other_business = self.make_business(f"Other Credit Integrity {suffix}")
		self.customer = self.make_customer(self.business.name, f"Customer {suffix}")
		self.other_customer = self.make_customer(self.other_business.name, f"Other Customer {suffix}")
		self.product = frappe.get_doc({"doctype": "Product", "business": self.business.name, "product_name": f"PRODUCT-{suffix}", "reference": f"PRODUCT-{suffix}"}).insert()

	def make_business(self, name):
		return frappe.get_doc({"doctype": "Business", "business_name": name}).insert()

	def make_customer(self, business, name):
		return frappe.get_doc({"doctype": "Customer", "business": business, "customer_name": name}).insert()

	def transaction(self, **values):
		data = {"doctype": "Credit Transaction", "business": self.business.name, "customer": self.customer.name, "transaction_type": "OPENING_BALANCE", "direction": "DEBIT", "amount": 10, "transaction_date": frappe.utils.today()}
		data.update(values)
		return frappe.get_doc(data)

	def make_sale(self):
		frappe.get_doc({"doctype": "Stock Movement", "business": self.business.name, "product": self.product.name, "direction": "IN", "quantity": 5, "movement_reason": "OPENING_STOCK", "business_date": frappe.utils.today()}).insert().submit()
		sale = frappe.get_doc({"doctype": "Sale", "business": self.business.name, "customer": self.customer.name, "business_date": frappe.utils.today(), "sale_mode": "CREDIT", "amount_paid": "0", "items": [{"product": self.product.name, "quantity": 1, "unit_price": 10}]})
		sale.insert().submit()
		return sale

	def test_fake_sale_transaction_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.transaction(transaction_type="SALE", direction="DEBIT").insert()

	def test_fake_payment_transaction_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.transaction(transaction_type="PAYMENT", direction="CREDIT").insert()

	def test_wrong_business_customer_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.transaction(customer=self.other_customer.name).insert()

	def test_incorrect_source_relationship_is_rejected(self):
		sale = self.make_sale()
		tx = self.transaction(transaction_type="SALE", direction="DEBIT", source_sale=sale.name, amount=9)
		tx.flags.creditflow_system_generated = True
		with self.assertRaises(frappe.ValidationError):
			tx.insert()

	def test_duplicate_source_posting_is_rejected(self):
		sale = self.make_sale()
		tx = self.transaction(transaction_type="SALE", direction="DEBIT", source_sale=sale.name, amount=sale.total_amount)
		tx.flags.creditflow_system_generated = True
		with self.assertRaises(frappe.ValidationError):
			tx.insert()

	def test_dynamic_balance_ignores_removed_stored_field(self):
		self.transaction(amount=12).insert().submit()
		self.transaction(direction="CREDIT", amount=2).insert().submit()
		self.assertEqual(self.customer.get_ledger_balance(), "10.000")
