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


class IntegrationTestMVPAcceptance(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.suffix = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"MVP Acceptance {self.suffix}"}
		).insert()
		self.other_business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"MVP Other {self.suffix}"}
		).insert()
		self.owner = self.make_user("owner", self.business.name, "OWNER")
		self.staff = self.make_user("staff", self.business.name, "STAFF")

		frappe.set_user(self.owner)
		self.customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"business": self.business.name,
				"customer_name": f"MVP Customer {self.suffix}",
				"phone": "555-0100",
			}
		).insert()
		self.product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business.name,
				"product_name": f"MVP Product {self.suffix}",
				"reference": f"MVP-{self.suffix}",
				"primary_unit": "UNIT",
			}
		).insert()
		self.supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"business": self.business.name,
				"supplier_name": f"MVP Supplier {self.suffix}",
			}
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")

	def make_user(self, prefix, business, role):
		email = f"mvp-{prefix}-{self.suffix}@example.com"
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": f"MVP {prefix.title()}",
				"send_welcome_email": 0,
				"creditflow_business": business,
			}
		).insert(ignore_permissions=True)
		user.add_roles(role)
		return user.name

	def reported_balance(self):
		_, rows = execute_customer_balances(
			{"customer": self.customer.name, "show_zero_balances": 1}
		)
		return rows[0].outstanding_balance

	def reported_stock(self, product=None, **filters):
		report_filters = {
			"product": product or self.product.name,
			"show_zero_stock": 1,
			**filters,
		}
		_, rows = execute_stock_on_hand(report_filters)
		return rows[0].stock_on_hand if rows else None

	def test_complete_mvp_workflow_and_permissions(self):
		frappe.set_user(self.staff)
		purchase = frappe.get_doc(
			{
				"doctype": "Purchase",
				"business": self.business.name,
				"supplier": self.supplier.name,
				"business_date": frappe.utils.today(),
				"items": [
					{
						"product": self.product.name,
						"quantity": "10",
						"unit_cost": "1.2345",
					}
				],
			}
		).insert()
		purchase.submit()
		self.assertEqual(Decimal(str(purchase.total_amount)), Decimal("12.345"))
		self.assertEqual(self.reported_stock(), Decimal("10"))

		sale = frappe.get_doc(
			{
				"doctype": "Sale",
				"business": self.business.name,
				"customer": self.customer.name,
				"business_date": frappe.utils.today(),
				"sale_mode": "CREDIT",
				"amount_paid": "0",
				"items": [
					{
						"product": self.product.name,
						"quantity": "3",
						"unit_price": "2.345",
					}
				],
			}
		).insert()
		sale.submit()
		self.assertEqual(Decimal(str(sale.total_amount)), Decimal("7.035"))
		self.assertEqual(self.reported_stock(), Decimal("7"))
		self.assertEqual(self.reported_balance(), Decimal("7.035"))

		payment = frappe.get_doc(
			{
				"doctype": "Payment",
				"business": self.business.name,
				"customer": self.customer.name,
				"amount": "2.000",
				"business_date": frappe.utils.today(),
			}
		).insert()
		payment.submit()
		self.assertEqual(self.reported_balance(), Decimal("5.035"))

		for doctype, values in (
			(
				"Credit Transaction",
				{
					"customer": self.customer.name,
					"transaction_type": "SALE",
					"direction": "DEBIT",
					"amount": "1",
					"transaction_date": frappe.utils.today(),
				},
			),
			(
				"Stock Movement",
				{
					"product": self.product.name,
					"direction": "IN",
					"quantity": "1",
					"movement_reason": "MANUAL_ADJUSTMENT",
					"business_date": frappe.utils.today(),
				},
			),
		):
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc(
					{"doctype": doctype, "business": self.business.name, **values}
				).insert()

		for doctype, source_field, source in (
			("Sale Reversal", "original_sale", sale.name),
			("Payment Reversal", "original_payment", payment.name),
			("Purchase Reversal", "original_purchase", purchase.name),
		):
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc(
					{
						"doctype": doctype,
						"business": self.business.name,
						source_field: source,
						"business_date": frappe.utils.today(),
						"reason": "Not allowed for STAFF",
					}
				).insert()

		business = frappe.get_doc("Business", self.business.name)
		business.phone = "555-9999"
		with self.assertRaises(frappe.PermissionError):
			business.save()

		frappe.set_user(self.owner)
		sale_reversal = frappe.get_doc(
			{
				"doctype": "Sale Reversal",
				"business": self.business.name,
				"original_sale": sale.name,
				"business_date": frappe.utils.today(),
				"reason": "MVP acceptance",
			}
		).insert()
		sale_reversal.submit()
		self.assertEqual(self.reported_stock(), Decimal("10"))
		self.assertEqual(self.reported_balance(), Decimal("-2.000"))

		payment_reversal = frappe.get_doc(
			{
				"doctype": "Payment Reversal",
				"business": self.business.name,
				"original_payment": payment.name,
				"business_date": frappe.utils.today(),
				"reason": "MVP acceptance",
			}
		).insert()
		payment_reversal.submit()
		self.assertEqual(self.reported_balance(), Decimal("0.000"))

		purchase_reversal = frappe.get_doc(
			{
				"doctype": "Purchase Reversal",
				"business": self.business.name,
				"original_purchase": purchase.name,
				"business_date": frappe.utils.today(),
				"reason": "MVP acceptance",
			}
		).insert()
		purchase_reversal.submit()
		self.assertEqual(self.reported_stock(), Decimal("0"))

		for document in (
			sale,
			payment,
			purchase,
			sale_reversal,
			payment_reversal,
			purchase_reversal,
		):
			with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
				document.cancel()

		frappe.set_user("Administrator")
		negative_product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business.name,
				"product_name": f"MVP Negative {self.suffix}",
				"reference": f"MVP-NEG-{self.suffix}",
				"primary_unit": "UNIT",
			}
		).insert()
		legacy_movement = frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business.name,
				"product": negative_product.name,
				"direction": "OUT",
				"quantity": "2",
				"movement_reason": "MANUAL_ADJUSTMENT",
				"business_date": frappe.utils.today(),
			}
		).insert()
		with self.assertRaisesRegex(frappe.ValidationError, "Insufficient stock"):
			legacy_movement.submit()
		# Synthetic historical negative ledger: report coverage must not rely on
		# permitting new overdrafts through the posting workflow.
		frappe.db.set_value("Stock Movement", legacy_movement.name, "docstatus", 1)

		frappe.set_user(self.owner)
		_, negative_rows = execute_stock_on_hand(
			{"show_negative_stock_only": 1, "show_zero_stock": 1}
		)
		self.assertIn(negative_product.name, {row.product for row in negative_rows})
		_, nonzero_rows = execute_stock_on_hand({"show_zero_stock": 0})
		self.assertNotIn(self.product.name, {row.product for row in nonzero_rows})

		self.assertNotIn(
			self.other_business.name,
			frappe.get_list("Business", pluck="name"),
		)
		with self.assertRaises(frappe.PermissionError):
			execute_customer_balances({"business": self.other_business.name})
		with self.assertRaises(frappe.PermissionError):
			execute_stock_on_hand({"business": self.other_business.name})

		frappe.set_user("Administrator")
		self.assertIn(
			self.other_business.name,
			frappe.get_list("Business", pluck="name"),
		)
