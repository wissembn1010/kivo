const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

function setup() {
    const handlers = {};
    const pending = [];
    const sandbox = {
        cur_frm: { doctype: "Sale" }, locals: { "Sale Item": {} },
        frappe: {
            session: { user: "Administrator" }, user_roles: ["OWNER"],
            ui: { form: { on: (name, events) => handlers[name] = {...handlers[name], ...events} } },
            call: () => new Promise(resolve => pending.push(resolve)),
            model: { set_value: async (dt, name, key, value) => { sandbox.locals[dt][name][key] = value; } },
            db: {get_value: async () => ({message: {selling_price: 100}})},
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(require("node:path").join(__dirname, "../public/js/business_defaults.js"), "utf8"), sandbox);
    const row = {doctype: "Sale Item", name: "row1", product: "p1", quantity: 1, unit_price: 100};
    sandbox.locals["Sale Item"].row1 = row;
    const frm = {
        doc: {doctype: "Sale", docstatus: 0, business: "b1", customer: null, sale_mode: "CASH", amount_paid: 0, outstanding_amount: 0, items: [row]},
        fields_dict: Object.fromEntries(["total_amount", "total_ht", "total_tva", "total_ttc", "amount_paid", "outstanding_amount"].map(k => [k, {df: {fieldtype: "Currency"}}])),
        set_value: async (key, value) => {frm.doc[key] = value;},
        toggle_display() {}, toggle_enable() {}, toggle_reqd() {}, set_df_property() {}, set_intro() {}, refresh_field() {},
    };
    const response = (total = 119) => ({message: {
        total_ht: total / 1.19, total_tva: total - total / 1.19, total_ttc: total, total_amount: total,
        items: [{line_ht: total / 1.19, tva_rate: 19, tva_amount: total - total / 1.19, line_ttc: total}],
    }});
    return {sandbox, handlers, pending, frm, response};
}

test("taxed CASH preview uses TTC119 and MIXED leaves 69 outstanding", async () => {
    for (const mode of ["CASH", "MIXED"]) {
        const {sandbox, pending, frm, response} = setup();
        frm.doc.sale_mode = mode;
        frm.doc.amount_paid = mode === "MIXED" ? 50 : 0;
        const work = sandbox.recalculate_sale_total(frm);
        if (pending.length) pending.shift()(response());
        await work;
        assert.equal(frm.doc.total_amount, 119);
        assert.equal(frm.doc.amount_paid, mode === "CASH" ? 119 : 50);
        assert.equal(frm.doc.outstanding_amount, mode === "CASH" ? 0 : 69);
    }
});

test("submitted refresh and payment events never mutate snapshots", async () => {
    const {sandbox, handlers, pending, frm} = setup();
    frm.doc.docstatus = 1;
    Object.assign(frm.doc, {total_amount:119, total_ht:100, total_tva:19, total_ttc:119, amount_paid:119, outstanding_amount:0, payment_method:"CASH"});
    const before = JSON.stringify(frm.doc);
    await handlers.Sale.refresh(frm);
    sandbox.sync_sale_payment_fields(frm);
    assert.equal(JSON.stringify(frm.doc), before);
    assert.equal(pending.length, 0);
});

test("out of order previews cannot overwrite newer quantities", async () => {
    const {sandbox, pending, frm, response} = setup();
    const first = sandbox.recalculate_sale_total(frm);
    frm.doc.items[0].quantity = 2;
    const second = sandbox.recalculate_sale_total(frm);
    assert.equal(pending.length, 2);
    pending[1](response(238)); await second;
    pending[0](response(119)); await first;
    assert.equal(frm.doc.total_amount, 238);
    assert.equal(frm.doc.amount_paid, 238);
});

test("validate waits for the authoritative preview", async () => {
    const {handlers, pending, frm, response} = setup();
    let done = false;
    const work = Promise.resolve(handlers.Sale.validate(frm)).then(() => {done = true;});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(done, false);
    pending.shift()(response()); await work;
    assert.equal(frm.doc.amount_paid, 119);
});

test("a stale Product pricing reply cannot replace the current Product price", async () => {
    const {sandbox, frm} = setup();
    const replies = [];
    sandbox.frappe.db.get_value = () => new Promise(resolve => replies.push(resolve));
    const old = sandbox.get_product_pricing(frm.doc.items[0]);
    frm.doc.items[0].product = "p2";
    const current = sandbox.get_product_pricing(frm.doc.items[0]);
    replies[1]({message:{selling_price:200}}); await current;
    replies[0]({message:{selling_price:100}}); await old;
    assert.equal(frm.doc.items[0].__selling_price, 200);
});

test("validate waits for pending product pricing and then recalculates", async () => {
    const {sandbox, handlers, pending, frm, response} = setup();
    let pricing;
    sandbox.frappe.db.get_value = () => new Promise(resolve => {pricing = resolve;});
    const edit = handlers["Sale Item"].product(frm, "Sale Item", "row1");
    let saved = false;
    const save = handlers.Sale.validate(frm).then(() => {saved = true;});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(pending.length, 0);
    assert.equal(saved, false);
    pricing({message:{selling_price:200}});
    await new Promise(resolve => setImmediate(resolve));
    pending.shift()(response(238)); await edit;
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(saved, false);
    pending.shift()(response(238)); await save;
    assert.equal(frm.doc.items[0].unit_price, 200);
    assert.equal(frm.doc.amount_paid, 238);
});

test("discount edits use server fiscal totals rather than local HT", async () => {
    const {handlers, pending, frm, response} = setup();
    Object.assign(frm.doc.items[0], {base_price:100, discount_percent:10});
    const edit = handlers["Sale Item"].discount_percent(frm, "Sale Item", "row1");
    await new Promise(resolve => setImmediate(resolve));
    pending.shift()(response(107.1)); await edit;
    assert.equal(frm.doc.items[0].unit_price, 90);
    assert.equal(frm.doc.total_amount, 107.1);
    assert.equal(frm.doc.amount_paid, 107.1);
});

test("failed preview rejects save, and a late preview cannot mutate a submitted Sale", async () => {
    const {sandbox, handlers, pending, frm, response} = setup();
    sandbox.frappe.call = async () => {throw new Error("preview denied");};
    await assert.rejects(handlers.Sale.validate(frm), /preview denied/);
    sandbox.frappe.call = () => new Promise(resolve => pending.push(resolve));
    const work = sandbox.recalculate_sale_total(frm);
    frm.doc.docstatus = 1;
    const before = JSON.stringify(frm.doc);
    pending.shift()(response()); await work;
    assert.equal(JSON.stringify(frm.doc), before);
});

test("pricing and payment field-event chains settle before validation", async () => {
    const {sandbox, handlers, frm, response} = setup();
    sandbox.frappe.model.set_value = async (dt, name, field, value) => {
        const row = sandbox.locals[dt][name];
        if (row[field] === value) return;
        row[field] = value;
        await handlers[dt]?.[field]?.(frm, dt, name);
    };
    frm.set_value = async (field, value) => {
        if (frm.doc[field] === value) return;
        frm.doc[field] = value;
        await handlers.Sale[field]?.(frm);
    };
    sandbox.frappe.call = async () => response(frm.doc.items[0].unit_price * 1.19);
    await handlers["Sale Item"].product(frm, "Sale Item", "row1");
    await sandbox.frappe.model.set_value("Sale Item", "row1", "unit_price", 80);
    await handlers.Sale.validate(frm);
    assert.equal(frm.doc.items[0].discount_percent, 20);
    assert.equal(frm.doc.amount_paid, 95.2);
    assert.equal(frm.__sale_edits.size, 0);
});
