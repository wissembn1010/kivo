import hashlib
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime
from lxml import etree

XSD_DIR = Path(__file__).with_name("tej_xsd")
DECLARATION_XSD = XSD_DIR / "TEJDeclarationRS_v1.0.xsd"
OPERATIONS_XSD = XSD_DIR / "TEJRSCodesOperations_v1.0.xsd"
COUNTRIES_XSD = XSD_DIR / "TEJISOPaysDevises.xsd"
NS = {"xs": "http://www.w3.org/2001/XMLSchema"}
MILLIEME = Decimal("0.001")


def to_millimes(value):
	try:
		amount = Decimal(str(value)).quantize(MILLIEME, rounding=ROUND_HALF_UP)
	except (InvalidOperation, TypeError, ValueError):
		raise frappe.ValidationError(_("Amount must be a valid TND value."))
	return int(amount * 1000)


def _schema_values(path, type_name):
	root = etree.parse(str(path))
	type_node = root.xpath(f"//xs:simpleType[@name='{type_name}']", namespaces=NS)
	if not type_node:
		return {}
	result = {}
	for node in type_node[0].xpath(".//xs:enumeration", namespaces=NS):
		description = " ".join(node.xpath("string(.//xs:documentation)", namespaces=NS).split())
		result[node.get("value")] = description
	return result


def official_operation_codes():
	return _schema_values(OPERATIONS_XSD, "TypeCodesOperations")


def official_country_codes():
	return _schema_values(COUNTRIES_XSD, "TypeCodesPays")


def official_currency_codes():
	return _schema_values(COUNTRIES_XSD, "TypeCodeDevise")


def validate_required_identity(tax):
	errors = []
	if not re.fullmatch(r"\d{7}[A-Z]", (tax.declarant_tax_identifier or "").strip().upper()):
		errors.append("Business Matricule Fiscal must be 7 digits followed by one uppercase letter")
	if tax.declarant_category not in {"PP", "PM"}:
		errors.append("Business Taxpayer Category must be PP or PM")
	for field, label in (("beneficiary_name", "Supplier Name"), ("beneficiary_identifier_type", "TEJ Identifier Type"),
		("beneficiary_identifier", "Identification Number"), ("beneficiary_category", "Taxpayer Category"),
		("beneficiary_address", "Address"), ("beneficiary_email", "Email"), ("beneficiary_phone", "Phone")):
		if not tax.get(field):
			errors.append(f"Supplier {label} is required")
	id_type = str(tax.beneficiary_identifier_type or "")
	identifier = str(tax.beneficiary_identifier or "").strip().upper()
	if id_type not in {"1", "2", "3", "4", "5"}:
		errors.append("Supplier TEJ Identifier Type must be 1, 2, 3, 4 or 5")
	elif id_type == "1" and not re.fullmatch(r"\d{7}[A-Z]", identifier):
		errors.append("Supplier fiscal identifier must be 7 digits followed by one uppercase letter")
	elif id_type == "2" and not re.fullmatch(r"\d{8}", identifier):
		errors.append("Supplier CIN must contain exactly 8 digits")
	if id_type in {"2", "3", "4"} and not tax.beneficiary_birth_date:
		errors.append("Supplier Date of Birth is required for CIN, Passport or Residence Card")
	if id_type in {"3", "4", "5"} and tax.beneficiary_country_code not in official_country_codes():
		errors.append("Supplier TEJ Country Code must be an official non-Tunisian code")
	if tax.tej_operation_code not in official_operation_codes():
		errors.append("Withholding TEJ Operation Code is not present in the official schema")
	if errors:
		raise frappe.ValidationError("; ".join(errors))


def _text(parent, name, value):
	node = etree.SubElement(parent, name)
	node.text = str(value)
	return node


def _identity(parent, tax):
	beneficiary = etree.SubElement(parent, "Beneficiaire")
	id_taxpayer = etree.SubElement(beneficiary, "IdTaxpayer")
	kinds = {"1": "MatriculeFiscal", "2": "CIN", "3": "Passeport", "4": "CarteSejour", "5": "AutreIdentifiantFiscal"}
	kind = str(tax.beneficiary_identifier_type)
	id_node = etree.SubElement(id_taxpayer, kinds[kind])
	_text(id_node, "TypeIdentifiant", kind)
	_text(id_node, "Identifiant", str(tax.beneficiary_identifier).strip().upper())
	if kind in {"2", "3", "4"}:
		_text(id_node, "DateNaissance", getdate(tax.beneficiary_birth_date).strftime("%d/%m/%Y"))
	if kind in {"3", "4", "5"}:
		_text(id_node, "Pays", tax.beneficiary_country_code)
	_text(id_node, "CategorieContribuable", tax.beneficiary_category)
	_text(beneficiary, "Resident", 1 if tax.beneficiary_resident else 0)
	_text(beneficiary, "NometprenonOuRaisonsociale", tax.beneficiary_name)
	_text(beneficiary, "Adresse", tax.beneficiary_address)
	if tax.beneficiary_activity:
		_text(beneficiary, "Activite", tax.beneficiary_activity)
	contact = etree.SubElement(beneficiary, "InfosContact")
	_text(contact, "AdresseMail", tax.beneficiary_email)
	_text(contact, "NumTel", tax.beneficiary_phone)


def _certificate(parent, tax):
	certificate = etree.SubElement(parent, "Certificat")
	_identity(certificate, tax)
	_text(certificate, "DatePayement", getdate(tax.transaction_date).strftime("%d/%m/%Y"))
	_text(certificate, "Ref_certif_chez_declarant", tax.certificate_reference)
	operations = etree.SubElement(certificate, "ListeOperations")
	operation = etree.SubElement(operations, "Operation", IdTypeOperation=tax.tej_operation_code)
	_text(operation, "AnneeFacturation", getdate(tax.transaction_date).strftime("%Y"))
	_text(operation, "CNPC", 1 if tax.convention_non_double_taxation else 0)
	_text(operation, "P_Charge", 1 if tax.tax_borne_by_payer else 0)
	_text(operation, "MontantHT", to_millimes(tax.operation_amount_ht))
	_text(operation, "TauxRS", Decimal(str(tax.withholding_rate)).quantize(Decimal("0.01")))
	if Decimal(str(tax.vat_rate or 0)):
		_text(operation, "TauxTVA", Decimal(str(tax.vat_rate)).quantize(Decimal("0.01")))
		_text(operation, "MontantTVA", to_millimes(tax.vat_amount))
	_text(operation, "MontantTTC", to_millimes(tax.settlement_amount))
	_text(operation, "MontantRS", to_millimes(tax.withholding_amount))
	_text(operation, "MontantNetServi", to_millimes(tax.net_paid))
	total = etree.SubElement(certificate, "TotalPayement")
	_text(total, "TotalMontantHT", to_millimes(tax.operation_amount_ht))
	_text(total, "TotalMontantTVA", to_millimes(tax.vat_amount or 0))
	_text(total, "TotalMontantTTC", to_millimes(tax.settlement_amount))
	_text(total, "TotalMontantRS", to_millimes(tax.withholding_amount))
	_text(total, "TotalMontantNetServi", to_millimes(tax.net_paid))


def serialize_batch(batch):
	root = etree.Element("DeclarationsRS", VersionSchema="1.0")
	declarant = etree.SubElement(root, "Declarant")
	first_tax = frappe.get_doc("Withholding Tax", batch.records[0].withholding_tax)
	_text(declarant, "TypeIdentifiant", "1")
	_text(declarant, "Identifiant", str(first_tax.declarant_tax_identifier).strip().upper())
	_text(declarant, "CategorieContribuable", first_tax.declarant_category)
	reference = etree.SubElement(root, "ReferenceDeclaration")
	_text(reference, "ActeDepot", "0" if batch.declaration_type == "INITIAL" else "1")
	_text(reference, "AnneeDepot", batch.declaration_year)
	_text(reference, "MoisDepot", getdate(batch.declaration_month).strftime("%m"))
	groups = {"ADD": "AjouterCertificats", "MODIFY": "ModifierCertificats", "CANCEL": "AnnulerCertificats"}
	for action, element_name in groups.items():
		rows = [row for row in batch.records if row.action == action]
		if not rows:
			continue
		container = etree.SubElement(root, element_name)
		for row in rows:
			tax = frappe.get_doc("Withholding Tax", row.withholding_tax)
			validate_required_identity(tax)
			if getdate(tax.transaction_date).strftime("%Y-%m") != getdate(batch.declaration_month).strftime("%Y-%m"):
				raise frappe.ValidationError(_("All withholding dates must match the batch month."))
			if action == "CANCEL":
				cert = etree.SubElement(container, "Certificat")
				_text(cert, "Ref_certif_chez_declarant", tax.certificate_reference)
			else:
				_certificate(container, tax)
	return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def validate_xml(xml_bytes):
	schema = etree.XMLSchema(etree.parse(str(DECLARATION_XSD)))
	document = etree.fromstring(xml_bytes)
	if not schema.validate(document):
		raise frappe.ValidationError("; ".join(str(error) for error in schema.error_log))
	return True


def generate_batch_xml(batch_name):
	batch = frappe.get_doc("TEJ Export Batch", batch_name)
	batch.check_permission("write")
	if not batch.records:
		frappe.throw(_("At least one Withholding Tax record is required."))
	try:
		xml_bytes = serialize_batch(batch)
		validate_xml(xml_bytes)
	except Exception as exc:
		batch.db_set({"status": "VALIDATION_FAILED", "xml_generation_status": "FAILED", "xsd_validation_status": "INVALID", "validation_errors": str(exc)[:10000]})
		raise
	filename = f"TEJ_RS_{frappe.scrub(batch.business).upper()}_{batch.declaration_year}_{getdate(batch.declaration_month).strftime('%m')}_{batch.name}.xml"
	file_doc = frappe.get_doc({"doctype": "File", "file_name": filename, "content": xml_bytes, "attached_to_doctype": batch.doctype, "attached_to_name": batch.name, "is_private": 1}).insert(ignore_permissions=True)
	checksum = hashlib.sha256(xml_bytes).hexdigest()
	taxes = [frappe.get_doc("Withholding Tax", row.withholding_tax) for row in batch.records]
	batch.db_set({"status": "EXPORT_READY", "created_at": batch.created_at or now_datetime(), "created_by": batch.created_by or frappe.session.user,
		"record_count": len(taxes), "total_withholding_base": sum(Decimal(str(t.withholding_base)) for t in taxes),
		"total_withheld_amount": sum(Decimal(str(t.withholding_amount)) for t in taxes), "generated_filename": filename,
		"xml_file": file_doc.file_url, "xml_checksum": checksum, "xml_generation_status": "GENERATED", "xsd_validation_status": "VALID", "validation_errors": None})
	for tax in taxes:
		state = "EXPORTED_THEN_REVERSED" if tax.status == "REVERSED" else "EXPORTED"
		tax.db_set({"tej_state": state, "last_tej_export_batch": batch.name}, update_modified=False)
	return {"batch": batch.name, "file_url": file_doc.file_url, "filename": filename, "checksum": checksum, "xsd_valid": True}


@frappe.whitelist(methods=["POST"])
def create_monthly_batch(business, year, month):
	from creditflow.permissions import get_user_business, has_cross_business_access

	if not has_cross_business_access() and get_user_business() != business:
		frappe.throw(_("You can only create TEJ exports for your assigned Business."), frappe.PermissionError)
	year, month = int(year), int(month)
	if month < 1 or month > 12:
		frappe.throw(_("Month must be between 1 and 12."))
	if frappe.db.exists("TEJ Export Batch", {"business": business, "declaration_year": year, "declaration_month": f"{year:04d}-{month:02d}-01", "declaration_type": "INITIAL", "status": ["!=", "VALIDATION_FAILED"]}):
		frappe.throw(_("An initial TEJ batch already exists for this Business and month. Use a corrective batch."))
	names = frappe.get_all("Withholding Tax", filters={"business": business, "docstatus": 1, "status": "ACTIVE", "transaction_date": ["between", [f"{year:04d}-{month:02d}-01", frappe.utils.get_last_day(f"{year:04d}-{month:02d}-01")]], "tej_state": "NOT_EXPORTED"}, pluck="name", order_by="transaction_date asc, name asc")
	if not names:
		frappe.throw(_("No eligible active, unexported withholding records exist for this month."))
	batch = frappe.get_doc({"doctype": "TEJ Export Batch", "business": business, "declaration_year": year, "declaration_month": f"{year:04d}-{month:02d}-01", "declaration_type": "INITIAL", "created_at": now_datetime(), "created_by": frappe.session.user, "records": [{"withholding_tax": name, "certificate_reference": frappe.db.get_value("Withholding Tax", name, "certificate_reference"), "action": "ADD"} for name in names]})
	batch.insert()
	return batch.name
