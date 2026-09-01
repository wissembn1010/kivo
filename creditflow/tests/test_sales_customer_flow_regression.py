from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase

from creditflow.creditflow.report.customer_balances.customer_balances import (
	execute as customer_balances,
)
from creditflow.creditflow.report.customer_statement.customer_statement import (
	execute as customer_statement,
)
from creditflow.creditflow.report.product_performance.product_performance import (
	execute as product_performance,
)
from creditflow.creditflow.report.sales_report.sales_report import execute as sales_report


class IntegrationTestSalesCustomerFlowRegression(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		suffix = uuid4().hex[:10]
		self.business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Sales Regression Business {suffix}"}
		).insert()
		self.customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"business": self.business.name,
				"customer_name": f"Sales Regression Customer {suffix}",
			}
		).insert()
		self.product = frappe.get_doc(
			{
				"doctype": "Product",
				"business": self.business.name,
				"product_name": f"Sales Regression Product {suffix}",
				"reference": f"SALE-REG-{suffix}",
				"primary_unit": "UNIT",
				"tva_rate": 0,
			}
		).insert()
		self.post_opening_stock("10")

	def tearDown(self):
		frappe.set_user("Administrator")

	def post_opening_stock(self, quantity):
		movement = frappe.get_doc(
			{
				"doctype": "Stock Movement",
				"business": self.business.name,
				"product": self.product.name,
				"direction": "IN",
				"quantity": quantity,
				"movement_reason": "OPENING_STOCK",
				"business_date": "2026-08-19",
			}
		).insert()
		movement.submit()

	def make_sale(
		self,
		*,
		business_date="2026-08-20",
		customer=None,
		quantity="2",
		unit_price="50",
		sale_mode="CREDIT",
		amount_paid="0",
		payment_method=None,
	):
		sale = frappe.get_doc(
			{
				"doctype": "Sale",
				"business": self.business.name,
				"customer": customer or self.customer.name,
				"business_date": business_date,
				"sale_mode": sale_mode,
				"amount_paid": amount_paid,
				"payment_method": payment_method,
				"items": [
					{
						"product": self.product.name,
						"quantity": quantity,
						"unit_price": unit_price,
					}
				],
			}
		).insert()
		return sale

	def submit_sale(self, **kwargs):
		sale = self.make_sale(**kwargs)
		sale.submit()
		return sale

	def submit_credit_sale(self, **kwargs):
		return self.submit_sale(sale_mode="CREDIT", amount_paid="0", **kwargs)

	def submit_payment(self, amount="40", business_date="2026-08-21"):
		payment = frappe.get_doc(
			{
				"doctype": "Payment",
				"business": self.business.name,
				"customer": self.customer.name,
				"business_date": business_date,
				"amount": amount,
				"payment_method": "CASH",
			}
		).insert()
		payment.submit()
		return payment

	def submit_reversal(self, sale, business_date="2026-08-22"):
		reversal = frappe.get_doc(
			{
				"doctype": "Sale Reversal",
				"business": self.business.name,
				"original_sale": sale.name,
				"business_date": business_date,
				"reason": "Sales/customer regression test",
			}
		).insert()
		reversal.submit()
		return reversal

	def stock_balance(self):
		value = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(CASE WHEN direction='IN' THEN quantity ELSE -quantity END), 0)
			FROM `tabStock Movement`
			WHERE business=%s AND product=%s AND docstatus=1
			""",
			(self.business.name, self.product.name),
		)[0][0]
		return Decimal(str(value))

	def customer_balance(self):
		return Decimal(self.customer.get_ledger_balance())

	def statement_rows(self):
		_, rows = customer_statement({"customer": self.customer.name})
		return rows

	def balance_row(self):
		_, rows = customer_balances(
			{
				"business": self.business.name,
				"customer": self.customer.name,
				"show_zero_balances": 1,
			}
		)
		return rows[0]

	def test_01_credit_sale_updates_stock_debt_statement_and_sales_report(self):
		sale = self.submit_credit_sale()

		self.assertEqual(Decimal(str(sale.total_amount)), Decimal("100.000"))
		self.assertEqual(self.stock_balance(), Decimal("8"))
		self.assertEqual(self.customer_balance(), Decimal("100.000"))

		statement_row = next(row for row in self.statement_rows() if row.reference == sale.name)
		self.assertEqual(statement_row.transaction_type, "SALE")
		self.assertEqual(statement_row.debit, Decimal("100.000"))
		self.assertEqual(statement_row.credit, Decimal("0.000"))
		self.assertEqual(statement_row.running_balance, Decimal("100.000"))

		_, report_rows = sales_report(
			{
				"business": self.business.name,
				"from_date": "2026-08-20",
				"to_date": "2026-08-20",
			}
		)
		self.assertEqual([row.sale for row in report_rows], [sale.name])

	def test_02_customer_payment_reduces_balance_and_updates_statement(self):
		sale = self.submit_credit_sale()
		payment = self.submit_payment()

		self.assertEqual(self.customer_balance(), Decimal("60.000"))
		rows = self.statement_rows()
		payment_row = next(row for row in rows if row.reference == payment.name)
		self.assertEqual(payment_row.transaction_type, "PAYMENT")
		self.assertEqual(payment_row.debit, Decimal("0.000"))
		self.assertEqual(payment_row.credit, Decimal("40.000"))
		self.assertEqual(payment_row.running_balance, Decimal("60.000"))
		self.assertEqual([row.reference for row in rows], [sale.name, payment.name])

	def test_03_cash_sale_reduces_stock_without_creating_customer_debt(self):
		debt_before = self.customer_balance()
		sale = self.submit_sale(
			business_date="2026-08-21",
			sale_mode="CASH",
			amount_paid="100",
			payment_method="CASH",
		)

		self.assertEqual(self.stock_balance(), Decimal("8"))
		self.assertEqual(self.customer_balance(), debt_before)
		self.assertEqual(
			frappe.db.count("Credit Transaction", {"source_sale": sale.name}), 0
		)
		self.assertEqual(
			frappe.db.count(
				"Stock Movement", {"source_sale": sale.name, "docstatus": 1}
			),
			1,
		)
		self.assertEqual(frappe.db.get_value("Sale", sale.name, "docstatus"), 1)
		_, report_rows = sales_report(
			{
				"business": self.business.name,
				"customer": self.customer.name,
				"from_date": "2026-08-21",
				"to_date": "2026-08-21",
			}
		)
		self.assertEqual([row.sale for row in report_rows], [sale.name])

	def test_04_sale_reversal_restores_stock_and_reverses_only_sale_debt(self):
		sale = self.submit_credit_sale()
		payment = self.submit_payment()
		reversal = self.submit_reversal(sale)

		self.assertEqual(self.stock_balance(), Decimal("10"))
		self.assertEqual(self.customer_balance(), Decimal("-40.000"))
		self.assertEqual(frappe.db.get_value("Sale", sale.name, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Payment", payment.name, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Sale Reversal", reversal.name, "docstatus"), 1)
		self.assertEqual(
			frappe.db.count(
				"Credit Transaction",
				{"source_sale_reversal": reversal.name, "docstatus": 1},
			),
			1,
		)
		self.assertEqual(
			frappe.db.count(
				"Stock Movement", {"source_sale_reversal": reversal.name, "docstatus": 1}
			),
			1,
		)

	def test_05_second_sale_reversal_is_rejected_without_duplicate_effects(self):
		sale = self.submit_credit_sale()
		first_reversal = self.submit_reversal(sale)

		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": "Sale Reversal",
					"business": self.business.name,
					"original_sale": sale.name,
					"business_date": "2026-08-22",
					"reason": "Duplicate reversal attempt",
				}
			).insert()

		self.assertEqual(self.stock_balance(), Decimal("10"))
		self.assertEqual(self.customer_balance(), Decimal("0.000"))
		self.assertEqual(
			frappe.db.count(
				"Stock Movement",
				{"source_sale_reversal": first_reversal.name, "docstatus": 1},
			),
			1,
		)
		self.assertEqual(
			frappe.db.count(
				"Credit Transaction",
				{"source_sale_reversal": first_reversal.name, "docstatus": 1},
			),
			1,
		)

	def test_06_double_submit_is_idempotent_for_sale_and_payment(self):
		sale = self.submit_credit_sale()
		sale.submit()
		payment = self.submit_payment()
		payment.submit()

		self.assertEqual(
			frappe.db.count(
				"Credit Transaction", {"source_sale": sale.name, "docstatus": 1}
			),
			1,
		)
		self.assertEqual(
			frappe.db.count(
				"Stock Movement", {"source_sale": sale.name, "docstatus": 1}
			),
			1,
		)
		self.assertEqual(
			frappe.db.count(
				"Credit Transaction", {"source_payment": payment.name, "docstatus": 1}
			),
			1,
		)
		self.assertEqual(self.stock_balance(), Decimal("8"))
		self.assertEqual(self.customer_balance(), Decimal("60.000"))

	def test_07_cross_business_customer_is_rejected_server_side(self):
		other_business = frappe.get_doc(
			{"doctype": "Business", "business_name": f"Foreign {uuid4().hex[:10]}"}
		).insert()
		other_customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"business": other_business.name,
				"customer_name": f"Foreign Customer {uuid4().hex[:10]}",
			}
		).insert()

		with self.assertRaises(frappe.ValidationError):
			self.make_sale(customer=other_customer.name)

		self.assertEqual(self.stock_balance(), Decimal("10"))
		self.assertEqual(self.customer_balance(), Decimal("0.000"))

	def test_08_sales_report_filters_totals_and_reversal_treatment(self):
		credit_sale = self.submit_credit_sale(business_date="2026-08-20")
		cash_sale = self.submit_sale(
			business_date="2026-08-21",
			sale_mode="CASH",
			amount_paid="100",
			payment_method="CASH",
		)
		self.submit_reversal(credit_sale, business_date="2026-08-22")

		_, rows = sales_report(
			{
				"business": self.business.name,
				"customer": self.customer.name,
				"from_date": "2026-08-20",
				"to_date": "2026-08-21",
			}
		)
		by_sale = {row.sale: row for row in rows}
		self.assertEqual(set(by_sale), {credit_sale.name, cash_sale.name})
		self.assertEqual(by_sale[credit_sale.name].returned_amount, Decimal("100.000"))
		self.assertEqual(by_sale[credit_sale.name].net_sale_value, Decimal("0.000"))
		self.assertEqual(by_sale[cash_sale.name].returned_amount, Decimal("0.000"))
		self.assertEqual(by_sale[cash_sale.name].net_sale_value, Decimal("100.000"))
		self.assertEqual(sum(row.total_ttc for row in rows), Decimal("200.000"))
		self.assertEqual(sum(row.returned_amount for row in rows), Decimal("100.000"))
		self.assertEqual(sum(row.net_sale_value for row in rows), Decimal("100.000"))

		_, outside_range = sales_report(
			{
				"business": self.business.name,
				"customer": self.customer.name,
				"from_date": "2026-08-22",
				"to_date": "2026-08-22",
			}
		)
		self.assertFalse(outside_range)
		_, wrong_customer = sales_report(
			{
				"business": self.business.name,
				"customer": "NONEXISTENT-CUSTOMER",
				"from_date": "2026-08-20",
				"to_date": "2026-08-21",
			}
		)
		self.assertFalse(wrong_customer)

	def test_09_product_performance_filters_and_excludes_reversed_sales(self):
		credit_sale = self.submit_credit_sale(business_date="2026-08-20")
		self.submit_sale(
			business_date="2026-08-21",
			sale_mode="CASH",
			amount_paid="100",
			payment_method="CASH",
		)
		self.submit_reversal(credit_sale, business_date="2026-08-22")

		_, rows = product_performance(
			{
				"business": self.business.name,
				"product": self.product.name,
				"from_date": "2026-08-20",
				"to_date": "2026-08-21",
			}
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].product, self.product.name)
		self.assertEqual(rows[0].quantity_sold, Decimal("2"))
		self.assertEqual(rows[0].net_quantity_sold, Decimal("2"))
		self.assertEqual(rows[0].gross_sales_value, Decimal("100.000"))
		self.assertEqual(rows[0].net_sales_value, Decimal("100.000"))

		_, outside_range = product_performance(
			{
				"business": self.business.name,
				"product": self.product.name,
				"from_date": "2026-08-18",
				"to_date": "2026-08-18",
			}
		)
		self.assertFalse(outside_range)
		_, wrong_product = product_performance(
			{
				"business": self.business.name,
				"product": "NONEXISTENT-PRODUCT",
				"from_date": "2026-08-20",
				"to_date": "2026-08-21",
			}
		)
		self.assertFalse(wrong_product)

	def test_10_customer_ledger_statement_and_balances_reconcile(self):
		sale = self.submit_credit_sale()
		payment = self.submit_payment()
		reversal = self.submit_reversal(sale)

		ledger_balance = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(CASE WHEN direction='DEBIT' THEN amount ELSE -amount END), 0)
			FROM `tabCredit Transaction`
			WHERE business=%s AND customer=%s AND docstatus=1
			""",
			(self.business.name, self.customer.name),
		)[0][0]
		statement = self.statement_rows()
		balances = self.balance_row()

		self.assertEqual(Decimal(str(ledger_balance)), Decimal("-40.000"))
		self.assertEqual(statement[-1].reference, reversal.name)
		self.assertEqual(statement[-1].running_balance, Decimal("-40.000"))
		self.assertEqual(balances.total_debit, Decimal("100.000"))
		self.assertEqual(balances.total_credit, Decimal("140.000"))
		self.assertEqual(balances.outstanding_balance, Decimal("-40.000"))
		self.assertEqual(self.customer_balance(), Decimal("-40.000"))
		self.assertEqual(
			[row.reference for row in statement], [sale.name, payment.name, reversal.name]
		)
