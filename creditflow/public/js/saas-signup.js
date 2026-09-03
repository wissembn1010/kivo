(() => {
  const form = document.querySelector("#cf-signup-form");
  if (!form) return;
  const message = document.querySelector("#cf-form-message");
  const button = form.querySelector("button[type=submit]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity() || button.disabled) return;
    button.disabled = true; button.textContent = __("Sending…"); message.textContent = "";
    const values = Object.fromEntries(new FormData(form));
    try {
      const response = await frappe.call("creditflow.signup.signup", values);
      sessionStorage.setItem("creditflow_pending_email", values.email);
      window.location.assign("/check-email");
    } catch (_) {
      message.textContent = __("We couldn’t start signup. Check your details and try again.");
      message.className = "cf-message cf-error";
    } finally { button.disabled = false; button.textContent = __("Send verification email"); }
  });
})();
