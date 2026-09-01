import frappe


def execute():
	frappe.db.sql(
		"""
		UPDATE `tabPayment`
		SET payment_method = 'CASH'
		WHERE payment_method IS NULL OR payment_method = ''
		"""
	)
