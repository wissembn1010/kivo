from decimal import Decimal

import frappe
from frappe.model.document import Document


class Customer(Document):

    @frappe.whitelist()
    def get_ledger_balance(self):
        if self.is_new() or not self.business:
            return "0.000"

        result = frappe.db.sql(
            """
            SELECT COALESCE(
                SUM(
                    CASE
                        WHEN direction = 'DEBIT' THEN amount
                        WHEN direction = 'CREDIT' THEN -amount
                        ELSE 0
                    END
                ),
                0
            )
            FROM `tabCredit Transaction`
            WHERE customer = %s
              AND business = %s
              AND docstatus = 1
            """,
            (self.name, self.business),
        )

        balance = result[0][0] if result else 0

        return str(
            Decimal(str(balance or 0)).quantize(
                Decimal("0.001")
            )
        )