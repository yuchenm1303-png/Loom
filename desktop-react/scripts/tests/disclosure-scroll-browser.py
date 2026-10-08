from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=str(Path('desktop-react/.cache/playwright/chromium-1243/chrome-win64/chrome.exe').resolve()), headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto('http://127.0.0.1:5198/scripts/fixtures/long-conversation.html?motion=1&sections=1')
    page.wait_for_function("document.querySelector('.transcript-scroll')?.dataset.following === 'true'")
    page.evaluate('''() => {
      const content = document.querySelector('.transcript');
      const entry = document.createElement('div');
      entry.innerHTML = '<button id="disclosure-probe" aria-expanded="false">Details</button><div class="task-flow-inline-detail-grid"><div class="task-flow-inline-detail-inner"><div class="task-flow-inline-detail"><pre>' + 'output line\\n'.repeat(30) + '</pre></div></div></div>';
      content.append(entry);
      const button = entry.querySelector('button'), detail = entry.querySelector('.task-flow-inline-detail-grid');
      button.onclick = () => { const open = button.getAttribute('aria-expanded') !== 'true'; button.setAttribute('aria-expanded', String(open)); detail.classList.toggle('open', open); };
      const scroller = document.querySelector('.transcript-scroll');
      const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop');
      window.scrollWrites = 0;
      Object.defineProperty(scroller, 'scrollTop', { get() { return descriptor.get.call(this); }, set(value) { window.scrollWrites++; descriptor.set.call(this, value); } });
    }''')
    page.wait_for_timeout(600)
    page.evaluate('window.scrollWrites = 0')
    page.locator('#disclosure-probe').click()
    page.wait_for_timeout(450)
    assert page.locator('.transcript-scroll').get_attribute('data-following') == 'false'
    assert page.evaluate('window.scrollWrites') == 0
    for _ in range(6):
        page.locator('#disclosure-probe').press('Enter')
        page.wait_for_timeout(40)
    page.wait_for_timeout(450)
    assert page.evaluate('window.scrollWrites') == 0
    page.locator('.transcript-jump-latest').click()
    page.wait_for_function("document.querySelector('.transcript-scroll').dataset.following === 'true'")
    browser.close()
    print('PASS: disclosure cancels bottom follow, rapid keyboard reversal causes zero controller scroll writes, jump restores follow')
