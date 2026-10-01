frappe.listview_settings["Sale"] = {
    formatters: {
        sale_mode(value) {
            return __(value, null, "Sale Mode");
        },
    },
};
