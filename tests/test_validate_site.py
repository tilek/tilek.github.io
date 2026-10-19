"""Run with: python3 -m unittest discover -s tests -v"""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from scripts.validate_site import EXPECTED_PODCAST_IDS, main, srcset_urls, validate_site


class ValidateSiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.site = Path(self.temporary.name)
        self.nav = '<nav><a href="/">About</a><a href="/podcasts/">Podcasts</a></nav>'
        self.embeds = "".join(
            f'<iframe src="https://www.youtube-nocookie.com/embed/{video_id}"></iframe>'
            for video_id in sorted(EXPECTED_PODCAST_IDS)
        )
        self.write("index.html", self.nav)
        self.write("podcasts/index.html", self.nav + self.embeds)
        self.write("assets/img/photo.jpg", "image fixture")
        self.write("assets/img/photo-480.webp", "image fixture")
        self.write("assets/img/photo-800.webp", "image fixture")
        self.write("assets/css/main.css", "body {}")
        self.write("assets/js/main.js", "/* fixture */")

    def write(self, name, content):
        path = self.site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def add_html(self, content, page="index.html"):
        path = self.site / page
        path.write_text(path.read_text(encoding="utf-8") + content, encoding="utf-8")

    def errors(self):
        return validate_site(self.site).errors

    def test_minimal_site_passes(self):
        self.assertEqual(self.errors(), [])

    def test_root_relative_relative_and_same_site_urls_pass(self):
        self.add_html(
            '<link href="../assets/css/main.css?v=1&amp;theme=dark" rel="stylesheet">'
            '<img src="../assets/img/photo.jpg#preview">'
            '<a href="https://tilek.net/podcasts/?view=all#video">Podcasts</a>'
            '<a href="https://tilek.github.io/">Home</a>'
            '<a href="//www.tilek.net/podcasts">Podcasts</a>'
            '<script src="/assets/js/main.js"></script>'
            '<source srcset="/assets/img/photo-480.webp 480w,/assets/img/photo-800.webp 800w,">',
            page="podcasts/index.html",
        )
        self.assertEqual(self.errors(), [])

    def test_nested_relative_html_file_and_encoded_asset_pass(self):
        self.write("assets/img/with space.jpg", "image fixture")
        self.write("archive/topic/index.html", '<a href="../../podcasts/">Podcasts</a><img src="../../assets/img/with%20space.jpg">')
        self.add_html('<a href="archive/topic/index.html">Archive</a>')
        self.assertEqual(self.errors(), [])

    def test_missing_references_fail_on_all_html_pages(self):
        for attribute, tag in (("href", "a"), ("src", "img"), ("src", "script")):
            with self.subTest(attribute=attribute, tag=tag):
                self.write("archive/example.html", f'<{tag} {attribute}="../missing.txt">')
                errors = self.errors()
                self.assertTrue(any("archive/example.html:1" in error and "missing.txt" in error for error in errors), errors)

    def test_missing_webp_srcset_fails_even_with_existing_fallback(self):
        self.add_html(
            '<picture><source srcset="/assets/img/photo-480.webp 480w,/assets/img/missing-800.webp 800w,">'
            '<img src="/assets/img/photo.jpg"></picture>'
        )
        errors = self.errors()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("source[srcset]", errors[0])
        self.assertIn("missing-800.webp", errors[0])

    def test_missing_absolute_same_site_asset_fails(self):
        for host in ("tilek.net", "www.tilek.net", "tilek.github.io"):
            with self.subTest(host=host):
                self.write("archive.html", f'<img src="https://{host}/missing.jpg">')
                self.assertTrue(any("missing.jpg" in error for error in self.errors()))

    def test_external_urls_and_dynamic_controls_are_not_fetched(self):
        self.add_html(
            '<a href="https://example.invalid/missing.html">External</a>'
            '<a href="//example.invalid/missing.html">External</a>'
            '<a href="mailto:hello@example.invalid">Email</a><a href="tel:+123">Phone</a>'
            '<a href="javascript:void(0)">Open</a><a href="#js-created-panel">Panel</a><a href="">Disabled</a>'
            '<img src="data:image/png;base64,AAAA"><img src="blob:https://tilek.net/test">'
            '<script>const html = \'<img src="/dynamic-placeholder.jpg">\';</script>'
            '<!-- <img src="/commented-out.jpg"> -->'
        )
        self.assertEqual(self.errors(), [])

    def test_srcset_data_uri_does_not_hide_following_local_candidate(self):
        self.assertEqual(
            list(srcset_urls("data:image/png;base64,AAAA 1x, /missing.webp 2x")),
            ["data:image/png;base64,AAAA", "/missing.webp"],
        )
        self.add_html('<img srcset="data:image/png;base64,AAAA 1x, /missing.webp 2x">')
        self.assertTrue(any("missing.webp" in error for error in self.errors()))

    def test_directory_without_index_is_not_a_valid_target(self):
        (self.site / "empty").mkdir()
        self.add_html('<a href="/empty/">Empty</a>')
        self.assertTrue(any("missing empty/index.html" in error for error in self.errors()))

    def test_base_href_resolves_relative_assets(self):
        self.add_html('<base href="/assets/"><img src="img/photo.jpg">')
        self.assertEqual(self.errors(), [])

    def test_missing_required_pages_fail(self):
        for page in ("index.html", "podcasts/index.html"):
            with self.subTest(page=page):
                path = self.site / page
                original = path.read_text(encoding="utf-8")
                path.unlink()
                self.assertIn(f"Required page missing: {page}", self.errors())
                self.write(page, original)

    def test_missing_navigation_fails_even_if_link_exists_elsewhere(self):
        self.write("index.html", '<nav><a href="/">About</a></nav><a href="/podcasts/">Podcasts</a>')
        self.assertIn("index.html: navigation must link to podcasts/index.html", self.errors())

    def test_missing_podcast_embed_fails(self):
        video_id = sorted(EXPECTED_PODCAST_IDS)[0]
        self.write("podcasts/index.html", self.nav + self.embeds.replace(video_id, "not-the-video"))
        self.assertTrue(any("missing podcast embeds: " + video_id in error for error in self.errors()))

    def test_podcast_ids_on_wrong_host_do_not_pass(self):
        self.write("podcasts/index.html", self.nav + self.embeds.replace("www.youtube-nocookie.com", "example.invalid"))
        self.assertTrue(any("missing podcast embeds" in error for error in self.errors()))

    def test_additional_podcast_is_allowed(self):
        self.add_html('<iframe src="https://www.youtube.com/embed/new-video"></iframe>', page="podcasts/index.html")
        self.assertEqual(self.errors(), [])

    def test_nonexistent_site_fails(self):
        self.assertTrue(validate_site(self.site / "absent").errors)

    def test_cli_exit_status_reflects_broken_asset(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main([str(self.site)]), 0)
            self.add_html('<img src="/missing.jpg">')
            self.assertEqual(main([str(self.site)]), 1)


if __name__ == "__main__":
    unittest.main()
