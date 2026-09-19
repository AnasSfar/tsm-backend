"""Offline browser regressions: python -m unittest dev.test_twitter_media_menu."""
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from collectors.spotify.core import twitter


class MediaMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)
        cls.temp = tempfile.TemporaryDirectory()
        cls.image = Path(cls.temp.name) / "image.png"
        cls.image.write_bytes(b"offline file chooser fixture")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.route("**/*", lambda route: route.abort())
        self.page.set_content("""
            <div id="composer">
              <div role="textbox" contenteditable="true">Draft text</div>
              <input id="file" type="file" accept="image/*" hidden>
              <button aria-label="Add media">Media</button>
              <button data-testid="addButton" onclick="window.adds++">Add post</button>
              <button data-testid="tweetButton" onclick="window.posts++">Post</button>
            </div>
            <div id="overlay" style="display:none; position:fixed; inset:0; background:#ddd">
              <div data-testid="Dropdown"><div role="menuitem" tabindex="0">Upload</div></div>
            </div>
            <script>
              window.adds = 0; window.posts = 0;
              const overlay = document.querySelector('#overlay');
              document.querySelector('[aria-label="Add media"]').onclick = () => overlay.style.display = 'block';
              document.querySelector('[role="menuitem"]').onclick = () => {
                overlay.style.display = 'none'; document.querySelector('#file').click();
              };
              document.addEventListener('keydown', e => {
                if (e.key === 'Escape') overlay.style.display = 'none';
              });
            </script>
        """)

    def tearDown(self):
        self.page.close()

    def attach(self):
        return twitter._attach_with_file_chooser(
            self.page, self.page.locator('#composer'), self.image
        )

    def assert_controls_work(self):
        self.assertTrue(twitter._click_thread_add_button(self.page))
        twitter._click_tweet_button(self.page)
        self.assertEqual(self.page.evaluate('[window.adds, window.posts]'), [1, 1])
        self.assertEqual(self.page.get_by_role('textbox').inner_text(), 'Draft text')

    def test_upload_menu_then_thread_and_post(self):
        self.assertTrue(self.attach())
        self.assertEqual(self.page.locator('#file').evaluate('el => el.files.length'), 1)
        self.assert_controls_work()

    def test_legacy_direct_chooser(self):
        self.page.evaluate("""document.querySelector('[aria-label="Add media"]').onclick =
            () => document.querySelector('#file').click()""")
        self.assertTrue(self.attach())
        self.assert_controls_work()

    def test_chooser_failure_closes_menu_before_input_fallback(self):
        self.page.evaluate("document.querySelector('[role=menuitem]').onclick = () => {}")
        self.assertFalse(self.attach())
        self.page.locator('#file').set_input_files(str(self.image))
        self.assert_controls_work()

    def test_remaining_menu_dismissed_without_submitting_draft(self):
        self.page.get_by_role('button', name='Add media', exact=True).click()
        twitter._dismiss_media_upload_menu(self.page)
        self.assertEqual(self.page.evaluate('window.posts'), 0)
        self.assert_controls_work()


if __name__ == '__main__':
    unittest.main()
