frappe.query_reports["Sales Report"]={filters:[{fieldname:"period",label:__("Period"),fieldtype:"Select",options:"Today\nThis Week\nThis Month\nCustom",default:"This Month"},{fieldname:"from_date",label:__("From Date"),fieldtype:"Date",default:frappe.datetime.month_start()},{fieldname:"to_date",label:__("To Date"),fieldtype:"Date",default:frappe.datetime.month_end()},{fieldname:"customer",label:__("Customer"),fieldtype:"Link",options:"Customer"},{fieldname:"business",label:__("Business"),fieldtype:"Link",options:"Business"}],
	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "sale_mode") value = __(value, null, "Sale Mode");
		if (column.fieldname === "customer_name" && !data?.customer && value === "Walk-in Customer") value = __(value);
		return default_formatter(value, row, column, data);
	}
};
