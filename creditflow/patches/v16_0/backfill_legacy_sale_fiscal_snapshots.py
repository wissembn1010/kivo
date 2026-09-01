import frappe


def execute():
    """Preserve legacy economics without pretending their historical TVA is known."""
    frappe.db.sql(
        """
        UPDATE `tabSale`
        SET total_ttc = COALESCE(total_amount, 0),
            total_ht = COALESCE(total_amount, 0),
            total_tva = 0,
            legacy_fiscal_data = 1
        WHERE docstatus = 1 AND (invoice_number IS NULL OR invoice_number = '')
        """
    )
    frappe.db.sql(
        """
        UPDATE `tabSale Item` item
        INNER JOIN `tabSale` sale ON sale.name = item.parent
        SET item.product_name = COALESCE(
                (SELECT product_name FROM `tabProduct` WHERE name = item.product),
                item.product
            ),
            item.line_ht = ROUND(COALESCE(item.quantity, 0) * COALESCE(item.unit_price, 0), 3),
            item.tva_rate = 0,
            item.tva_amount = 0,
            item.line_ttc = ROUND(COALESCE(item.quantity, 0) * COALESCE(item.unit_price, 0), 3)
        WHERE sale.docstatus = 1 AND sale.legacy_fiscal_data = 1
        """
    )
