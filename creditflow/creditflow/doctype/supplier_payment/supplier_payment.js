frappe.ui.form.on("Supplier Payment", {
	refresh(frm) { frm.trigger("calculate_withholding"); },
	amount(frm) { frm.trigger("calculate_withholding"); },
	withholding_enabled(frm) { frm.trigger("calculate_withholding"); },
	withholding_base(frm) { frm.trigger("calculate_withholding"); },
	withholding_rate(frm) { frm.trigger("calculate_withholding"); },
	calculate_withholding(frm) {
		const settlement = flt(frm.doc.amount, 3);
		const withheld = frm.doc.withholding_enabled ? flt(flt(frm.doc.withholding_base, 3) * flt(frm.doc.withholding_rate, 2) / 100, 3) : 0;
		frm.set_value("withholding_amount", withheld);
		frm.set_value("net_paid", flt(settlement - withheld, 3));
	}
});
