frappe.ui.form.on("Business", {
  refresh(frm) {
    if (frm.is_new()) {
      frm.set_intro(__("Start with the legal name. Address, contact and fiscal fields can be completed before issuing invoices."), "blue");
      return;
    }
    const missing = ["address", "phone", "tax_identifier"].filter((field) => !frm.doc[field]);
    if (missing.length) {
      frm.set_intro(__("Complete address, phone and matricule fiscal before issuing customer invoices."), "orange");
    } else {
      frm.set_intro(__("Business setup is ready. Continue with Customers, Suppliers, Products and opening-balance imports."), "green");
    }
  },
});
