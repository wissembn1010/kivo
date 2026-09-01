frappe.ui.form.on("Customer", {
    refresh(frm) {
        if (frm.is_new()) {
            return;
        }

        frm.call("get_ledger_balance").then((r) => {
            if (r.message !== undefined) {
                frm.set_intro(
                    __("Outstanding Balance: {0} TND", [r.message])
                );
            }
        });

        frm.add_custom_button(__("Sale"), () => {
            frappe.new_doc("Sale", {
                business: frm.doc.business,
                customer: frm.doc.name,
                business_date: frappe.datetime.get_today()
            });
        }, __("Create"));

        frm.add_custom_button(__("Payment"), () => {
            frappe.new_doc("Payment", {
                business: frm.doc.business,
                customer: frm.doc.name,
                business_date: frappe.datetime.get_today()
            });
        }, __("Create"));

        frm.add_custom_button(__("Payments"), () => {
            frappe.set_route("List", "Payment", {
                business: frm.doc.business,
                customer: frm.doc.name
            });
        }, __("View"));
    }
});
