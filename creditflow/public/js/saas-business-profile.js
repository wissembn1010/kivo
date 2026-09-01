(() => {
  const form = document.querySelector("#cf-profile-form");
  if (!form) return;
  const message = document.querySelector("#cf-form-message");
  const submit = form.querySelector("button[type=submit]");
  const complete = async (args, button) => {
    if (button.disabled) return;
    button.disabled = true;
    try {
      const response = await frappe.call("creditflow.saas_onboarding.complete_business_profile", args);
      window.location.assign(response.message.redirect_to);
    } catch (_) {
      message.textContent = "We couldn't save your profile. Check the details and try again.";
      message.className = "cf-message cf-error"; button.disabled = false;
    }
  };
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (form.reportValidity()) complete(Object.fromEntries(new FormData(form)), submit);
  });
  const skip = document.querySelector("#cf-skip");
  skip.addEventListener("click", () => complete({ skip: 1 }, skip));
})();
