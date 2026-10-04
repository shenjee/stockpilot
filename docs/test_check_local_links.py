"""Offline regression checks for the documentation target checker."""

from pathlib import Path
import tempfile
import unittest

from check_local_links import broken_links, destinations


class LocalLinksTest(unittest.TestCase):
    def test_links_images_references_and_titles(self):
        source = '[a](a.md) ![b](b.png "title")\n[r]: <two words.md> "title"'
        self.assertEqual(list(destinations(source)), [(1, 'a.md'), (1, 'b.png'), (2, 'two words.md')])

    def test_code_is_not_a_link(self):
        source = '```md\n[a](missing.md)\n```\n~~~\n[b](missing.md)\n~~~\n`[c](missing.md)`\n    [d](missing.md)'
        self.assertEqual(list(destinations(source)), [])

    def test_local_paths_fragments_queries_and_external_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'space name.md').touch()
            (root / 'docs').mkdir()
            path = root / 'docs' / 'index.md'
            path.write_text('\n'.join([
                '[encoded](../space%20name.md#heading)',
                '[root](/space%20name.md?raw=1)',
                '[directory](../docs/)',
                '[anchor](#heading)',
                '[web](https://example.com/missing)',
                '[relative web](//example.com/missing)',
                '[email](mailto:person@example.com)',
                '[missing](missing.md#heading)',
                '[reference]: ../missing.md',
            ]))
            self.assertEqual(broken_links(path, root), [
                'docs/index.md:8: missing.md#heading',
                'docs/index.md:9: ../missing.md',
            ])


if __name__ == '__main__':
    unittest.main()
