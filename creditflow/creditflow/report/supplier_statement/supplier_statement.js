frappe.query_reports["Supplier Statement"]={filters:[{fieldname:"supplier",label:__("Supplier"),fieldtype:"Link",options:"Supplier",reqd:1},{fieldname:"from_date",label:__("From Date"),fieldtype:"Date"},{fieldname:"to_date",label:__("To Date"),fieldtype:"Date"}],
	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "transaction_type") value = __(value);
		if (column.fieldname === "reference" && data?.transaction_type === "OPENING" && value === "Opening balance") value = __(value);
		return default_formatter(value, row, column, data);
	}
};
