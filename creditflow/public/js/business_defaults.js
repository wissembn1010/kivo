function set_default_business(frm) {
        if (frm.is_new() && !frm.doc.business && frappe.session.user !== "Administrator") {
                frappe.db.get_value("User", frappe.session.user, "creditflow_business").then(({ message }) => {
                        if (message?.creditflow_business && !frm.doc.business) {
                                frm.set_value("business", message.creditflow_business);
                        }
                });
        }
}

function flt(value) {
        return Number.parseFloat(value || 0) || 0;
}

function set_if_changed(frm, fieldname, value) {
        const fieldtype = frm.fields_dict[fieldname]?.df?.fieldtype;
        const numeric_fieldtypes = ["Currency", "Float", "Int", "Percent"];

        if (numeric_fieldtypes.includes(fieldtype)) {
                const current = flt(frm.doc[fieldname]);
                const next = flt(value);

                if (Math.abs(current - next) > 0.000001) {
                        frm.set_value(fieldname, next);
                }
                return;
        }

        const current = frm.doc[fieldname] ?? "";
        const next = value ?? "";

        if (current !== next) {
                frm.set_value(fieldname, next);
        }
}

function recalculate_table_total(frm, child_field, quantity_field, rate_field, total_field) {
        const total = (frm.doc[child_field] || []).reduce((running, row) => {
                return running + flt(row[quantity_field]) * flt(row[rate_field]);
        }, 0);

        set_if_changed(frm, total_field, total.toFixed(3));
}

function user_can_override() {
        return (
                frappe.session.user === "Administrator" ||
                (frappe.user_roles || []).includes("OWNER")
        );
}

function row_has_manual_price(row) {
        return !!row.__manual_unit_price;
}

async function load_customer_context(frm) {
        if (!frm.doc.customer) {
                frm.__customer_context = {
                        customer: null,
                        price_type: "RETAIL",
                        credit_limit: 0,
                        current_debt: 0,
                };

                update_sale_warnings(frm);
                return frm.__customer_context;
        }

        const customer_name = frm.doc.customer;

        const [{ message }, transactions] = await Promise.all([
                frappe.db.get_value(
                        "Customer",
                        customer_name,
                        ["price_type", "credit_limit"]
                ),
                frappe.db.get_list("Credit Transaction", {
                        filters: {
                                business: frm.doc.business,
                                customer: customer_name,
                                docstatus: 1,
                        },
                        fields: ["direction", "amount"],
                        limit: 1000,
                }),
        ]);

        if (frm.doc.customer !== customer_name) {
                return null;
        }

        const debt = (transactions || []).reduce((total, transaction) => {
                const amount = flt(transaction.amount);

                if (transaction.direction === "DEBIT") {
                        return total + amount;
                }

                if (transaction.direction === "CREDIT") {
                        return total - amount;
                }

                return total;
        }, 0);

        frm.__customer_context = {
                customer: customer_name,
                price_type: message?.price_type || "RETAIL",
                credit_limit: flt(message?.credit_limit),
                current_debt: debt,
        };

        update_sale_warnings(frm);
        return frm.__customer_context;
}

async function get_product_pricing(row) {
        if (!row.product) {
                return null;
        }

        const product = row.product;
        const { message } = await frappe.db.get_value(
                "Product",
                product,
                [
                        "selling_price",
                        "professional_price",
                        "minimum_selling_price",
                ]
        );

        if (!message || row.product !== product) {
                return null;
        }

        row.__selling_price = flt(message.selling_price);
        row.__professional_price = flt(message.professional_price);
        row.__minimum_selling_price = flt(message.minimum_selling_price);

        return message;
}

function selected_base_price(frm, row) {
        const price_type = frm.__customer_context?.price_type || "RETAIL";

        if (
                frm.doc.customer &&
                price_type === "PROFESSIONAL" &&
                flt(row.__professional_price) > 0
        ) {
                return flt(row.__professional_price);
        }

        return flt(row.__selling_price);
}

async function apply_automatic_sale_price(frm, cdt, cdn, force = false) {
        const row = locals[cdt][cdn];

        if (!row?.product) {
                return;
        }

        if (!force && row_has_manual_price(row)) {
                return;
        }

        const product = row.product;
        const customer = frm.doc.customer;
        const request = row.__price_request = (row.__price_request || 0) + 1;

        if (
                frm.doc.customer &&
                (!frm.__customer_context ||
                        frm.__customer_context.customer !== frm.doc.customer)
        ) {
                await load_customer_context(frm);
        }

        const pricing = await get_product_pricing(row);
        if (!pricing || frm.doc.docstatus !== 0 || row.product !== product ||
                frm.doc.customer !== customer || row.__price_request !== request ||
                !(frm.doc.items || []).includes(row)) {
                return;
        }

        const base_price = selected_base_price(frm, row);
        const discount_percent = flt(row.discount_percent);
        const unit_price = base_price * (1 - discount_percent / 100);

        row.__setting_unit_price = true;

        await frappe.model.set_value(
                cdt,
                cdn,
                "base_price",
                Number(base_price.toFixed(3))
        );

        await frappe.model.set_value(
                cdt,
                cdn,
                "unit_price",
                Number(Math.max(unit_price, 0).toFixed(3))
        );

        row.__setting_unit_price = false;

        await recalculate_sale_total(frm);
}

async function apply_discount(frm, cdt, cdn) {
        const row = locals[cdt][cdn];

        let discount = flt(row.discount_percent);

        if (discount < 0) {
                discount = 0;
        }

        if (discount > 100) {
                discount = 100;
        }

        if (discount !== flt(row.discount_percent)) {
                await frappe.model.set_value(
                        cdt,
                        cdn,
                        "discount_percent",
                        discount
                );
        }

        if (row.base_price === null || row.base_price === undefined || row.base_price === "") {
                await apply_automatic_sale_price(frm, cdt, cdn, true);
                return;
        }

        const unit_price = flt(row.base_price) * (1 - discount / 100);

        row.__setting_unit_price = true;

        await frappe.model.set_value(
                cdt,
                cdn,
                "unit_price",
                Number(Math.max(unit_price, 0).toFixed(3))
        );

        row.__setting_unit_price = false;

        await recalculate_sale_total(frm);
}

function update_override_visibility(frm, price_issue, credit_issue) {
        const can_override = user_can_override();

        const show_price =
                can_override &&
                (
                        price_issue ||
                        !!frm.doc.price_override ||
                        !!frm.doc.price_override_reason
                );

        const show_credit =
                can_override &&
                (
                        credit_issue ||
                        !!frm.doc.credit_limit_override ||
                        !!frm.doc.credit_override_reason
                );

        frm.toggle_display(
                "overrides_section",
                show_price || show_credit
        );

        frm.toggle_display("price_override", show_price);
        frm.toggle_display("price_override_reason", show_price);

        frm.toggle_display("credit_limit_override", show_credit);
        frm.toggle_display("credit_override_reason", show_credit);
}

function update_sale_warnings(frm) {
        if (frm.doctype !== "Sale") {
                return;
        }

        const warnings = [];

        const below_minimum_rows = (frm.doc.items || []).filter((row) => {
                const minimum = flt(row.__minimum_selling_price);

                return (
                        minimum > 0 &&
                        flt(row.unit_price) < minimum
                );
        });

        const price_issue = below_minimum_rows.length > 0;

        if (price_issue) {
                const rows = below_minimum_rows
                        .map((row) => `#${row.idx}`)
                        .join(", ");

                warnings.push(
                        `Price below minimum selling price on row(s): ${rows}.`
                );
        }

        let credit_issue = false;

        const context = frm.__customer_context;

        if (
                context &&
                context.customer === frm.doc.customer &&
                (frm.doc.sale_mode || "CREDIT") !== "CASH" &&
                flt(context.credit_limit) > 0
        ) {
                const projected_debt =
                        flt(context.current_debt) +
                        flt(frm.doc.outstanding_amount);

                if (projected_debt > flt(context.credit_limit)) {
                        credit_issue = true;

                        warnings.push(
                                `Credit limit exceeded: current debt ${flt(context.current_debt).toFixed(3)}, ` +
                                `projected debt ${projected_debt.toFixed(3)}, ` +
                                `limit ${flt(context.credit_limit).toFixed(3)}.`
                        );
                }
        }

        update_override_visibility(frm, price_issue, credit_issue);

        // Frappe appends headline messages, so clear the previous
        // CreditFlow warning before rendering the current state.
        frm.set_intro("");

        if (warnings.length) {
                frm.set_intro(warnings.join("<br>"), "orange");
        }
}

function sync_sale_payment_fields(frm) {
        const mode = (frm.doc.sale_mode || "CREDIT").toUpperCase();
        const total = flt(frm.doc.total_amount);
        const amount_paid = flt(frm.doc.amount_paid);
        const show_payment_fields = mode !== "CREDIT";

        frm.toggle_display("amount_paid", show_payment_fields);
        frm.toggle_display(
                "payment_method",
                show_payment_fields &&
                (mode === "CASH" || amount_paid > 0 || !!frm.doc.payment_method)
        );
        frm.toggle_display(
                "payment_reference",
                show_payment_fields &&
                (amount_paid > 0 || !!frm.doc.payment_reference)
        );

        frm.toggle_enable("amount_paid", mode !== "CASH");
        frm.toggle_reqd("customer", mode !== "CASH");
        frm.set_df_property("outstanding_amount", "read_only", 1);

        if (frm.doc.docstatus !== 0) {
                return;
        }

        if (mode === "CASH") {
                set_if_changed(frm, "amount_paid", total.toFixed(3));
                set_if_changed(frm, "outstanding_amount", "0.000");
                set_if_changed(frm, "payment_method", "CASH");
        } else if (mode === "CREDIT") {
                set_if_changed(frm, "amount_paid", "0.000");
                set_if_changed(frm, "outstanding_amount", total.toFixed(3));
                set_if_changed(frm, "payment_method", "");
                set_if_changed(frm, "payment_reference", "");
        } else {
                set_if_changed(
                        frm,
                        "outstanding_amount",
                        Math.max(total - amount_paid, 0).toFixed(3)
                );
        }

        update_sale_warnings(frm);
}

function sale_preview_inputs(frm) {
        return {
                business: frm.doc.business,
                customer: frm.doc.customer || null,
                items: (frm.doc.items || []).map(row => Object.fromEntries(
                        ["product", "quantity", "uom", "base_price", "unit_price", "discount_percent"]
                                .map(field => [field, row[field] ?? null])
                )),
        };
}

async function recalculate_sale_total(frm) {
        if (frm.doc.docstatus !== 0) return true;
        const request = frm.__sale_preview_request = (frm.__sale_preview_request || 0) + 1;
        const inputs = sale_preview_inputs(frm);
        // An unfinished new grid row is validated on save by the server.
        if (!inputs.business || inputs.items.some(row => !row.product || flt(row.quantity) <= 0)) return true;
        const snapshot = JSON.stringify(inputs);
        const { message } = await frappe.call({
                method: "creditflow.creditflow.doctype.sale.sale.preview_totals",
                args: {...inputs, items: JSON.stringify(inputs.items)},
        });
        if (frm.doc.docstatus !== 0) return true;
        if (request !== frm.__sale_preview_request || snapshot !== JSON.stringify(sale_preview_inputs(frm))) return false;
        for (const field of ["total_ht", "total_tva", "total_ttc", "total_amount"]) {
                set_if_changed(frm, field, message[field]);
        }
        (frm.doc.items || []).forEach((row, index) => Object.assign(row, message.items[index]));
        frm.refresh_field("items");
        sync_sale_payment_fields(frm);
        return true;
}

// Save must wait for pricing edits, not just for the last totals request.
function sale_edit(handler) {
        return function(frm, ...args) {
                if (frm.doc.docstatus !== 0) return;
                const pending = frm.__sale_edits ||= new Set();
                const task = handler(frm, ...args);
                pending.add(task);
                return task.finally(() => pending.delete(task));
        };
}

function recalculate_purchase_total(frm) {
        recalculate_table_total(
                frm,
                "items",
                "quantity",
                "unit_cost",
                "total_amount"
        );
}

frappe.ui.form.on(cur_frm.doctype, {
        setup(frm) {
                set_default_business(frm);
        },
});

frappe.ui.form.on("Sale", {
        async refresh(frm) {
                if (frm.doc.docstatus !== 0) {
                        sync_sale_payment_fields(frm);
                        return;
                }
                if (frm.doc.customer) {
                        await load_customer_context(frm);
                } else {
                        frm.__customer_context = {
                                customer: null,
                                price_type: "RETAIL",
                                credit_limit: 0,
                                current_debt: 0,
                        };
                }

                for (const row of frm.doc.items || []) {
                        if (row.product) {
                                await get_product_pricing(row);
                        }
                }

                await recalculate_sale_total(frm);
                update_sale_warnings(frm);
        },

        async validate(frm) {
                do {
                        while (frm.__sale_edits?.size) await Promise.all([...frm.__sale_edits]);
                } while (!(await recalculate_sale_total(frm)) || frm.__sale_edits?.size);
        },

        customer: sale_edit(async function(frm) {
                await load_customer_context(frm);

                for (const row of frm.doc.items || []) {
                        if (
                                row.product &&
                                !row_has_manual_price(row)
                        ) {
                                await apply_automatic_sale_price(
                                        frm,
                                        row.doctype,
                                        row.name,
                                        true
                                );
                        }
                }

                update_sale_warnings(frm);
        }),

        sale_mode(frm) {
                sync_sale_payment_fields(frm);
        },

        amount_paid(frm) {
                sync_sale_payment_fields(frm);
        },

        payment_method(frm) {
                sync_sale_payment_fields(frm);
        },

        items_add(frm) {
                recalculate_sale_total(frm);
        },

        items_remove(frm) {
                recalculate_sale_total(frm);
                update_sale_warnings(frm);
        },
});

frappe.ui.form.on("Sale Item", {
        product: sale_edit(async function(frm, cdt, cdn) {
                const row = locals[cdt][cdn];

                if (!row.product) {
                        return;
                }

                row.__manual_unit_price = false;

                await apply_automatic_sale_price(
                        frm,
                        cdt,
                        cdn,
                        true
                );
        }),

        discount_percent: sale_edit(async function(frm, cdt, cdn) {
                await apply_discount(frm, cdt, cdn);
        }),

        unit_price: sale_edit(async function(frm, cdt, cdn) {
                const row = locals[cdt][cdn];

                if (row.__setting_unit_price) {
                        return;
                }

                row.__manual_unit_price = true;
                row.__price_request = (row.__price_request || 0) + 1;

                /*
                 * Manual lower price behaves as a shortcut for setting
                 * the equivalent discount. This keeps server-side
                 * base_price + discount calculation authoritative.
                 */
                const base_price = flt(row.base_price);
                const desired_price = flt(row.unit_price);

                if (
                        base_price > 0 &&
                        desired_price >= 0 &&
                        desired_price <= base_price
                ) {
                        const discount =
                                ((base_price - desired_price) / base_price) * 100;

                        await frappe.model.set_value(
                                cdt,
                                cdn,
                                "discount_percent",
                                Number(discount.toFixed(3))
                        );
                }

                await recalculate_sale_total(frm);
                update_sale_warnings(frm);
        }),

        quantity(frm) {
                recalculate_sale_total(frm);
        },
});

frappe.ui.form.on("Purchase", {
        refresh(frm) {
                recalculate_purchase_total(frm);
        },

        validate(frm) {
                recalculate_purchase_total(frm);
        },

        items_add(frm) {
                recalculate_purchase_total(frm);
        },

        items_remove(frm) {
                recalculate_purchase_total(frm);
        },
});

frappe.ui.form.on("Purchase Item", {
        quantity(frm) {
                recalculate_purchase_total(frm);
        },

        unit_cost(frm) {
                recalculate_purchase_total(frm);
        },
});
