"""The visual detector parses Gemini's annotation JSON and maps normalized
0-1000 coordinates back to PDF points. The vision call needs a key/network, so
here we test the parser + coordinate mapping + counts (deterministic)."""
from backend import visual_detect as vd


def test_parses_and_maps_coords_to_points():
    reply = """```json
    [
      {"type":"material","code":"M.5","point":[500,250]},
      {"type":"tree","code":"","point":[0,0]},
      {"type":"pool","code":"","point":[1000,1000]},
      {"type":"junk","code":"x","point":[10,10]},
      {"type":"material","code":"bad","point":[1]}
    ]
    ```"""
    anns = vd.parse_annotations(reply, width=800.0, height=600.0)
    # junk type and malformed point dropped
    assert [a["type"] for a in anns] == ["material", "tree", "pool"]
    # M.5 at normalized y=500,x=250 -> pt [x,y] = [0.25*800, 0.5*600] = [200, 300]
    m5 = anns[0]
    assert abs(m5["pt"][0] - 200) < 0.01 and abs(m5["pt"][1] - 300) < 0.01
    # corners map correctly
    assert anns[1]["pt"] == [0.0, 0.0]
    assert abs(anns[2]["pt"][0] - 800) < 0.01 and abs(anns[2]["pt"][1] - 600) < 0.01


def test_counts_only_count_types():
    anns = [
        {"type": "material", "code": "M.5"},
        {"type": "tree", "code": ""}, {"type": "tree", "code": ""},
        {"type": "pool", "code": ""}, {"type": "spa", "code": ""},
    ]
    assert vd.counts(anns) == {"tree": 2, "pool": 1, "spa": 1}


def test_empty_or_unparseable_reply():
    assert vd.parse_annotations("no json here", 100, 100) == []
    assert vd.parse_annotations("", 100, 100) == []
