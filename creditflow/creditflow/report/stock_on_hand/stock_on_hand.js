frappe.query_reports["Stock On Hand"] = {
	filters: [
		{
			fieldname: "product",
			label: __("Product"),
			fieldtype: "Link",
			options: "Product",
		},
		{
			fieldname: "business",
			label: __("Business"),
			fieldtype: "Link",
			options: "Business",
		},
		{
			fieldname: "show_zero_stock",
			label: __("Show Zero Stock"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "show_negative_stock_only",
			label: __("Show Negative Stock Only"),
			fieldtype: "Check",
			default: 0,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "stock_status") value = __(value);
		if (column.fieldname === "unit") value = __(value);
		return default_formatter(value, row, column, data);
	}
};
