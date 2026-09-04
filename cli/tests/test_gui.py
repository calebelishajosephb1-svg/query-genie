"""Smoke tests for the UNSQL GUI layer."""
import json
import urllib.request

from unsql.gui import GUIVisualizer
from unsql.gui import web


def test_push_get_history(tmp_path):
    viz = GUIVisualizer()
    viz.push_result(["id", "name"], [(1, "a"), (2, "=2+2")], sql="select *", title="t")
    last = viz.get_last()
    assert last["columns"] == ["id", "name"]
    assert len(last["rows"]) == 2
    assert len(viz.history()) >= 1

    csv_path = viz.export_csv(tmp_path / "out.csv")
    assert "'=2+2" in csv_path.read_text()

    json_path = viz.export_json(tmp_path / "out.json")
    assert json.loads(json_path.read_text())[0]["name"] == "a"


def test_sanitize():
    assert web._sanitize("=1+1") == "'=1+1"
    assert web._sanitize("  @cmd") == "'  @cmd"
    assert web._sanitize("plain") == "plain"
    assert web._sanitize(None) == ""


def test_server_lifecycle():
    url = web.start_server(port=8899, columns=["a"], rows=[(1,)], sql="select 1")
    try:
        assert web.start_server(port=8899) == url  # hot swap, same URL
        web.update_data(["a"], [(1,), (2,)], "select 1")
        body = urllib.request.urlopen(url + "api/data", timeout=5).read().decode()
        assert len(json.loads(body)["rows"]) == 2
        html = urllib.request.urlopen(url, timeout=5).read().decode()
        assert "<table>" in html
        csv_body = urllib.request.urlopen(url + "api/csv", timeout=5).read().decode()
        assert "a" in csv_body
    finally:
        web.stop_server()
