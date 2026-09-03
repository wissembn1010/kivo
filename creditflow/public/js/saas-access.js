if (window.frappe?.boot?.lang === "ar-TN") {
  const originalIsRtl = frappe.utils.is_rtl.bind(frappe.utils);
  frappe.utils.is_rtl = (lang = null) => (lang || frappe.boot.lang) === "ar-TN" || originalIsRtl(lang);
  document.documentElement.lang = "ar-TN";
  document.documentElement.dir = "rtl";
  const rtlDesk = frappe.assets?.bundled_asset?.("desk.bundle.css", true);
  if (rtlDesk && !document.querySelector("link[data-kivo-rtl]")) {
    const link = document.createElement("link");
    link.rel = "stylesheet"; link.href = rtlDesk; link.dataset.kivoRtl = "1";
    document.head.appendChild(link);
  }
}

frappe.ready(() => {
  const policy = frappe.boot && frappe.boot.creditflow_saas;
  if (!policy) return;
  if (policy.banner) {
    const banner = document.createElement("div");
    banner.className = `creditflow-saas-banner ${policy.can_write ? "is-trial" : "is-readonly"}`;
    banner.textContent = policy.banner;
    if (policy.subscription_route) {
      const link = document.createElement("a"); link.href = policy.subscription_route; link.textContent = __("View subscription"); banner.appendChild(link);
    }
    document.body.prepend(banner);
  }
  if (!policy.can_write) {
    frappe.router.on("change", () => setTimeout(() => {
      if (window.cur_frm && policy.protected_doctypes.includes(cur_frm.doctype)) {
        cur_frm.disable_save();
        cur_frm.set_intro(__("Your Kivo subscription is read-only. Your existing data is safe."), "orange");
      }
    }, 100));
  }
});
