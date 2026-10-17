"""Playground browser flow using rules, without live models or real customer data."""
import tempfile
import threading
import unittest
from pathlib import Path
from sel.service import CallService
from sel.server import serve

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parent.parent

@unittest.skipIf(sync_playwright is None, 'Playwright is not installed')
class PlaygroundBrowser(unittest.TestCase):
    def test_read_practise_retry_progress_and_mobile(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = CallService(tmp, ROOT / 'packs', chat_factory=lambda: None)
            rep = service.add_person('Practice Rep', 'rep')['id']
            other = service.add_person('Other Rep', 'rep')['id']
            server = serve(service, port=0, quiet=True)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{server.server_address[1]}'
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                    errors = []
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.add_init_script(f"localStorage.setItem('sel.viewer', JSON.stringify('{rep}'))")
                    page.goto(base + '/#playbook/demo-solar')
                    page.get_by_role('heading', name='Playground', exact=True).wait_for()
                    page.get_by_role('button', name='Library', exact=True).click()
                    page.locator('.doc-row').first.get_by_role('button', name='Read', exact=True).click()
                    page.locator('.doc-text').first.wait_for()
                    page.locator('.doc-row').first.get_by_role('button', name='Practise this', exact=True).click()
                    response = page.get_by_label('Your response (English)')
                    response.fill('I would explain the conditions first. What would you like to understand?')
                    page.reload()
                    response = page.get_by_label('Your response (English)')
                    self.assertIn('conditions', response.input_value())
                    page.get_by_role('button', name='Get feedback', exact=True).click()
                    page.get_by_role('heading', name='One thing to work on').wait_for()
                    page.get_by_role('button', name='Try again', exact=True).click()
                    response.fill('Which condition matters most to your business?')
                    page.get_by_role('button', name='Get feedback', exact=True).click()
                    page.get_by_role('heading', name='One thing to work on').wait_for()
                    page.get_by_role('button', name='My progress', exact=True).click()
                    page.wait_for_function("document.querySelectorAll('summary').length === 2")
                    page.locator('summary').first.click()
                    self.assertIn('Which condition', page.locator('main').inner_text())
                    for width in (1440, 390, 320, 720):
                        page.set_viewport_size({'width': width, 'height': 900})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth - innerWidth'), 1, str(width) + str(page.evaluate("[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>[e.tagName,e.className,e.getBoundingClientRect().right]).slice(-15)")))
                    page.emulate_media(reduced_motion='reduce')
                    page.get_by_role('button', name='Practise', exact=True).focus()
                    page.keyboard.press('Enter')
                    page.get_by_label('Your response (English)').wait_for()
                    page.get_by_role('button', name='Back to exercises', exact=True).click()
                    page.get_by_role('button', name='Practise: That is too expensive. Why should I pay that much?', exact=True).click()
                    page.get_by_label('Your response (English)').fill('What are you comparing the price with?')
                    page.get_by_role('button', name='Get feedback', exact=True).click()
                    page.get_by_role('heading', name='One thing to work on').wait_for()
                    # Viewer isolation: neither feedback nor an unfinished response follows another rep.
                    page.select_option('#viewer', other)
                    page.get_by_role('button', name='My progress', exact=True).click()
                    page.get_by_text('No submitted exercises yet. Start in Practise.').wait_for()
                    page.get_by_role('button', name='Practise', exact=True).click()
                    self.assertEqual(page.locator('textarea').count(), 0)
                    page.set_viewport_size({'width': 1440, 'height': 1000})
                    page.screenshot(path=str(Path(tempfile.gettempdir()) / 'sel-playground-desktop.png'), full_page=True)
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.screenshot(path=str(Path(tempfile.gettempdir()) / 'sel-playground-mobile.png'), full_page=True)
                    page.get_by_role('link', name='Start practice', exact=True).click()
                    page.get_by_role('heading', name='Call simulator', exact=True).wait_for()
                    self.assertTrue(page.url.endswith('#sim'))
                    page.get_by_role('button', name='Open a call', exact=True).wait_for()
                    page.goto(base + '/static/design-system.html')
                    page.locator('#cards').wait_for()
                    self.assertEqual(errors, [])
                    browser.close()
            finally:
                service.wait_idle()
                service.bus.close()
                server.shutdown()
                server.server_close()
