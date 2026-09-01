function update_payment_fields(frm) {
	const status = frm.doc.payment_status || "CREDIT";
	const has_immediate_payment = status === "PAID" || status === "PARTIAL";

	frm.toggle_display("payment_method", has_immediate_payment);
	frm.toggle_display("payment_reference", has_immediate_payment);
	frm.toggle_display("paid_amount", status === "PARTIAL");
	frm.toggle_reqd("payment_method", has_immediate_payment);
	frm.set_df_property("paid_amount", "read_only", status !== "PARTIAL");

	if (frm.doc.docstatus !== 0) {
		return;
	}

	if (status === "CREDIT") {
		frm.set_value("paid_amount", 0);
		frm.set_value("payment_method", "");
		frm.set_value("payment_reference", "");
	} else if (status === "PAID") {
		frm.set_value("paid_amount", frm.doc.total_amount || 0);
	}
}

frappe.ui.form.on("Purchase", {
	refresh: update_payment_fields,
	payment_status: update_payment_fields,
	total_amount(frm) {
		if (frm.doc.payment_status === "PAID") {
			frm.set_value("paid_amount", frm.doc.total_amount || 0);
		}
	},
});
