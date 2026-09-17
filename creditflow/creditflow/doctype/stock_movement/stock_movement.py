from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document

from creditflow.uom import get_row_base_quantity


SYSTEM_REASONS = {"PURCHASE", "SALE", "CUSTOMER_RETURN", "REVERSAL"}
MANUAL_REASONS = {"OPENING_STOCK", "MANUAL_ADJUSTMENT"}
VALID_DIRECTIONS = {"IN", "OUT"}
SOURCE_FIELDS = ("source_sale", "source_purchase", "source_sale_reversal", "source_sale_return", "source_sale_return_item", "source_purchase_reversal")


class StockMovement(Document):
	def validate(self):
		self.validate_business_and_product()
		self.validate_quantity()
		self.validate_direction_and_reason()
		self.validate_source_integrity()

	def before_submit(self):
		if self.movement_reason not in MANUAL_REASONS or self.direction != "OUT":
			return
		# Serialize with Sales, then read committed stock even from a stale snapshot.
		frappe.db.sql("SELECT name FROM `tabProduct` WHERE name = %s FOR UPDATE", self.product)
		available = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(CASE WHEN direction = 'IN' THEN quantity
				WHEN direction = 'OUT' THEN -quantity ELSE 0 END), 0)
			FROM `tabStock Movement`
			WHERE business = %s AND product = %s AND docstatus = 1
			FOR UPDATE
			""",
			(self.business, self.product),
		)[0][0]
		if Decimal(str(available or 0)) < Decimal(str(self.quantity)):
			frappe.throw(_("Insufficient stock for Product {0}: available {1}, required {2}.").format(
				self.product, available, self.quantity
			))

	def validate_business_and_product(self):
		if not self.business or not frappe.db.exists("Business", self.business):
			frappe.throw(_("Business does not exist."))
		if not self.product or not frappe.db.exists("Product", self.product):
			frappe.throw(_("Product does not exist."))
		if frappe.db.get_value("Product", self.product, "business") != self.business:
			frappe.throw(_("Product must belong to the same Business as the Stock Movement."))

	def validate_quantity(self):
		try:
			quantity = Decimal(str(self.quantity))
		except (InvalidOperation, TypeError, ValueError):
			frappe.throw(_("Quantity must be a valid number."))
		if quantity <= 0:
			frappe.throw(_("Quantity must be greater than 0."))

	def validate_direction_and_reason(self):
		if self.direction not in VALID_DIRECTIONS:
			frappe.throw(_("Direction must be IN or OUT."))
		if self.movement_reason not in SYSTEM_REASONS | MANUAL_REASONS:
			frappe.throw(_("Movement Reason is not supported by an implemented workflow."))

	def validate_source_integrity(self):
		if self.movement_reason in MANUAL_REASONS:
			if any(self.get(field) for field in SOURCE_FIELDS) or self.reversal_of:
				frappe.throw(_("Manual Stock Movements cannot contain system source links."))
			return
		if not self.flags.get("creditflow_system_generated"):
			frappe.throw(_("{0} Stock Movements can only be created by their source document.").format(self.movement_reason))
		if self.movement_reason == "SALE":
			self.validate_source_movement("Sale", "source_sale", "OUT")
		elif self.movement_reason == "PURCHASE":
			self.validate_source_movement("Purchase", "source_purchase", "IN")
		elif self.movement_reason == "CUSTOMER_RETURN":
			self.validate_customer_return()
		else:
			self.validate_reversal_movement()

	def require_only_source(self, expected_field):
		if not self.get(expected_field) or any(self.get(field) for field in SOURCE_FIELDS if field != expected_field):
			frappe.throw(_("Stock Movement source fields do not match its Movement Reason."))

	def validate_source_movement(self, doctype, source_field, direction):
		self.require_only_source(source_field)
		if self.direction != direction or self.reversal_of:
			frappe.throw(_("{0} Stock Movement direction or reversal link is invalid.").format(doctype))
		source_name = self.get(source_field)
		source = frappe.get_doc(doctype, source_name)
		if source.docstatus == 2 or source.business != self.business:
			frappe.throw(_("Stock Movement does not match its source {0}.").format(doctype))
		movement_quantity = Decimal(str(self.quantity))
		matching_rows = [
			row
			for row in source.items
			if row.product == self.product
			and get_row_base_quantity(row) == movement_quantity
		]
		if not matching_rows:
			frappe.throw(_("Stock Movement Product and Quantity must match a source item row."))
		filters = {source_field: source_name, "product": self.product, "quantity": self.quantity, "docstatus": ["!=", 2]}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		if frappe.db.count("Stock Movement", filters) >= len(matching_rows):
			frappe.throw(_("A Stock Movement already exists for this source item row."))

	def validate_customer_return(self):
		if not self.source_sale_return or not self.source_sale_return_item:
			frappe.throw(_("Customer Return Stock Movements require their Sale Return row source."))
		if any(self.get(field) for field in SOURCE_FIELDS if field not in {"source_sale_return", "source_sale_return_item"}):
			frappe.throw(_("Stock Movement source fields do not match CUSTOMER_RETURN."))
		if self.direction != "IN" or self.reversal_of:
			frappe.throw(_("Customer Return Stock Movement must restore stock with direction IN."))
		return_doc = frappe.get_doc("Sale Return", self.source_sale_return)
		rows = [row for row in return_doc.items if row.name == self.source_sale_return_item]
		if return_doc.docstatus == 2 or return_doc.business != self.business or len(rows) != 1:
			frappe.throw(_("Stock Movement must match its Sale Return source."))
		row = rows[0]
		if row.product != self.product or Decimal(str(row.base_quantity)) != Decimal(str(self.quantity)):
			frappe.throw(_("Stock Movement Product and Quantity must match its Sale Return row."))
		filters = {"source_sale_return_item": row.name, "docstatus": ["!=", 2]}
		if not self.is_new(): filters["name"] = ["!=", self.name]
		if frappe.db.exists("Stock Movement", filters):
			frappe.throw(_("A Stock Movement already exists for this Sale Return row."))

	def validate_reversal_movement(self):
		if not self.reversal_of:
			frappe.throw(_("REVERSAL Stock Movements must reference the original movement."))
		original = frappe.db.get_value("Stock Movement", self.reversal_of, ["business", "product", "direction", "quantity", "docstatus", "source_sale", "source_purchase"], as_dict=True)
		if not original or original.docstatus != 1:
			frappe.throw(_("Reversal Of must be a submitted Stock Movement."))
		if original.business != self.business or original.product != self.product or Decimal(str(original.quantity)) != Decimal(str(self.quantity)):
			frappe.throw(_("Reversal must match the original movement Business, Product, and Quantity."))
		if self.source_sale_reversal:
			self.require_only_source("source_sale_reversal")
			if self.direction != "IN" or original.direction != "OUT" or not original.source_sale:
				frappe.throw(_("Sale reversal movement does not match the original Sale movement."))
			self.validate_reversal_source("Sale Reversal", self.source_sale_reversal, "original_sale", original.source_sale)
		elif self.source_purchase_reversal:
			self.require_only_source("source_purchase_reversal")
			if self.direction != "OUT" or original.direction != "IN" or not original.source_purchase:
				frappe.throw(_("Purchase reversal movement does not match the original Purchase movement."))
			self.validate_reversal_source("Purchase Reversal", self.source_purchase_reversal, "original_purchase", original.source_purchase)
		else:
			frappe.throw(_("REVERSAL Stock Movements require a Sale or Purchase Reversal source."))
		filters = {"reversal_of": self.reversal_of, "docstatus": ["!=", 2]}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		if frappe.db.exists("Stock Movement", filters):
			frappe.throw(_("This Stock Movement has already been reversed."))

	def validate_reversal_source(self, doctype, source_name, original_field, original_name):
		source = frappe.get_doc(doctype, source_name)
		if source.docstatus == 2 or source.business != self.business or source.get(original_field) != original_name:
			frappe.throw(_("Stock Movement does not match its reversal source."))

	def before_cancel(self):
		frappe.throw(_("Posted stock ledger entries cannot be cancelled. Create a reversal instead."))
