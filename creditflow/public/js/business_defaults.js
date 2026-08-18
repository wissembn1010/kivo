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

function sync_sale_payment_fields(frm) {
	const mode = (frm.doc.sale_mode || "CREDIT").toUpperCase();
	const total = flt(frm.doc.total_amount);
	const amount_paid = flt(frm.doc.amount_paid);
	const show_payment_fields = mode !== "CREDIT";

	frm.toggle_display("amount_paid", show_payment_fields);
	frm.toggle_display("payment_method", show_payment_fields && (mode === "CASH" || amount_paid > 0 || !!frm.doc.payment_method));
	frm.toggle_display("payment_reference", show_payment_fields && (amount_paid > 0 || !!frm.doc.payment_reference));
	frm.toggle_enable("amount_paid", mode !== "CASH");
	frm.toggle_reqd("customer", mode !== "CASH");
	frm.set_df_property("outstanding_amount", "read_only", 1);

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
		set_if_changed(frm, "outstanding_amount", Math.max(total - amount_paid, 0).toFixed(3));
	}
}

function recalculate_sale_total(frm) {
	recalculate_table_total(frm, "items", "quantity", "unit_price", "total_amount");
	sync_sale_payment_fields(frm);
}

function recalculate_purchase_total(frm) {
	recalculate_table_total(frm, "items", "quantity", "unit_cost", "total_amount");
}

frappe.ui.form.on(cur_frm.doctype, {
	setup(frm) {
		set_default_business(frm);
	},
});

frappe.ui.form.on("Sale", {
	refresh(frm) {
		recalculate_sale_total(frm);
	},
	validate(frm) {
		recalculate_sale_total(frm);
	},
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
	},
});

frappe.ui.form.on("Sale Item", {
	product(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.product) {
			return;
		}

		frappe.db.get_value("Product", row.product, "selling_price").then(({ message }) => {
			if (!message) {
				return;
			}

			if (
				row.__manual_unit_price ||
				!(row.unit_price === null || row.unit_price === undefined || row.unit_price === "")
			) {
				return;
			}

			row.__setting_unit_price = true;
			frappe.model.set_value(cdt, cdn, "unit_price", message.selling_price || 0).then(() => {
				row.__setting_unit_price = false;
				recalculate_sale_total(frm);
			});
		});
	},
	unit_price(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.__setting_unit_price) {
			row.__manual_unit_price = true;
		}
		recalculate_sale_total(frm);
	},
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
