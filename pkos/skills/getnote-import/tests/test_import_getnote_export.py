#!/usr/bin/env python3
"""Tests for import_getnote_export.py — the offline getnote HTML export ingester.

Run: python3 pkos/skills/getnote-import/tests/test_import_getnote_export.py
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_getnote_export.py"
_spec = importlib.util.spec_from_file_location("import_getnote_export", SCRIPT)
gi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gi)


# --- Fixture HTML, mirroring the verified real-export structure --------------

def _page(title, note_inner):
    return (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        f"<title>{title}</title></head><body>"
        '<div id="jsonData" data-json="ZW5jcnlwdGVk"></div>'
        '<div class="note-container"><div class="note">'
        f"{note_inner}"
        "</div></div></body></html>"
    )


LINK_NOTE = _page("如何使用ChatGPT助力健身", """
    <h1>如何使用ChatGPT助力健身</h1>
    <p>创建于：2025-03-24 13:19:36</p>
    <p>标签：<span class="tag">AI链接笔记</span><span class="tag">健康与健身</span></p>
    <hr>
    <div class="attachment">原文：<a href="https://flip.it/9mJLyT" target="_blank">7 prompts</a></div>
    <p><p>ChatGPT 擅长处理健身后勤，如计算<strong>宏量营养素</strong>。</p></p>
""")

EXCERPT_NOTE = _page("", """
    <p>创建于：2024-06-22 10:30:21</p>
    <p>标签：<span class="tag">得到</span><span class="tag">跟熊浩学沟通·30讲</span></p>
    <hr>
    <p><p>王老吉广告语把产品本质做了意义重塑。</p></p>
    <blockquote><p><p>怕上火喝王老吉——它在销售一种恐惧。</p></p></blockquote>
""")

PLAIN_TITLED = _page("2.6 网络配置工具 pipework", """
    <h1>2.6 网络配置工具 pipework</h1>
    <p>创建于：2025-02-22 18:23:22</p>
    <p>标签：<span class="tag">K8S</span><span class="tag">第25章&amp;amp;CKA</span><span class="tag">..</span></p>
    <hr>
    <p><h2>Installation</h2><pre><code class="language-bash">git clone https://x.git</code></pre></p>
""")

PLAIN_UNTITLED = _page("", """
    <p>创建于：2024-08-11 17:05:02</p>
    <p>标签：<span class="tag">得到</span><span class="tag">自我提升</span></p>
    <hr>
    <p><p>来看看优秀的人到底有多优秀，给自己加点码。</p></p>
""")

IMAGE_ONLY = _page("系统错误代码对照表", """
    <h1>系统错误代码对照表</h1>
    <p>创建于：2025-09-10 10:46:58</p>
    <p>标签：<span class="tag">图片笔记</span></p>
    <hr>
    <div class="attachment"><div><img class="zoomable" src="files/x.jpeg" alt="IMG.JPG"/></div></div>
""")


def _write(tmp, name, html):
    p = Path(tmp) / "notes" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_link_note_parses_source_url(self):
        p = _write(self.tmp, "aaaa1111.html", LINK_NOTE)
        note = gi.parse_note_html(p)
        self.assertEqual(note["getnote_id"], "aaaa1111")
        self.assertEqual(note["title"], "如何使用ChatGPT助力健身")
        self.assertEqual(note["created"], "2025-03-24")
        self.assertEqual(note["source_url"], "https://flip.it/9mJLyT")
        self.assertIn("健康与健身", note["tags"])
        self.assertEqual(note["excerpt"], "")
        self.assertIn("宏量营养素", note["summary"])
        self.assertEqual(gi.route(note), ("50-References", "reference"))

    def test_excerpt_note_splits_blockquote(self):
        p = _write(self.tmp, "bbbb2222.html", EXCERPT_NOTE)
        note = gi.parse_note_html(p)
        self.assertEqual(note["title"], "")
        self.assertIn("意义重塑", note["summary"])
        self.assertIn("销售一种恐惧", note["excerpt"])
        self.assertEqual(gi.route(note), ("50-References", "reference"))

    def test_plain_titled_routes_knowledge(self):
        p = _write(self.tmp, "cccc3333.html", PLAIN_TITLED)
        note = gi.parse_note_html(p)
        self.assertEqual(note["title"], "2.6 网络配置工具 pipework")
        self.assertEqual(note["excerpt"], "")
        self.assertEqual(gi.route(note), ("10-Knowledge", "knowledge"))
        # Fenced code block survives the HTML→MD round-trip.
        self.assertIn("```", note["summary"])
        self.assertIn("git clone", note["summary"])

    def test_plain_untitled_routes_idea(self):
        p = _write(self.tmp, "dddd4444.html", PLAIN_UNTITLED)
        note = gi.parse_note_html(p)
        self.assertEqual(note["title"], "")
        self.assertEqual(gi.route(note), ("20-Ideas/观点心得", "idea"))

    def test_image_only_note_skipped(self):
        p = _write(self.tmp, "eeee5555.html", IMAGE_ONLY)
        self.assertIsNone(gi.parse_note_html(p))

    def test_double_escaped_tag_unescaped(self):
        p = _write(self.tmp, "cccc3333.html", PLAIN_TITLED)
        note = gi.parse_note_html(p)
        self.assertIn("第25章&CKA", note["tags"])   # &amp;amp; -> &
        self.assertNotIn("..", note["tags"])         # punctuation-only tag dropped


class MarkdownTests(unittest.TestCase):
    def test_html_to_md_basics(self):
        md = gi.html_to_md("<h2>标题</h2><p>正文 <strong>粗</strong> 字</p>")
        self.assertIn("## 标题", md)
        self.assertIn("**粗**", md)

    def test_build_markdown_valid_frontmatter(self):
        note = {
            "getnote_id": "id123", "title": "测试", "created": "2025-01-01",
            "tags": ["得到", "跟熊浩学沟通·30讲", "a:b"], "source_url": "",
            "summary": "正文", "excerpt": "摘抄一句", "has_image": False,
        }
        md = gi.build_markdown(note, "reference")
        self.assertTrue(md.startswith("---\n"))
        self.assertIn("getnote_id: id123", md)
        self.assertIn("## 摘抄", md)
        self.assertIn("> 摘抄一句", md)
        if yaml is not None:
            fm = yaml.safe_load(md[3:md.find("\n---", 3)])
            self.assertEqual(fm["type"], "reference")
            self.assertEqual(fm["getnote_id"], "id123")
            self.assertIn("a:b", fm["tags"])  # special-char tag survived quoting

    def test_yaml_tag_quoting(self):
        # CJK content (fullwidth punctuation) is safe unquoted — YAML only reacts
        # to ASCII flow-syntax characters.
        self.assertEqual(gi._yaml_tag("plain"), "plain")
        self.assertEqual(gi._yaml_tag("跟熊浩学沟通·30讲"), "跟熊浩学沟通·30讲")
        self.assertTrue(gi._yaml_tag("a,b").startswith('"'))
        self.assertTrue(gi._yaml_tag("a:b").startswith('"'))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.export = tempfile.mkdtemp()
        self.vault = tempfile.mkdtemp()
        for name, html in [("a1.html", LINK_NOTE), ("b2.html", EXCERPT_NOTE),
                           ("c3.html", PLAIN_TITLED), ("d4.html", PLAIN_UNTITLED),
                           ("e5.html", IMAGE_ONLY)]:
            _write(self.export, name, html)

    def test_run_writes_and_dedups(self):
        rc = gi.run(self.export, self.vault)
        self.assertEqual(rc, 0)
        written = list(Path(self.vault).rglob("*.md"))
        self.assertEqual(len(written), 4)  # 5 notes, image-only skipped
        state = os.path.join(self.vault, ".state", "getnote-import-state.yaml")
        self.assertTrue(os.path.exists(state))

        # Second run: everything already imported → nothing new written.
        gi.run(self.export, self.vault)
        self.assertEqual(len(list(Path(self.vault).rglob("*.md"))), 4)

    def test_dry_run_writes_nothing(self):
        gi.run(self.export, self.vault, dry_run=True)
        self.assertEqual(list(Path(self.vault).rglob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
