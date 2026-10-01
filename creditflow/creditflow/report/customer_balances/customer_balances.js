frappe.query_reports["Customer Balances"] = {
	filters: [
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "business",
			label: __("Business"),
			fieldtype: "Link",
			options: "Business",
		},
		{
			fieldname: "show_zero_balances",
			label: __("Show Zero Balances"),
			fieldtype: "Check",
			default: 0,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "credit_status") value = __(value);
		return default_formatter(value, row, column, data);
	}
};
