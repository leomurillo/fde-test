from app.markdown import inline, plain_excerpt, render


def test_escapes_html():
    out = render('<script>alert(1)</script> and <img src=x onerror=alert(1)>')
    assert "<script>" not in out and "<img" not in out and "&lt;script&gt;" in out


def test_links_only_http():
    assert 'href="https://example.com/a?b=1&amp;c=2"' in inline("[x](https://example.com/a?b=1&c=2)")
    assert "href" not in inline("[x](javascript:alert(1))")
    assert 'href="https://example.com"' in inline("see https://example.com.")


def test_blocks():
    md = "Intro **bold** and `run_id`, snake_case_name.\n\n- one\n- two\n\n```sql\nSELECT 1 < 2\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    out = render(md)
    assert "<strong>bold</strong>" in out and "<code>run_id</code>" in out and "snake_case_name" in out
    assert "<ul><li>one</li><li>two</li></ul>" in out
    assert '<pre><code class="lang-sql">SELECT 1 &lt; 2</code></pre>' in out
    assert "<table>" in out and "<td>1</td>" in out


def test_excerpt():
    assert plain_excerpt("**Hello** `x`", 100) == "Hello x"
    assert plain_excerpt("a" * 300, 10).endswith("…")
