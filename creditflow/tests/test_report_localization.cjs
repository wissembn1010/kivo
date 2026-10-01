const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const translations = {'Negative':'Négatif','UNIT':'Unité','EXCEEDED':'Dépassé','Reversed':'Contrepassé','OPENING':'Solde initial','Opening balance':'Solde initial','Walk-in Customer':'Client de passage'};
function report(name) {
  const sandbox = {frappe:{query_reports:{}, datetime:{month_start:()=>"2026-10-01", month_end:()=>"2026-10-31"}}, __:(v,args,context)=>context==='Sale Mode' && v==='CASH' ? 'Au comptant' : translations[v] || v};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../creditflow/report',name,name+'.js'),'utf8'), sandbox);
  return Object.values(sandbox.frappe.query_reports)[0];
}
test('report formatters translate presentation without mutating canonical row values', () => {
  for (const [name,field,value,expected] of [
    ['stock_on_hand','stock_status','Negative','Négatif'], ['stock_on_hand','unit','UNIT','Unité'],
    ['customer_balances','credit_status','EXCEEDED','Dépassé'], ['purchases_report','reversal_status','Reversed','Contrepassé'],
    ['sales_report','sale_mode','CASH','Au comptant'], ['customer_statement','transaction_type','OPENING','Solde initial'],
    ['supplier_statement','transaction_type','OPENING','Solde initial']]) {
    const data = {[field]:value}; const before = JSON.stringify(data);
    assert.equal(report(name).formatter(value,0,{fieldname:field},data,v=>v),expected);
    assert.equal(JSON.stringify(data),before);
  }
});
test('document references and actual customer names are never translated as sentinels', () => {
  for (const name of ['customer_statement','supplier_statement']) {
    const formatter=report(name).formatter;
    assert.equal(formatter('Opening balance',0,{fieldname:'reference'},{transaction_type:'PAYMENT'},v=>v),'Opening balance');
    assert.equal(formatter('Opening balance',0,{fieldname:'reference'},{transaction_type:'OPENING'},v=>v),'Solde initial');
  }
  const formatter=report('sales_report').formatter;
  assert.equal(formatter('Walk-in Customer',0,{fieldname:'customer_name'},{customer:'real-customer'},v=>v),'Walk-in Customer');
  assert.equal(formatter('Walk-in Customer',0,{fieldname:'customer_name'},{customer:null},v=>v),'Client de passage');
});
