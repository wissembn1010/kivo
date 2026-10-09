frappe.ui.form.on("CreditFlow Billing Payment", {
  refresh(frm) {
    if (frm.is_new() || !frappe.user.has_role("System Manager")) return;
    frm.disable_save();
    frm.add_custom_button(__("Review details and audit"), async () => {
      const response = await frappe.call("creditflow.billing.api.admin_payment_details", {payment: frm.doc.name});
      const details = response.message;
      const dialog = new frappe.ui.Dialog({title: __("Payment review"), size: "large", fields: [{fieldname: "details", fieldtype: "HTML"}]});
      const wrapper = dialog.fields_dict.details.$wrapper;
      wrapper.empty();
      const identity = details.customer || details.payment?.owner || details.payment?.requested_by || details.owner || details.business || frm.doc.business;
      $("<p>").text(`${__("Customer")}: ${typeof identity === "object" ? JSON.stringify(identity) : identity}`).appendTo(wrapper);
      $("<p>").text(`${__("Plan")}: ${frm.doc.plan} · ${Number(frm.doc.amount).toFixed(3)} ${frm.doc.currency}`).appendTo(wrapper);
      $("<p>").text(`${__("Reference")}: ${frm.doc.internal_order_id}`).appendTo(wrapper);
      const receipt = details.receipt || details.payment?.receipt || frm.doc.receipt;
      if (typeof receipt === "string" && receipt.startsWith("/private/files/")) {
        $("<a>", {href: receipt, target: "_blank", rel: "noopener noreferrer"}).text(__("Open private receipt")).appendTo(wrapper);
      }
      $("<h5>").text(__("Audit and payment history")).appendTo(wrapper);
      $("<pre>").css({"white-space": "pre-wrap"}).text(JSON.stringify(details, null, 2)).appendTo(wrapper);
      dialog.show();
    });
    if (frm.doc.provider !== "BANK_TRANSFER" || !["PENDING_PAYMENT", "PROOF_SUBMITTED", "UNDER_REVIEW"].includes(frm.doc.status)) return;
    const review = (action) => {
      const verify = action === "VERIFIED";
      const fields = verify ? [
        {fieldtype: "HTML", options: `<p>${__("Verify actual received funds in your authorized bank records before approving. A customer receipt is insufficient.")}</p>`},
        {fieldname: "transaction_id", label: __("Received bank transaction ID"), fieldtype: "Data", reqd: 1},
        {fieldname: "amount", label: __("Actual received amount (TND)"), fieldtype: "Currency", precision: 3, reqd: 1},
        {fieldname: "transaction_date", label: __("Bank transaction date"), fieldtype: "Date", reqd: 1},
        {fieldname: "reference", label: __("Reference in bank record"), fieldtype: "Data", reqd: 1},
        {fieldname: "destination_account", label: __("Destination account in bank record (when available)"), fieldtype: "Data"},
        {fieldname: "confirmed_funds", label: __("I checked actual received funds independently"), fieldtype: "Check", reqd: 1}
      ] : [];
      fields.push({fieldname: "notes", label: verify ? __("How actual received funds were independently checked") : __("Admin notes"), fieldtype: "Small Text", reqd: verify || action === "REJECTED"});
      const dialog = new frappe.ui.Dialog({
        title: verify ? __("Verify received payment") : action === "REJECTED" ? __("Reject payment") : __("Mark under review"),
        fields,
        primary_action_label: verify ? __("Verify and activate subscription") : __("Save review"),
        async primary_action(values) {
          if (verify && !values.confirmed_funds) return;
          delete values.confirmed_funds;
          dialog.get_primary_btn().prop("disabled", true);
          try {
            await frappe.call("creditflow.billing.api.admin_review", {payment: frm.doc.name, action, ...values});
            dialog.hide(); await frm.reload_doc();
          } finally { dialog.get_primary_btn().prop("disabled", false); }
        }
      });
      dialog.show();
    };
    frm.add_custom_button(__("Mark under review"), () => review("UNDER_REVIEW"), __("Payment review"));
    frm.add_custom_button(__("Verify received funds"), () => review("VERIFIED"), __("Payment review"));
    frm.add_custom_button(__("Reject"), () => review("REJECTED"), __("Payment review"));
  }
});
