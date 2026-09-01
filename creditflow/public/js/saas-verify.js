(() => {
  const form = document.querySelector("#cf-verify-form");
  if (!form) return;
  const password = document.querySelector("#cf-password");
  const confirm = document.querySelector("#cf-password-confirm");
  const message = document.querySelector("#cf-form-message");
  const button = form.querySelector("button[type=submit]");
  document.querySelector("#cf-toggle-password").addEventListener("click", (event) => {
    const visible = password.type === "text";
    password.type = visible ? "password" : "text";
    event.currentTarget.textContent = visible ? "Show" : "Hide";
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (password.value !== confirm.value) {
      message.textContent = "Passwords do not match."; message.className = "cf-message cf-error"; return;
    }
    if (!form.reportValidity() || button.disabled) return;
    button.disabled = true; button.textContent = "Creating workspace…";
    try {
      const response = await frappe.call("creditflow.signup.complete_verification", { token: form.dataset.token, password: password.value });
      password.value = ""; confirm.value = "";
      window.location.assign(response.message.login_url);
    } catch (_) {
      password.value = ""; confirm.value = "";
      message.textContent = "This link is invalid, expired, or could not be completed. Request a new verification email.";
      message.className = "cf-message cf-error";
      button.disabled = false; button.textContent = "Create my CreditFlow workspace";
    }
  });
})();
