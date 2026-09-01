const refresh_report = (report) => {
	report.refresh();
};

const use_custom_date_range = (report) => {
	const period_filter = report.get_filter("period");
	if (period_filter.get_value() !== "Custom") {
		period_filter.set_input("Custom");
	}
	report.refresh();
};

frappe.query_reports["Purchases Report"] = {
	filters: [
		{
			fieldname: "period",
			label: __("Period"),
			fieldtype: "Select",
			options: "Today\nThis Week\nThis Month\nCustom",
			default: "This Month",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			on_change: use_custom_date_range,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			on_change: use_custom_date_range,
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
			on_change: refresh_report,
		},
		{
			fieldname: "business",
			label: __("Business"),
			fieldtype: "Link",
			options: "Business",
			on_change: refresh_report,
		},
	],
};
