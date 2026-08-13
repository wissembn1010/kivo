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
};
