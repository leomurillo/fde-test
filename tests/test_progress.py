import yaml

from tests.conftest import FIXTURES


def test_item_browser_paginates_50_per_page(make_client, tmp_path):
    base = yaml.safe_load((FIXTURES / "D06-sample.yaml").read_text())["items"][0]
    # ids above the real bank's, so bank/lock.json does not reject them as edited locked versions
    items = [{**base, "id": f"D6-{i:03d}", "tags": []} for i in range(501, 621)]
    bank = tmp_path / "bigbank"
    bank.mkdir()
    (bank / "D06-big.yaml").write_text(yaml.safe_dump({"domain": "D6", "items": items}, sort_keys=False))
    client = make_client(bank_dir=bank)
    assert client.app.state.ctx.sync_report.loaded == 120
    page = client.get("/progress")
    assert "D6-501" not in page.text and ">120<" in page.text
    p1 = client.get("/progress/items?domain=D6").text
    assert p1.count('class="card browser-item"') == 50 and "Page 1 of 3" in p1 and "D6-550" in p1
    p3 = client.get("/progress/items?domain=D6&page_no=3").text
    assert p3.count('class="card browser-item"') == 20 and "D6-620" in p3 and "D6-550" not in p3
    assert "Page 3 of 3" in client.get("/progress/items?domain=D6&page_no=99").text  # clamped
    full = client.get("/progress?domain=D6&page_no=2").text
    assert full.count('class="card browser-item"') == 50 and "D6-551" in full
    assert client.get("/progress/items?domain=D1").text.count('class="card browser-item"') == 0
    assert client.get("/progress?status=bogus&type=bogus").status_code == 200
