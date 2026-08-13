frappe.ui.form.on(cur_frm.doctype, {
	setup(frm) {
		if (frm.is_new() && !frm.doc.business && frappe.session.user !== "Administrator") {
			frappe.db
				.get_value("User", frappe.session.user, "creditflow_business")
				.then(({ message }) => {
					if (message?.creditflow_business && !frm.doc.business) {
						frm.set_value("business", message.creditflow_business);
					}
				});
		}
	},
});
