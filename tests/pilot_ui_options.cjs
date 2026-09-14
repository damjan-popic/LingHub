// Existing browser scenarios make deliberate optional tool/provider selections.
// Open the ordinary disclosure before interacting with its controls.
async function openComposerOptions(page) {
  if (!await page.locator('#options').isVisible()) await page.locator('#options-toggle').click();
}
async function selectComposerOption(page, selector, value) {
  await openComposerOptions(page);
  return page.locator(selector).selectOption(value);
}
module.exports = {openComposerOptions,selectComposerOption};
