/* Real Chromium UI tests against synthetic records on the dedicated test site.
 * Run after bank_browser_support.setup, then bank_browser_support.cleanup.
 * PLAYWRIGHT_MODULE points to the existing installation; no package install.
 */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs");
const assert = require("assert/strict");
const {execFileSync} = require("child_process");
const dir = "/tmp/kivo-bank-production-review";
const fixture = JSON.parse(fs.readFileSync(`${dir}/fixture.json`, "utf8"));
const origin = "http://kivo-fix001-tests.localhost:8093";
const checks = [];
let browser;

async function api(page, method, args = {}) {
  return page.evaluate(async ({method, args}) => {
    const response = await fetch(`/api/method/${method}`, {method: "POST",
      headers: {"X-Frappe-CSRF-Token": frappe.csrf_token || window.csrf_token || "", "Content-Type": "application/json"},
      body: JSON.stringify(args)});
    return {status: response.status, body: await response.json()};
  }, {method, args});
}

async function login(page, label, target) {
  await page.goto(`${origin}/login${target ? `?redirect-to=${encodeURIComponent(target)}` : ""}`);
  await page.locator("#login_email").fill(fixture.users[label]);
  await page.locator("#login_password").fill(fixture.password);
  await page.locator(".for-login .btn-login:not(.btn-login-with-email-link)").click();
  await page.waitForURL(url => !url.pathname.endsWith("/login"), {timeout: 30000, waitUntil: "domcontentloaded"});
}

async function blocked(page, state) {
  const summary = await api(page, "creditflow.saas_access.subscription_summary");
  assert.equal(summary.body.message.can_write, false, state);
  await page.evaluate(() => {
    localStorage.setItem("subscription_status", "ACTIVE");
    if (frappe.boot) frappe.boot.creditflow_saas = {access_state: "ACTIVE", can_write: true};
  });
  const response = await api(page, "frappe.client.insert", {doc: JSON.stringify({doctype: "Customer",
    customer_name: `Blocked browser ${fixture.token} ${state}`, business: fixture.business})});
  assert.equal(response.status, 403, JSON.stringify(response.body));
  checks.push({check: `Backend blocks ${state}, including forged browser status`, passed: true});
}

async function checkout(page, cycle) {
  await page.goto(`${origin}/creditflow-subscription`);
  await page.locator(`.cf-checkout[data-plan="${fixture.plan}"][data-cycle="${cycle}"]`).click();
  await page.waitForURL(/creditflow-payment-return\?payment=/);
  await page.locator("#cf-payment-status").filter({hasText: "Awaiting your bank transfer"}).waitFor();
  assert.equal((await page.locator("#cf-payment-rib").textContent()).replace(/\s/g, ""), "25190000000150858272");
  const payment = new URL(page.url()).searchParams.get("payment");
  const reference = await page.locator("#cf-payment-reference").textContent();
  const amount = cycle === "MONTHLY" ? "79.000 TND" : "790.000 TND";
  assert.equal(await page.locator("#cf-payment-amount").textContent(), amount);
  await page.locator("#cf-copy-rib").click();
  await page.locator("#cf-form-message").filter({hasText: "Copied"}).waitFor();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "2519 0000 0001 5085 8272");
  checks.push({check: `Customer ${cycle} checkout, exact price/reference/RIB and clipboard`, passed: true});
  return {payment, reference, amount: cycle === "MONTHLY" ? 79 : 790};
}

async function reviewPage(page, payment) {
  await page.goto(`${origin}/desk/creditflow-billing-payment/${payment}`);
  await page.waitForFunction(name => window.cur_frm && cur_frm.doc.name === name && !cur_frm.is_new(), payment);
  await page.getByRole("button", {name: "Payment review", exact: true}).click();
}

async function verify(page, item, number) {
  await reviewPage(page, item.payment);
  await page.getByText("Verify received funds", {exact: true}).click();
  const modal = page.locator(".modal.show").last();
  await modal.locator('[data-fieldname="transaction_id"] input').fill(`BROWSER-${fixture.token}-${number}`);
  await modal.locator('[data-fieldname="amount"] input').fill(String(item.amount));
  await modal.locator('[data-fieldname="transaction_date"] input').fill(new Date().toISOString().slice(0, 10));
  await modal.locator('[data-fieldname="reference"] input').fill(item.reference);
  await modal.locator('[data-fieldname="destination_account"] input').fill("2519 0000 0001 5085 8272");
  await modal.locator('[data-fieldname="notes"] textarea').fill("Synthetic browser fixture: independently checked booked incoming test bank record; no real funds.");
  await modal.locator('input[data-fieldname="confirmed_funds"]').check();
  const response = page.waitForResponse(r => r.url().includes("creditflow.billing.api.admin_review") && r.request().method() === "POST");
  await modal.getByRole("button", {name: "Verify and activate subscription", exact: true}).click();
  assert.equal((await response).status(), 200);
  await page.waitForFunction(() => cur_frm.doc.status === "VERIFIED");
  checks.push({check: `Native admin verification ${number} via independently supplied bank details`, passed: true});
}

(async () => {
  try {
    browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH,
      args: ["--no-sandbox", "--host-resolver-rules=MAP kivo-fix001-tests.localhost 127.0.0.1"]});
    const ownerContext = await browser.newContext({permissions: ["clipboard-read", "clipboard-write"]});
    const adminContext = await browser.newContext();
    const owner = await ownerContext.newPage();
    const admin = await adminContext.newPage();
    await login(owner, "owner");
    await owner.goto(`${origin}/creditflow-subscription`);
    await blocked(owner, "new unpaid expired trial");
    const first = await checkout(owner, "MONTHLY");
    await blocked(owner, "PENDING_PAYMENT");
    await owner.locator("#cf-proof").setInputFiles({name: "synthetic-transfer.png", mimeType: "image/png",
      buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8//8/AwMDEwMDAwMDAwAkBgMB/DXemwAAAABJRU5ErkJggg==", "base64")});
    await owner.getByRole("button", {name: "Submit proof for review", exact: true}).click();
    await owner.locator("#cf-payment-status").filter({hasText: "Proof submitted"}).waitFor();
    await owner.screenshot({path: `${dir}/customer-proof-submitted.png`, fullPage: true});
    await blocked(owner, "PROOF_SUBMITTED");
    const approveOwn = await api(owner, "creditflow.billing.api.admin_review", {payment: first.payment, action: "VERIFIED"});
    assert.equal(approveOwn.status, 403);
    checks.push({check: "Customer cannot approve own payment", passed: true});
    await login(admin, "admin", `/desk/creditflow-billing-payment/${first.payment}`);
    await reviewPage(admin, first.payment);
    await admin.getByText("Mark under review", {exact: true}).click();
    const reviewModal = admin.locator(".modal.show").last();
    await reviewModal.locator('[data-fieldname="notes"] textarea').fill("Checking synthetic received-funds record");
    await reviewModal.getByRole("button", {name: "Save review", exact: true}).click();
    await admin.waitForFunction(() => cur_frm.doc.status === "UNDER_REVIEW");
    await owner.locator("#cf-refresh-payment").click();
    await owner.locator("#cf-payment-status").filter({hasText: "Payment under review"}).waitFor();
    await blocked(owner, "UNDER_REVIEW");
    await verify(admin, first, 1);
    await admin.screenshot({path: `${dir}/admin-payment-verified.png`, fullPage: true});
    await owner.locator("#cf-refresh-payment").click();
    await owner.locator("#cf-payment-status").filter({hasText: "Payment verified"}).waitFor();
    let summary = (await api(owner, "creditflow.saas_access.subscription_summary")).body.message;
    assert.equal(summary.can_write, true);
    const oldEnd = summary.current_period_end;
    const created = await api(owner, "frappe.client.insert", {doc: JSON.stringify({doctype: "Customer",
      customer_name: `Active browser ${fixture.token}`, business: fixture.business})});
    assert.equal(created.status, 200, JSON.stringify(created.body));
    checks.push({check: "Verified activation grants backend writes and updates customer UI", passed: true});
    const renewal = await checkout(owner, "ANNUAL");
    summary = (await api(owner, "creditflow.saas_access.subscription_summary")).body.message;
    assert.equal(summary.current_period_end, oldEnd);
    assert.equal(summary.can_write, true);
    await verify(admin, renewal, 2);
    summary = (await api(owner, "creditflow.saas_access.subscription_summary")).body.message;
    assert.equal(summary.can_write, true);
    assert.ok(summary.current_period_end > oldEnd);
    const records = (await api(admin, "creditflow.billing.api.admin_payment_details", {payment: renewal.payment})).body.message;
    assert.equal(records.payment.billing_period_start, oldEnd);
    checks.push({check: "Annual renewal preserves current access and extends from existing paid end", passed: true});
    const denied = await checkout(owner, "MONTHLY");
    await reviewPage(admin, denied.payment);
    await admin.getByText("Reject", {exact: true}).click();
    const rejection = admin.locator(".modal.show").last();
    await rejection.locator('[data-fieldname="notes"] textarea').fill("Synthetic transfer was not received");
    await rejection.getByRole("button", {name: "Save review", exact: true}).click();
    await admin.waitForFunction(() => cur_frm.doc.status === "REJECTED");
    checks.push({check: "Native admin rejection does not extend the paid subscription", passed: true});
    execFileSync("bench", ["--site", "kivo-fix001-tests.localhost", "execute", "creditflow.tests.bank_browser_support.expire"], {cwd: process.env.BENCH_ROOT, stdio: "pipe"});
    await owner.goto(`${origin}/creditflow-subscription`);
    await owner.getByText("Kivo is read-only. Your historical business data is safe.", {exact: true}).waitFor();
    await blocked(owner, "expired subscription with rejected transfer");
    checks.push({check: "Expiry materialized by actual maintenance code and displayed in browser", passed: true});
    await owner.screenshot({path: `${dir}/customer-subscription-expired.png`, fullPage: true});
    fs.writeFileSync(`${dir}/browser-results.json`, JSON.stringify({passed: true, origin, synthetic_records_only: true, checks}, null, 2));
    console.log(JSON.stringify({passed: true, checks: checks.length, evidence: `${dir}/browser-results.json`}));
  } catch (error) {
    const pages = browser ? browser.contexts().flatMap(context => context.pages()) : [];
    const pageErrors = [];
    for (const page of pages) {
      if (page.url().includes("creditflow-subscription") || page.url().includes("creditflow-payment-return") || page.url().includes("/desk/")) {
        await page.screenshot({path: `${dir}/browser-failure-${pageErrors.length}.png`, fullPage: true});
        pageErrors.push({path: new URL(page.url()).pathname,
          message: await page.locator("#cf-form-message").textContent().catch(() => "")});
      }
    }
    fs.writeFileSync(`${dir}/browser-results.json`, JSON.stringify({passed: false, checks, error: error.message, pageErrors}, null, 2));
    console.error(error.message);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
})();
