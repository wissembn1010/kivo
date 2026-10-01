frappe.pages["creditflow-import"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Kivo CSV Import"), single_column: true });
  const type = page.add_field({ label: __("Import Type"), fieldtype: "Select", fieldname: "import_type", reqd: 1, options: ["CUSTOMERS", "SUPPLIERS", "PRODUCTS", "OPENING_STOCK", "OPENING_CUSTOMER_DEBT", "OPENING_SUPPLIER_DEBT"].join("\n") });
  const body = $("<div class='frappe-card p-4'><p class='text-muted'></p><input type='file' accept='.csv,text/csv' class='form-control mb-3'><pre class='mt-3'></pre></div>").appendTo(page.body);
  body.find("p").text(__("Download a template, choose a CSV, preview every row, then commit only after validation succeeds."));
  const render_result = (result) => {
    const lines = [__("Import Type") + ": " + __(result.import_type)];
    if (result.created_count !== undefined) {
      lines.push(__("Created records: {0}", [result.created_count]));
    } else {
      lines.push(__("Valid rows: {0}", [result.valid_count]), __("Invalid rows: {0}", [result.invalid_count]));
    }
    if (result.duplicate_import) lines.push(__("This file has already been imported."));
    for (const error of result.invalid_rows || result.errors || []) lines.push(__("Row {0}: {1}", [error.row, error.error]));
    output.text(lines.join("\n"));
  };
  const input = body.find("input")[0]; const output = body.find("pre");
  const read = () => new Promise((resolve, reject) => { if (!input.files.length) return reject(new Error(__("Choose a CSV file first."))); const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsText(input.files[0]); });
  page.add_inner_button(__("Download Template"), async () => { const r = await frappe.call("creditflow.onboarding.csv_template", { import_type: type.get_value() }); const blob = new Blob([r.message], {type:"text/csv"}); const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=`${type.get_value().toLowerCase()}.csv`;a.click();URL.revokeObjectURL(a.href); });
  page.set_primary_action(__("Preview"), async () => { try { const csv_content=await read(); const r=await frappe.call("creditflow.onboarding.preview_csv",{import_type:type.get_value(),csv_content}); render_result(r.message); } catch(e) { frappe.msgprint(e.message); } });
  page.add_inner_button(__("Commit Valid Import"), async () => { try { const csv_content=await read(); const r=await frappe.call("creditflow.onboarding.commit_csv",{import_type:type.get_value(),csv_content}); render_result(r.message); frappe.show_alert({message:__("Import completed"),indicator:"green"}); } catch(e) { frappe.msgprint(e.message); } });
};
