frappe.ready(() => {
  const message = document.querySelector("#cf-form-message");
  const showMessage = (text, error = false) => {
    if (!message) return;
    message.textContent = text;
    message.className = `cf-message ${error ? "cf-error" : ""}`;
  };
  document.querySelectorAll(".cf-checkout").forEach((button) => button.addEventListener("click", async () => {
    if (button.disabled) return;
    button.disabled = true;
    try {
      const r = await frappe.call("creditflow.billing.api.start_checkout", {plan: button.dataset.plan, billing_cycle: button.dataset.cycle});
      const url = new URL(r.message.checkout_url, window.location.origin);
      if (url.origin !== window.location.origin || url.pathname !== "/creditflow-payment-return") throw new Error("Invalid payment URL");
      window.location.assign(url.href);
    } catch (e) { showMessage(__("Unable to create a bank transfer request. Please retry."), true); }
    finally { button.disabled = false; }
  }));
  const page = document.querySelector("#cf-bank-payment");
  if (!page) return;
  const payment = page.dataset.payment;
  const statusLabels = {
    PENDING_PAYMENT: __("Awaiting your bank transfer"), PROOF_SUBMITTED: __("Proof submitted — awaiting verification"),
    UNDER_REVIEW: __("Payment under review"), VERIFIED: __("Payment verified — subscription activated"),
    REJECTED: __("Payment rejected — contact support"), EXPIRED: __("Payment request expired — create a new request"),
    SUCCEEDED: __("Historical payment confirmed"), PENDING: __("Historical payment pending"),
    FAILED: __("Payment failed"), CANCELLED: __("Payment cancelled"), REFUNDED: __("Payment refunded")
  };
  const setText = (id, value) => { document.getElementById(id).textContent = value || ""; };
  const refresh = async () => {
    const button = document.getElementById("cf-refresh-payment");
    button.disabled = true;
    try {
      const r = await frappe.call("creditflow.billing.api.refresh_payment", {payment});
      const p = r.message;
      setText("cf-payment-status", statusLabels[p.status] || __("Contact support for payment status"));
      setText("cf-payment-plan", `${p.plan} · ${p.billing_cycle}`);
      setText("cf-payment-amount", `${Number(p.amount).toFixed(3)} TND`);
      setText("cf-payment-rib", p.bank?.rib);
      setText("cf-payment-reference", p.internal_order_id);
      setText("cf-bank-identity", [p.bank?.bank_name, p.bank?.beneficiary_name].filter(Boolean).join(" · "));
      setText("cf-payment-expiry", p.expires_at ? `${__("Request expires:")} ${p.expires_at}` : "");
      document.getElementById("cf-transfer-details").hidden = !p.bank?.rib;
      document.getElementById("cf-proof-form").hidden = !["PENDING_PAYMENT", "PROOF_SUBMITTED"].includes(p.status);
      const receipt = document.getElementById("cf-receipt-link");
      receipt.hidden = true;
      if (typeof p.receipt === "string" && p.receipt.startsWith("/private/files/")) {
        receipt.href = p.receipt; receipt.hidden = false;
      }
    } catch (e) { showMessage(__("Unable to load payment details. Please retry."), true); }
    finally { button.disabled = false; }
  };
  document.getElementById("cf-refresh-payment").addEventListener("click", refresh);
  [["cf-copy-rib", "cf-payment-rib"], ["cf-copy-reference", "cf-payment-reference"]].forEach(([button, source]) => {
    document.getElementById(button).addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(document.getElementById(source).textContent); showMessage(__("Copied")); }
      catch (e) { showMessage(__("Copy failed. Select and copy the displayed text."), true); }
    });
  });
  document.getElementById("cf-proof-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = document.getElementById("cf-proof").files[0];
    if (!file || !["image/jpeg", "image/png", "application/pdf"].includes(file.type) || file.size > 5 * 1024 * 1024 || !file.size) {
      showMessage(__("Choose a JPEG, PNG or PDF file up to 5 MiB."), true); return;
    }
    const button = form.querySelector("button"); button.disabled = true;
    try {
      const content = await new Promise((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file);
      });
      await frappe.call("creditflow.billing.api.submit_proof", {payment, filename: file.name, content});
      form.reset(); showMessage(__("Proof submitted. Received funds must be verified before paid access starts.")); await refresh();
    } catch (e) { showMessage(__("Proof could not be submitted. Please retry."), true); }
    finally { button.disabled = false; }
  });
  refresh();
});
