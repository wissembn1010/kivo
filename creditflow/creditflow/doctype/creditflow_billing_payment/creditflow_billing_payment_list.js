frappe.listview_settings["CreditFlow Billing Payment"] = {
  add_fields: ["business", "plan", "amount", "currency", "internal_order_id", "status", "provider"],
  get_indicator(doc) {
    const statuses = {
      PENDING_PAYMENT: [__("Awaiting transfer"), "orange"], PROOF_SUBMITTED: [__("Proof submitted"), "blue"],
      UNDER_REVIEW: [__("Under review"), "blue"], VERIFIED: [__("Verified"), "green"],
      REJECTED: [__("Rejected"), "red"], EXPIRED: [__("Expired"), "gray"],
      SUCCEEDED: [__("Historical success"), "green"], PENDING: [__("Historical pending"), "orange"],
      FAILED: [__("Failed"), "red"], CANCELLED: [__("Cancelled"), "gray"], REFUNDED: [__("Refunded"), "gray"]
    };
    const indicator = statuses[doc.status] || [__("Unknown status"), "gray"];
    return [...indicator, `status,=,${doc.status}`];
  },
  onload(listview) {
    if (!frappe.user.has_role("System Manager")) return;
    listview.page.add_inner_button(__("Awaiting review"), () => {
      listview.filter_area.clear();
      listview.filter_area.add([["CreditFlow Billing Payment", "provider", "=", "BANK_TRANSFER"], ["CreditFlow Billing Payment", "status", "in", ["PENDING_PAYMENT", "PROOF_SUBMITTED", "UNDER_REVIEW"]]]);
    });
  }
};
