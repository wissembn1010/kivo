(() => {
  const email = sessionStorage.getItem("creditflow_pending_email") || "";
  const mask = (value) => {
    const [local, domain] = value.split("@");
    if (!domain) return "your email address";
    return `${local.slice(0, 2)}${"•".repeat(Math.max(2, local.length - 2))}@${domain}`;
  };
  document.querySelector("#cf-masked-email").textContent = mask(email);
  const button = document.querySelector("#cf-resend");
  const message = document.querySelector("#cf-form-message");
  button.addEventListener("click", async () => {
    if (!email || button.disabled) return;
    button.disabled = true; button.textContent = "Sending…";
    try {
      await frappe.call("creditflow.signup.resend_verification", { email });
      message.textContent = "If the registration is pending, a fresh link is on its way.";
      message.className = "cf-message cf-success";
    } catch (_) {
      message.textContent = "Please wait a moment and try again.";
      message.className = "cf-message cf-error";
    } finally { setTimeout(() => { button.disabled = false; button.textContent = "Resend verification"; }, 60000); }
  });
})();
