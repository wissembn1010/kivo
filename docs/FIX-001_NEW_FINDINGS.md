# Separate security finding discovered during FIX-001

## FIX001-RELATED-01 — persisted child ownership bypass (P0-equivalent, confirmed, OPEN)

This is recorded separately under the instruction to report additional P0-equivalent instances without automatically changing other DocTypes. No implementation for this finding is included in the stored-Business hotfix. No work on FIX-002 was performed.

### Reproduction and impact

Executed on `kivo-fix001-tests.localhost`, using newly created synthetic Businesses A/B, Customers, Products, draft Sales with one Sale Item each, and an A STAFF user. All probe database changes were rolled back in `finally`.

As the A STAFF user, call the actual `frappe.client.bulk_update` with:

```python
client.bulk_update(frappe.as_json([{
    "doctype": "Sale Item",
    "docname": foreign_sale.items[0].name,
    "parent": own_sale.name,
    "parenttype": "Sale",
    "parentfield": "items",
    "product": own_product.name,
    "quantity": 9,
}]))
```

Observed after the stored-Business patch:

```text
failed_docs = []
BEFORE: parent=SALE-00002 (Business B), quantity=1.0
AFTER:  parent=SALE-00001 (Business A), quantity=9.0
Result: VULNERABLE
```

The probe directly queried the persisted Sale Item before/after the endpoint call. This confirms modification and transfer of an existing foreign child, not merely an in-memory permission result. Known foreign child IDs are required. The draft parent totals are not recalculated by this bulk child save, creating an additional integrity inconsistency. Submitted-document variants have not been tested and are not claimed here.

### Root cause

- `frappe/client.py::bulk_update` loads the child and applies incoming fields before saving.
- `frappe/permissions.py::has_child_permission` uses the supplied child document's `parenttype`, `parentfield`, and `parent` to authorize its parent. Its stored-parent lookup applies when the input is a string, not when it is the already-mutated child Document.
- The forged parent is a legitimate A-owned Sale, so the new stored-Business check on that parent succeeds.
- `Sale Item` has no `business` field and is not registered in Kivo's tenant hook mapping. Its controller has no independent stored-parent guard.
- Frappe's timestamp check loads the original child but does not authorize the original parent's Business.

Other child tables and parent saves containing foreign child IDs warrant a separate bounded remediation and regression matrix. Their exploitation was not automatically assumed or fixed here.

### Closure consequence

The original B-28 Business-field takeover is blocked, but the remediation plan's FIX-001 forged-child/parent acceptance criterion and the broader tenant mutation invariant are not satisfied. FIX-001 must remain **PARTIAL / NOT CLOSED** until this separately recorded bypass is resolved and tested.

Probe evidence: `/tmp/kivo_fix001_child_probe.log`.
