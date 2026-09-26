// v0.2.0 GUI end-to-end (#106) against the *real* dev-stack with the fake LLM.
//
//   bash scripts/dev-stack.sh up --fake-llm   # prints the api/web ports
//   E2E_BASE_URL=http://localhost:<web-port> pnpm test:e2e
//   bash scripts/dev-stack.sh down -v
//
// One test walks the #106 checklist through the UI only (no API shortcuts; the
// ids the test reads come from the URL the app itself wrote). Screenshots land
// under e2e/artifacts/ (gitignored).
//
// The fake LLM (`MORPHLOOP_LLM_PROVIDER=fake`, #130) answers every chat turn
// with the deterministic "[fake LLM] ..." text, so step 3/4 need no key.

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const ARTIFACTS = join(__dirname, "artifacts");
const TOPIC_TITLE = "DNS name resolution"; // parent topic: its TOC holds the lookup-path doc and the resolver doc (lab)
const DOC_TITLE = "How an application looks up a name";
const DOC_ID = "network.dns.lookup-path";
const FAKE_REPLY = "[fake LLM]";
const CHAT_TEXT = "What does this mean, and what should I check next?";
const NOTE = `e2e note ${Date.now()}`;

test.beforeAll(() => mkdirSync(ARTIFACTS, { recursive: true }));

let shotIndex = 0;
async function shot(page: Page, name: string): Promise<void> {
  shotIndex += 1;
  await page.screenshot({ path: join(ARTIFACTS, `${String(shotIndex).padStart(2, "0")}-${name}.png`), fullPage: true });
}

test("v0.2.0 GUI works end to end (#106)", async ({ page }) => {
  test.slow();

  await test.step("1. open /v2, pick a topic, start a session (ws created/selected)", async () => {
    await page.goto("/v2");
    await expect(page.getByRole("heading", { name: "morphloop v2" })).toBeVisible();
    await page.getByRole("button", { name: `Start ${TOPIC_TITLE}`, exact: true }).click();
    await expect(page).toHaveURL(/[?&]session=ses_[0-9A-Za-z]+/);
    await expect(page).toHaveURL(/[?&]ws=ws_[0-9A-Za-z]+/);
    await expect(page.locator("header.app-header strong")).toHaveText("software-engineering");
    await shot(page, "session-started");
  });

  await test.step("2. TOC shows docs; open one; blocks render", async () => {
    const toc = page.locator('nav[aria-label="Table of contents"]');
    await toc.getByRole("button", { name: DOC_TITLE, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`[?&]doc=${DOC_ID.replace(/\./g, "\\.")}`));
    await expect(page.locator("main.main-pane article h1")).toHaveText(DOC_TITLE);
    await expect(page.locator('[data-block-id="summary"]')).toBeVisible();
    await expect(page.locator("[data-doc-id][data-block-id]").first()).toBeVisible();
    await shot(page, "doc-open");
  });

  await test.step("3. select text -> highlight saved -> popup -> [fake LLM] reply", async () => {
    // Select the first ~24 characters of the summary block the way a drag
    // would: a DOM Range plus the mouseup the highlight pane listens for.
    await page.evaluate(() => {
      const block = document.querySelector('[data-block-id="summary"]');
      if (!block) throw new Error("no summary block");
      const node = document.createTreeWalker(block, NodeFilter.SHOW_TEXT).nextNode();
      if (!node) throw new Error("no text in the summary block");
      const range = document.createRange();
      range.setStart(node, 0);
      range.setEnd(node, Math.min(24, node.textContent?.length ?? 0));
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    });
    const toolbar = page.locator(".selection-toolbar");
    await expect(toolbar).toBeVisible();
    await toolbar.getByRole("button", { name: "Ask AI / chat" }).click();

    const popup = page.locator(".popup-chat");
    await expect(popup).toBeVisible();
    await expect(page.locator('section[aria-label="Saved highlights"] .highlights li').first()).toBeVisible();

    await popup.locator('textarea[aria-label="Message"]').fill(CHAT_TEXT);
    await popup.locator('button:has-text("Send")').click();
    await expect(popup.locator(".chat-msg.assistant")).toContainText(FAKE_REPLY);
    await shot(page, "highlight-popup-reply");
    await popup.locator(".popup-chat-close").click();
  });

  await test.step("4. main chat pane: send -> reply appears", async () => {
    const chat = page.locator('section[aria-label="Chat"]');
    const send = chat.locator("form.chat-form button");
    await chat.locator('textarea[aria-label="Message"]').fill(CHAT_TEXT);
    await expect(send).toBeEnabled();
    await send.click();
    await expect(chat.locator(".chat-msg.tutor").filter({ hasText: FAKE_REPLY })).toBeVisible();
    await shot(page, "main-chat-reply");
  });

  await test.step("5. memo pane: append a note -> it is listed", async () => {
    const memo = page.locator('section[aria-label="Learning notes"]');
    await memo.locator('textarea[aria-label="New note"]').fill(NOTE);
    const add = memo.getByRole("button", { name: "Add note" });
    await expect(add).toBeEnabled();
    await add.click();
    await expect(memo.locator(".memo-body").filter({ hasText: NOTE })).toBeVisible();
    await shot(page, "memo-note");
  });

  await test.step("6. drill pane: answer a choice item -> answered state", async () => {
    const drill = page.locator('section[aria-label="Drills"]');
    // Hold the <li> itself: its answer fieldset is replaced by "Answered" after submit.
    const item = drill.locator("li").filter({ has: page.locator("fieldset", { hasText: "Choose an answer" }) }).first();
    await expect(item).toBeVisible();
    const itemText = (await item.locator("p").first().innerText()).slice(0, 40);
    await item.locator('input[type="radio"]').first().check();
    await item.getByRole("button", { name: "Submit answer" }).click();
    const answered = drill.locator("li").filter({ hasText: itemText });
    await expect(answered.getByText("Answered", { exact: true })).toBeVisible();
    await shot(page, "drill-answered");
  });

  await test.step("7. embedded diagram renders; a lab directive starts a lab", async () => {
    await page.locator('[data-block-id="diagram"]').scrollIntoViewIfNeeded();
    await expect(page.locator("svg.viz-svg")).toBeVisible();
    await expect(page.locator(".viz h3")).toBeVisible();

    // The lab is embedded in the resolver doc (#141); open it first.
    await page.locator('nav[aria-label="Table of contents"]')
      .getByRole("button", { name: 'Stub resolver and /etc/resolv.conf', exact: true }).click();
    const startLab = page.getByRole("button", { name: "Start lab" });
    await expect
      (
        startLab,
        "the loaded workbook embeds no `::artifact{type=lab ...}` directive (only the " +
          "`dns-resolution-flow` diagram), and no pane outside the inline LabView offers a " +
          "Start lab control, so the GUI cannot start the lab. The lab API/runtime is " +
          "covered by the non-GUI tests instead.",
      )
      .toHaveCount(1);
    if ((await startLab.count()) > 0) {
      await startLab.first().click();
      await expect(page.locator('section[aria-label="Lab terminal"] [role="status"]')).toHaveText(
        "running",
        { timeout: 180_000 },
      );
    }
    await shot(page, "diagram-and-lab");
  });

  await test.step("8. reload restores session/ws/doc/highlight/memo/answered state", async () => {
    const url = page.url();
    await page.reload();
    await expect(page).toHaveURL(url);
    await expect(page.locator("header.app-header strong")).toHaveText("software-engineering");
    // Step 7 left the resolver doc (lab) open: reload must restore that doc.
    await expect(page.locator("main.main-pane article h1")).toHaveText("Stub resolver and /etc/resolv.conf");
    await page.locator('nav[aria-label="Table of contents"]').getByRole("button", { name: DOC_TITLE, exact: true }).click();
    await expect(page.locator("main.main-pane article h1")).toHaveText(DOC_TITLE);
    await expect(page.locator('[data-block-id="summary"]')).toBeVisible();
    await expect(page.locator('section[aria-label="Saved highlights"] .highlights li').first()).toBeVisible();
    await expect(page.locator('section[aria-label="Learning notes"] .memo-body').filter({ hasText: NOTE })).toBeVisible();
    await expect(drillAnswered(page)).toBeVisible();
    await expect(page.locator("svg.viz-svg")).toBeVisible();
    await shot(page, "reloaded-restored");
  });

  await test.step("9. open / (the v0.1 page) -> it still loads", async () => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "morphloop", exact: true })).toBeVisible();
    await shot(page, "v01-page");
  });
});

function drillAnswered(page: Page) {
  return page
    .locator('section[aria-label="Drills"] [role="status"]')
    .filter({ hasText: "Answered" })
    .first();
}
