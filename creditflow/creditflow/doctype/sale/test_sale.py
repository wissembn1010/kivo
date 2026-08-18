from decimal import Decimal
from uuid import uuid4

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestSale(IntegrationTestCase):
        def setUp(self):
                frappe.set_user("Administrator")
                suffix = uuid4().hex[:10]
                self.business = frappe.get_doc(
                        {"doctype": "Business", "business_name": f"Sale Test Business {suffix}"}
                ).insert()
                self.other_business = frappe.get_doc(
                        {"doctype": "Business", "business_name": f"Other Sale Test Business {suffix}"}
                ).insert()
                self.customer = frappe.get_doc(
                        {
                                "doctype": "Customer",
                                "customer_name": f"Sale Test Customer {suffix}",
                                "business": self.business.name,
                        }
                ).insert()
                self.other_customer = frappe.get_doc(
                        {
                                "doctype": "Customer",
                                "customer_name": f"Other Sale Test Customer {suffix}",
                                "business": self.other_business.name,
                        }
                ).insert()
                self.product = self.make_product(self.business.name, f"SALE-TEST-{suffix}")
                self.other_product = self.make_product(
                        self.other_business.name, f"SALE-TEST-OTHER-{suffix}"
                )

        def make_product(self, business, reference):
                return frappe.get_doc(
                        {
                                "doctype": "Product",
                                "business": business,
                                "product_name": reference,
                                "reference": reference,
                        }
                ).insert()

        def tearDown(self):
                frappe.set_user("Administrator")

        def make_role_user(self, role):
                email = f"sale-{role.lower()}-{uuid4().hex[:10]}@example.com"
                user = frappe.get_doc(
                        {
                                "doctype": "User",
                                "email": email,
                                "first_name": f"Sale {role.title()}",
                                "send_welcome_email": 0,
                                "creditflow_business": self.business.name,
                        }
                ).insert(ignore_permissions=True)
                user.add_roles(role)
                return user.name

        def post_customer_debt(self, amount):
                transaction = frappe.get_doc(
                        {
                                "doctype": "Credit Transaction",
                                "business": self.business.name,
                                "customer": self.customer.name,
                                "transaction_type": "OPENING_BALANCE",
                                "direction": "DEBIT",
                                "amount": amount,
                                "transaction_date": frappe.utils.today(),
                        }
                ).insert()
                transaction.submit()
                return transaction

        def make_sale(
                self,
                customer=None,
                product=None,
                quantity="1",
                unit_price="1",
                sale_mode="CREDIT",
                amount_paid="0",
                payment_method=None,
                payment_reference=None,
        ):
                sale = frappe.get_doc(
                        {
                                "doctype": "Sale",
                                "business": self.business.name,
                                "business_date": frappe.utils.today(),
                                "sale_mode": sale_mode,
                                "amount_paid": amount_paid,
                        }
                )
                if customer is not None:
                        sale.customer = customer
                elif sale_mode != "CASH":
                        sale.customer = self.customer.name
                if payment_method:
                        sale.payment_method = payment_method
                if payment_reference:
                        sale.payment_reference = payment_reference
                sale.append(
                        "items",
                        {
                                "product": product or self.product.name,
                                "quantity": quantity,
                                "unit_price": unit_price,
                        },
                )
                return sale

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
                return movement

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

        def test_valid_sale_and_total_calculation(self):
                sale = self.make_sale(quantity="1.234", unit_price="2.3456")
                sale.validate()
                self.assertEqual(Decimal(str(sale.total_amount)), Decimal("2.894"))

        def test_zero_quantity_is_rejected(self):
                with self.assertRaises(frappe.ValidationError):
                        self.make_sale(quantity="0").validate()

        def test_product_from_another_business_is_rejected(self):
                with self.assertRaises(frappe.ValidationError):
                        self.make_sale(product=self.other_product.name).validate()

        def test_customer_from_another_business_is_rejected(self):
                with self.assertRaises(frappe.ValidationError):
                        self.make_sale(customer=self.other_customer.name).validate()

        def test_successful_sale_posts_ledger_and_stock(self):
                self.add_stock(self.product.name, "10")
                sale = self.make_sale(quantity="3", unit_price="2.345")
                sale.insert()
                sale.submit()

                transactions = frappe.get_all(
                        "Credit Transaction",
                        filters={"source_sale": sale.name},
                        fields=["docstatus", "transaction_type", "direction", "amount", "source_sale"],
                )
                self.assertEqual(len(transactions), 1)
                self.assertEqual(transactions[0].docstatus, 1)
                self.assertEqual(transactions[0].transaction_type, "SALE")
                self.assertEqual(transactions[0].direction, "DEBIT")
                self.assertEqual(
                        Decimal(str(transactions[0].amount)), Decimal(str(sale.total_amount))
                )
                self.assertEqual(transactions[0].source_sale, sale.name)
                self.assertEqual(sale.sale_mode, "CREDIT")
                self.assertEqual(Decimal(str(sale.amount_paid)), Decimal("0.000"))
                self.assertEqual(Decimal(str(sale.outstanding_amount)), Decimal(str(sale.total_amount)))

                movements = frappe.get_all(
                        "Stock Movement",
                        filters={"source_sale": sale.name},
                        fields=["docstatus", "direction", "quantity", "movement_reason", "source_sale"],
                )
                self.assertEqual(len(movements), 1)
                self.assertEqual(movements[0].docstatus, 1)
                self.assertEqual(movements[0].direction, "OUT")
                self.assertEqual(movements[0].movement_reason, "SALE")
                self.assertEqual(movements[0].source_sale, sale.name)
                self.assertEqual(self.get_stock(self.product.name), Decimal("7"))
                self.assertEqual(
                        Decimal(self.customer.get_ledger_balance()), Decimal(str(sale.total_amount))
                )

        def test_insufficient_stock_has_no_side_effects(self):
                self.add_stock(self.product.name, "2")
                sale = self.make_sale(quantity="3")
                sale.insert()

                with self.assertRaises(frappe.ValidationError):
                        sale.submit()

                self.assertEqual(
                        frappe.db.count("Credit Transaction", {"source_sale": sale.name}), 0
                )
                self.assertEqual(frappe.db.count("Stock Movement", {"source_sale": sale.name}), 0)
                self.assertEqual(self.get_stock(self.product.name), Decimal("2"))

        def test_multiple_products_post_correct_movements(self):
                second_product = self.make_product(
                        self.business.name, f"SALE-TEST-SECOND-{uuid4().hex[:10]}"
                )
                self.add_stock(self.product.name, "10")
                self.add_stock(second_product.name, "8")
                sale = self.make_sale(quantity="3", unit_price="2")
                sale.append(
                        "items",
                        {"product": second_product.name, "quantity": "5", "unit_price": "4"},
                )
                sale.insert()
                sale.submit()

                self.assertEqual(
                        frappe.db.count("Stock Movement", {"source_sale": sale.name}), 2
                )
                self.assertEqual(self.get_stock(self.product.name), Decimal("7"))
                self.assertEqual(self.get_stock(second_product.name), Decimal("3"))
                self.assertEqual(Decimal(str(sale.total_amount)), Decimal("26.000"))

        def test_same_product_rows_are_aggregated_for_stock_check(self):
                self.add_stock(self.product.name, "6")
                sale = self.make_sale(quantity="4")
                sale.append(
                        "items",
                        {"product": self.product.name, "quantity": "3", "unit_price": "1"},
                )
                sale.insert()

                with self.assertRaises(frappe.ValidationError):
                        sale.submit()

                self.assertEqual(
                        frappe.db.count("Credit Transaction", {"source_sale": sale.name}), 0
                )
                self.assertEqual(frappe.db.count("Stock Movement", {"source_sale": sale.name}), 0)
                self.assertEqual(self.get_stock(self.product.name), Decimal("6"))

        def test_posted_sale_cannot_be_cancelled(self):
                self.add_stock(self.product.name, "2")
                sale = self.make_sale(quantity="1")
                sale.insert()
                sale.submit()

                with self.assertRaises(frappe.ValidationError):
                        sale.cancel()

        def test_full_cash_sale_posts_no_customer_debt(self):
                self.add_stock(self.product.name, "10")
                sale = self.make_sale(customer=None, quantity="3", unit_price="2", sale_mode="CASH", amount_paid="6", payment_method="CASH")
                sale.insert()
                sale.submit()

                self.assertEqual(sale.sale_mode, "CASH")
                self.assertEqual(Decimal(str(sale.amount_paid)), Decimal("6.000"))
                self.assertEqual(Decimal(str(sale.outstanding_amount)), Decimal("0.000"))
                self.assertEqual(frappe.db.count("Credit Transaction", {"source_sale": sale.name}), 0)
                self.assertEqual(self.get_stock(self.product.name), Decimal("7"))

        def test_mixed_sale_posts_partial_customer_debt(self):
                self.add_stock(self.product.name, "10")
                sale = self.make_sale(
                        quantity="3",
                        unit_price="100",
                        sale_mode="MIXED",
                        amount_paid="100",
                        payment_method="CASH",
                )
                sale.insert()
                sale.submit()

                transactions = frappe.get_all(
                        "Credit Transaction",
                        filters={"source_sale": sale.name},
                        fields=["transaction_type", "direction", "amount"],
                )
                self.assertEqual(len(transactions), 1)
                self.assertEqual(Decimal(str(transactions[0].amount)), Decimal("200.000"))
                self.assertEqual(Decimal(self.customer.get_ledger_balance()), Decimal("200.000"))
                self.assertEqual(Decimal(str(sale.outstanding_amount)), Decimal("200.000"))

        def test_credit_sale_without_customer_is_rejected(self):
                sale = self.make_sale(customer=None)
                sale.customer = None
                sale.sale_mode = "CREDIT"
                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_mixed_sale_without_customer_is_rejected(self):
                sale = self.make_sale(customer=None, sale_mode="MIXED", amount_paid="10", payment_method="CASH")
                sale.customer = None
                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_cash_walk_in_sale_succeeds(self):
                self.add_stock(self.product.name, "5")
                sale = self.make_sale(
                        customer=None,
                        quantity="2",
                        unit_price="1",
                        sale_mode="CASH",
                        amount_paid="2",
                        payment_method="CASH",
                )
                sale.insert()
                sale.submit()

                self.assertFalse(sale.customer)
                self.assertEqual(frappe.db.count("Credit Transaction", {"source_sale": sale.name}), 0)

        def test_amount_paid_greater_than_total_is_rejected(self):
                sale = self.make_sale(quantity="1", unit_price="10", sale_mode="CASH", amount_paid="11", payment_method="CASH")
                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_invalid_cash_amount_is_rejected(self):
                sale = self.make_sale(quantity="1", unit_price="10", sale_mode="CASH", amount_paid="9", payment_method="CASH")
                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_invalid_credit_amount_is_rejected(self):
                sale = self.make_sale(quantity="1", unit_price="10", sale_mode="CREDIT", amount_paid="1")
                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_invalid_mixed_amounts_are_rejected(self):
                with self.assertRaises(frappe.ValidationError):
                        self.make_sale(quantity="1", unit_price="10", sale_mode="MIXED", amount_paid="0", payment_method="CASH").validate()
                with self.assertRaises(frappe.ValidationError):
                        self.make_sale(quantity="1", unit_price="10", sale_mode="MIXED", amount_paid="10", payment_method="CASH").validate()

        def test_product_selling_price_defaults_when_unit_price_is_blank(self):
                pricey_product = self.make_product(self.business.name, f"DEFAULT-PRICE-{uuid4().hex[:10]}")
                frappe.db.set_value("Product", pricey_product.name, "selling_price", "12.345")
                sale = self.make_sale(product=pricey_product.name, unit_price="", quantity="1")
                sale.items[0].unit_price = ""
                sale.validate_items_and_calculate_total()
                self.assertEqual(Decimal(str(sale.items[0].unit_price)), Decimal("12.345"))
                self.assertEqual(Decimal(str(sale.total_amount)), Decimal("12.345"))

        # STEP 4A — pricing and minimum-price protection

        def test_retail_customer_defaults_retail_price(self):
                frappe.db.set_value("Product", self.product.name, "selling_price", "100")
                frappe.db.set_value("Product", self.product.name, "professional_price", "80")

                sale = self.make_sale(unit_price="")
                sale.items[0].unit_price = ""
                sale.validate()

                self.assertEqual(Decimal(str(sale.items[0].base_price)), Decimal("100.000"))
                self.assertEqual(Decimal(str(sale.items[0].unit_price)), Decimal("100.000"))

        def test_professional_customer_defaults_professional_price(self):
                frappe.db.set_value("Customer", self.customer.name, "price_type", "PROFESSIONAL")
                frappe.db.set_value("Product", self.product.name, "selling_price", "100")
                frappe.db.set_value("Product", self.product.name, "professional_price", "80")

                sale = self.make_sale(unit_price="")
                sale.items[0].unit_price = ""
                sale.validate()

                self.assertEqual(Decimal(str(sale.items[0].base_price)), Decimal("80.000"))
                self.assertEqual(Decimal(str(sale.items[0].unit_price)), Decimal("80.000"))

        def test_professional_price_zero_falls_back_to_retail(self):
                frappe.db.set_value("Customer", self.customer.name, "price_type", "PROFESSIONAL")
                frappe.db.set_value("Product", self.product.name, "selling_price", "125")
                frappe.db.set_value("Product", self.product.name, "professional_price", "0")

                sale = self.make_sale(unit_price="")
                sale.items[0].unit_price = ""
                sale.validate()

                self.assertEqual(Decimal(str(sale.items[0].base_price)), Decimal("125.000"))
                self.assertEqual(Decimal(str(sale.items[0].unit_price)), Decimal("125.000"))

        def test_discount_calculates_final_unit_price(self):
                frappe.db.set_value("Product", self.product.name, "selling_price", "100")

                sale = self.make_sale(unit_price="")
                sale.items[0].unit_price = ""
                sale.items[0].discount_percent = "10"
                sale.validate()

                self.assertEqual(Decimal(str(sale.items[0].base_price)), Decimal("100.000"))
                self.assertEqual(Decimal(str(sale.items[0].unit_price)), Decimal("90.000"))
                self.assertEqual(Decimal(str(sale.total_amount)), Decimal("90.000"))

        def test_negative_discount_is_rejected(self):
                sale = self.make_sale(unit_price="")
                sale.items[0].unit_price = ""
                sale.items[0].discount_percent = "-1"

                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_discount_over_100_is_rejected(self):
                sale = self.make_sale(unit_price="")
                sale.items[0].unit_price = ""
                sale.items[0].discount_percent = "101"

                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_staff_cannot_sell_below_minimum_price(self):
                frappe.db.set_value("Product", self.product.name, "minimum_selling_price", "90")

                staff = self.make_role_user("STAFF")
                frappe.set_user(staff)

                sale = self.make_sale(unit_price="80")

                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_administrator_minimum_price_override_requires_reason(self):
                frappe.db.set_value("Product", self.product.name, "minimum_selling_price", "90")

                sale = self.make_sale(unit_price="80")
                sale.price_override = 1

                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_administrator_can_override_minimum_price_with_reason(self):
                frappe.db.set_value("Product", self.product.name, "minimum_selling_price", "90")

                sale = self.make_sale(unit_price="80")
                sale.price_override = 1
                sale.price_override_reason = "Approved special deal"
                sale.validate()

                self.assertEqual(Decimal(str(sale.items[0].unit_price)), Decimal("80.000"))

        # STEP 4A — customer credit-limit protection

        def test_credit_sale_below_limit_succeeds(self):
                frappe.db.set_value("Customer", self.customer.name, "credit_limit", "100")
                self.post_customer_debt("70")

                sale = self.make_sale(
                        quantity="1",
                        unit_price="20",
                        sale_mode="CREDIT",
                        amount_paid="0",
                )
                sale.validate()

                self.assertEqual(
                        Decimal(str(sale.outstanding_amount)),
                        Decimal("20.000"),
                )

        def test_staff_cannot_exceed_credit_limit(self):
                frappe.db.set_value("Customer", self.customer.name, "credit_limit", "100")
                self.post_customer_debt("80")

                staff = self.make_role_user("STAFF")
                frappe.set_user(staff)

                sale = self.make_sale(
                        quantity="1",
                        unit_price="30",
                        sale_mode="CREDIT",
                        amount_paid="0",
                )

                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_mixed_sale_checks_only_outstanding_amount(self):
                frappe.db.set_value("Customer", self.customer.name, "credit_limit", "100")
                self.post_customer_debt("80")

                sale = self.make_sale(
                        quantity="1",
                        unit_price="100",
                        sale_mode="MIXED",
                        amount_paid="85",
                        payment_method="CASH",
                )
                sale.validate()

                self.assertEqual(
                        Decimal(str(sale.outstanding_amount)),
                        Decimal("15.000"),
                )

        def test_cash_sale_ignores_credit_limit(self):
                frappe.db.set_value("Customer", self.customer.name, "credit_limit", "50")
                self.post_customer_debt("100")

                sale = self.make_sale(
                        customer=self.customer.name,
                        quantity="1",
                        unit_price="20",
                        sale_mode="CASH",
                        amount_paid="20",
                        payment_method="CASH",
                )
                sale.validate()

                self.assertEqual(
                        Decimal(str(sale.outstanding_amount)),
                        Decimal("0.000"),
                )

        def test_credit_override_without_reason_is_rejected(self):
                frappe.db.set_value("Customer", self.customer.name, "credit_limit", "100")
                self.post_customer_debt("80")

                sale = self.make_sale(
                        quantity="1",
                        unit_price="30",
                        sale_mode="CREDIT",
                        amount_paid="0",
                )
                sale.credit_limit_override = 1

                with self.assertRaises(frappe.ValidationError):
                        sale.validate()

        def test_administrator_can_override_credit_limit_with_reason(self):
                frappe.db.set_value("Customer", self.customer.name, "credit_limit", "100")
                self.post_customer_debt("80")

                sale = self.make_sale(
                        quantity="1",
                        unit_price="30",
                        sale_mode="CREDIT",
                        amount_paid="0",
                )
                sale.credit_limit_override = 1
                sale.credit_override_reason = "Temporary credit approved"
                sale.validate()

                self.assertEqual(
                        Decimal(str(sale.outstanding_amount)),
                        Decimal("30.000"),
                )

        def test_owner_can_override_credit_limit_with_reason(self):
                frappe.db.set_value("Customer", self.customer.name, "credit_limit", "100")
                self.post_customer_debt("80")

                owner = self.make_role_user("OWNER")
                frappe.set_user(owner)

                sale = self.make_sale(
                        quantity="1",
                        unit_price="30",
                        sale_mode="CREDIT",
                        amount_paid="0",
                )
                sale.credit_limit_override = 1
                sale.credit_override_reason = "Owner approved customer credit"
                sale.validate()

                self.assertEqual(
                        Decimal(str(sale.outstanding_amount)),
                        Decimal("30.000"),
                )
