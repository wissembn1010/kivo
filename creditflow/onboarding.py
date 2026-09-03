import csv
import hashlib
import io
import json
import unicodedata
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import cint, getdate, now, today

from creditflow.permissions import get_user_business, has_cross_business_access

IMPORTS = {
    "CUSTOMERS": ("customer_name", "phone", "secondary_phone", "address", "credit_limit", "price_type", "notes"),
    "SUPPLIERS": ("supplier_name", "phone", "address", "notes"),
    "PRODUCTS": ("product_name", "reference", "selling_price", "professional_price", "minimum_selling_price", "tva_rate", "base_uom", "minimum_stock", "active"),
    "OPENING_STOCK": ("product", "quantity", "date", "note", "reference"),
    "OPENING_CUSTOMER_DEBT": ("customer", "amount", "date", "note", "reference"),
    "OPENING_SUPPLIER_DEBT": ("supplier", "amount", "date", "note", "reference"),
}
FINANCIAL = {"OPENING_STOCK", "OPENING_CUSTOMER_DEBT", "OPENING_SUPPLIER_DEBT"}


def _context(import_type, csv_content, requested_business=None):
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication is required."), frappe.AuthenticationError)
    roles = set(frappe.get_roles())
    if "OWNER" not in roles and not has_cross_business_access():
        frappe.throw(_("Only an OWNER can run onboarding imports."), frappe.PermissionError)
    import_type = (import_type or "").strip().upper()
    if import_type not in IMPORTS:
        frappe.throw(_("Unsupported import type."))
    if has_cross_business_access():
        business = requested_business
        if not business or not frappe.db.exists("Business", business):
            frappe.throw(_("A valid Business is required for an administrative import."))
    else:
        business = get_user_business()
        if requested_business and requested_business != business:
            frappe.throw(_("CSV imports can only target your assigned Business."), frappe.PermissionError)
        if not business:
            frappe.throw(_("Your user is not assigned to a Kivo Business."), frappe.PermissionError)
    content = csv_content.decode("utf-8-sig") if isinstance(csv_content, bytes) else str(csv_content or "")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return import_type, business, content, digest, f"{business}:{import_type}:{digest}"


def _decimal(value, label, positive=False, non_negative=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label} must be a valid number")
    if not number.is_finite() or (positive and number <= 0) or (non_negative and number < 0):
        comparator = "greater than 0" if positive else "greater than or equal to 0"
        raise ValueError(f"{label} must be {comparator}")
    return number


def _boolean(value):
    text = str(value or "1").strip().lower()
    if text not in {"1", "0", "true", "false", "yes", "no"}:
        raise ValueError("active must be 1/0, true/false, or yes/no")
    return 1 if text in {"1", "true", "yes"} else 0


def _normalized_key(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _duplicate_identity(import_type, data):
    if import_type == "CUSTOMERS": return "customer", _normalized_key(data["customer_name"])
    if import_type == "SUPPLIERS": return "supplier", _normalized_key(data["supplier_name"])
    if import_type == "PRODUCTS": return "product reference", _normalized_key(data["reference"])
    reference = _normalized_key(data.get("reference"))
    if reference: return "legacy reference", reference
    target = data.get("product") or data.get("customer") or data.get("supplier")
    amount = data.get("quantity") if import_type == "OPENING_STOCK" else data.get("amount")
    fingerprint = "|".join((str(target), format(amount.normalize(), "f"), str(data.get("date")), _normalized_key(data.get("note"))))
    return "opening event", fingerprint


def _source_identifier(import_type, data):
    if import_type == "CUSTOMERS": return data.get("customer_name")
    if import_type == "SUPPLIERS": return data.get("supplier_name")
    if import_type == "PRODUCTS": return data.get("reference")
    return data.get("reference") or data.get("product") or data.get("customer") or data.get("supplier")


def _resolve(doctype, key, business, label_field):
    if not key:
        raise ValueError(f"{doctype} is required")
    name = frappe.db.get_value(doctype, {"name": key, "business": business}, "name")
    if not name:
        matches = frappe.get_all(doctype, filters={label_field: key, "business": business}, pluck="name", limit=2)
        if len(matches) > 1:
            raise ValueError(f"{doctype} '{key}' is ambiguous; use its document ID")
        name = matches[0] if matches else None
    if not name:
        raise ValueError(f"{doctype} '{key}' was not found in this Business")
    return name


def _validate_row(import_type, row, business):
    if import_type == "CUSTOMERS":
        if not row.get("customer_name"): raise ValueError("customer_name is required")
        price = (row.get("price_type") or "RETAIL").upper()
        if price not in {"RETAIL", "PROFESSIONAL"}: raise ValueError("price_type must be RETAIL or PROFESSIONAL")
        credit = _decimal(row.get("credit_limit") or 0, "credit_limit", non_negative=True)
        if frappe.db.exists("Customer", {"business":business,"customer_name":row["customer_name"]}): raise ValueError("duplicate customer_name in this Business")
        return {"customer_name":row["customer_name"],"phone":row.get("phone"),"secondary_phone":row.get("secondary_phone"),"address":row.get("address"),"credit_limit":credit,"price_type":price,"notes":row.get("notes")}
    if import_type == "SUPPLIERS":
        if not row.get("supplier_name"): raise ValueError("supplier_name is required")
        if frappe.db.exists("Supplier", {"business":business,"supplier_name":row["supplier_name"]}): raise ValueError("duplicate supplier_name in this Business")
        return {"supplier_name":row["supplier_name"],"phone":row.get("phone"),"address":row.get("address"),"notes":row.get("notes")}
    if import_type == "PRODUCTS":
        if not row.get("product_name") or not row.get("reference"): raise ValueError("product_name and reference are required")
        uom=(row.get("base_uom") or "UNIT").upper()
        if not frappe.db.get_value("CreditFlow UOM",uom,"active"): raise ValueError(f"base_uom '{uom}' does not exist or is inactive")
        if frappe.db.exists("Product", {"reference":row["reference"]}): raise ValueError("duplicate product reference/SKU")
        data={"product_name":row["product_name"],"reference":row["reference"],"primary_unit":uom,"active":_boolean(row.get("active"))}
        for field in ("selling_price","professional_price","minimum_selling_price","tva_rate","minimum_stock"):
            data[field]=_decimal(row.get(field) or 0,field,non_negative=True)
        if data["tva_rate"] > 100: raise ValueError("tva_rate must be between 0 and 100")
        return data
    if import_type == "OPENING_STOCK":
        return {"product":_resolve("Product",row.get("product"),business,"reference"),"quantity":_decimal(row.get("quantity"),"quantity",positive=True),"date":getdate(row.get("date") or today()),"note":row.get("note"),"reference":row.get("reference")}
    if import_type == "OPENING_CUSTOMER_DEBT":
        return {"customer":_resolve("Customer",row.get("customer"),business,"customer_name"),"amount":_decimal(row.get("amount"),"amount",positive=True),"date":getdate(row.get("date") or today()),"note":row.get("note"),"reference":row.get("reference")}
    return {"supplier":_resolve("Supplier",row.get("supplier"),business,"supplier_name"),"amount":_decimal(row.get("amount"),"amount",positive=True),"date":getdate(row.get("date") or today()),"note":row.get("note"),"reference":row.get("reference")}


def _preview(import_type, csv_content, business=None, filename=None):
    import_type,business,content,digest,key=_context(import_type,csv_content,business)
    reader=csv.DictReader(io.StringIO(content))
    if not reader.fieldnames: frappe.throw(_("CSV header row is required."))
    headers=[(h or "").strip() for h in reader.fieldnames]
    if len(headers) != len(set(headers)): frappe.throw(_("CSV column names must be unique."))
    unknown=[h for h in headers if h not in IMPORTS[import_type]]
    if unknown: frappe.throw(_("Unsupported CSV column(s): {0}").format(", ".join(unknown)))
    valid=[];errors=[];total=Decimal("0");input_count=0;seen={}
    for number,raw in enumerate(reader,start=2):
        input_count += 1
        if None in raw:
            errors.append({"row":number,"error":f"Row {number} contains more values than declared columns."})
            continue
        row={str(k).strip():str(v or "").strip() for k,v in raw.items()}
        if not any(row.values()): input_count -= 1;continue
        try:
            data=_validate_row(import_type,row,business);duplicate_type,duplicate_key=_duplicate_identity(import_type,data)
            if (duplicate_type,duplicate_key) in seen: raise ValueError(f"duplicate {duplicate_type} '{duplicate_key}' first appeared on row {seen[(duplicate_type,duplicate_key)]}")
            seen[(duplicate_type,duplicate_key)]=number
            valid.append({"row":number,"data":data,"source_identifier":_source_identifier(import_type,data)})
            if import_type=="OPENING_STOCK": total+=data["quantity"]
            elif import_type in FINANCIAL: total+=data["amount"]
        except Exception as exc: errors.append({"row":number,"error":str(exc)})
    existing=frappe.db.get_value("Onboarding Import Batch",{"import_key":key},["name","status"],as_dict=True)
    duplicate=bool(existing and existing.status != "FAILED")
    return {"import_type":import_type,"business":business,"filename":filename or getattr(frappe.local,"form_dict",{}).get("filename") or "Uploaded CSV","file_hash":digest,"import_key":key,"valid_rows":valid,"invalid_rows":errors,"input_row_count":input_count,"valid_count":len(valid),"invalid_count":len(errors),"total":total,"duplicate_import":duplicate,"existing_batch":existing.name if existing else None}


@frappe.whitelist()
def preview_csv(import_type, csv_content, business=None, filename=None):
    """Validate all rows without creating master or economic records."""
    return _preview(import_type,csv_content,business,filename)


def _created_doctype(import_type):
    return {"CUSTOMERS":"Customer","SUPPLIERS":"Supplier","PRODUCTS":"Product","OPENING_STOCK":"Stock Movement","OPENING_CUSTOMER_DEBT":"Credit Transaction","OPENING_SUPPLIER_DEBT":"Supplier Transaction"}[import_type]


def _audit_rows(preview, status, created=None):
    created=created or {};rows=[]
    for item in preview["valid_rows"]:
        result=created.get(item["row"])
        rows.append({"source_row":item["row"],"import_entity":preview["import_type"],"source_identifier":item.get("source_identifier"),"result_status":"SUCCESS" if status=="SUCCESS" else "ROLLED BACK","created_doctype":result[0] if result and status=="SUCCESS" else None,"created_document":result[1] if result and status=="SUCCESS" else None,"message":None if status=="SUCCESS" else "Atomic import rolled back; no document was committed."})
    for error in preview["invalid_rows"]: rows.append({"source_row":error["row"],"import_entity":preview["import_type"],"result_status":"FAILED","message":error["error"]})
    return rows


def _save_batch(preview, status, created=None, failure_message=None):
    existing=preview.get("existing_batch");batch=frappe.get_doc("Onboarding Import Batch",existing) if existing else frappe.new_doc("Onboarding Import Batch")
    batch.update({"business":preview["business"],"import_type":preview["import_type"],"filename":preview["filename"],"file_hash":preview["file_hash"],"import_key":preview["import_key"],"status":status,"input_row_count":preview["input_row_count"],"row_count":preview["valid_count"],"success_count":preview["valid_count"] if status=="SUCCESS" else 0,"failure_count":preview["invalid_count"] if preview["invalid_count"] else (preview["input_row_count"] if status=="FAILED" else 0),"total_amount":preview["total"],"errors":json.dumps(preview["invalid_rows"] or ([{"error":failure_message}] if failure_message else []),ensure_ascii=False),"imported_by":frappe.session.user,"imported_on":now()})
    batch.set("created_records",_audit_rows(preview,status,created))
    if existing: batch.save(ignore_permissions=True)
    else: batch.insert(ignore_permissions=True)
    return batch


@frappe.whitelist()
def commit_csv(import_type, csv_content, business=None, filename=None):
    from creditflow.subscription import require_feature, require_write_access

    context_type, authorized_business, context_content, context_digest, context_key = _context(import_type, csv_content, business)
    require_write_access(authorized_business)
    require_feature(authorized_business, "migration")
    preview=_preview(import_type,csv_content,business,filename)
    if preview["duplicate_import"]: frappe.throw(_("This exact CSV was already imported for this Business and import type."))
    if preview["invalid_rows"]:
        _save_batch(preview,"FAILED")
        frappe.throw(_("Import has invalid rows: {0}").format("; ".join(f"row {r['row']}: {r['error']}" for r in preview["invalid_rows"])))
    if not preview["valid_rows"]: frappe.throw(_("Import contains no data rows."))
    savepoint="creditflow_onboarding_import";frappe.db.savepoint(savepoint);created=[];audit_created={}
    try:
        for item in preview["valid_rows"]:
            name=_commit_row(preview["import_type"],preview["business"],item["row"],item["data"],preview.get("existing_batch") or preview["import_key"])
            created.append(name);audit_created[item["row"]]=(_created_doctype(preview["import_type"]),name)
        batch=_save_batch(preview,"SUCCESS",audit_created)
    except Exception as exc:
        frappe.db.rollback(save_point=savepoint);_save_batch(preview,"FAILED",failure_message=str(exc));raise
    return {"batch":batch.name,"import_type":preview["import_type"],"created_count":len(created),"created":created,"total":preview["total"],"errors":[]}


def _commit_row(import_type,business,row_number,data,batch):
    marker=f"Import {batch} row {row_number}"
    if import_type in {"CUSTOMERS","SUPPLIERS","PRODUCTS"}:
        doctype={"CUSTOMERS":"Customer","SUPPLIERS":"Supplier","PRODUCTS":"Product"}[import_type]
        doc=frappe.get_doc({"doctype":doctype,"business":business,**data}).insert();return doc.name
    if import_type=="OPENING_STOCK":
        doc=frappe.get_doc({"doctype":"Stock Movement","business":business,"product":data["product"],"direction":"IN","quantity":data["quantity"],"movement_reason":"OPENING_STOCK","business_date":data["date"],"legacy_reference":data["reference"] or marker,"notes":data["note"] or marker}).insert();doc.submit();return doc.name
    if import_type=="OPENING_CUSTOMER_DEBT":
        doc=frappe.get_doc({"doctype":"Credit Transaction","business":business,"customer":data["customer"],"transaction_type":"OPENING_BALANCE","direction":"DEBIT","amount":data["amount"],"transaction_date":data["date"],"legacy_reference":data["reference"] or marker,"notes":data["note"] or marker}).insert();doc.submit();return doc.name
    doc=frappe.get_doc({"doctype":"Supplier Transaction","business":business,"supplier":data["supplier"],"transaction_type":"OPENING_BALANCE","direction":"DEBIT","amount":data["amount"],"transaction_date":data["date"],"notes":f"{marker}: {data['reference'] or ''} {data['note'] or ''}".strip()}).insert();doc.submit();return doc.name


@frappe.whitelist()
def csv_template(import_type):
    import_type=(import_type or "").strip().upper()
    if import_type not in IMPORTS: frappe.throw(_("Unsupported import type."))
    output=io.StringIO();writer=csv.writer(output);writer.writerow(IMPORTS[import_type]);return output.getvalue()
