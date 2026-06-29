# tests/test_weblookup.py
from unittest.mock import MagicMock, patch

import pytest

import cfs


SAMPLE_HTML = """
<html><body>
<div class="filament-profile">
  <span class="brand">Sunlu</span>
  <span class="name">PLA+</span>
  <span class="type">PLA</span>
  <span class="density">1.23</span>
  <span class="min-temp">205</span>
  <span class="max-temp">215</span>
  <span class="bed-temp">60</span>
  <span class="flow-ratio">0.998</span>
  <span class="pressure-advance">0.032</span>
  <span class="drying-temp">50</span>
  <span class="drying-time">8</span>
</div>
</body></html>
"""


def test_weblookup_found():
    resp = MagicMock()
    resp.text = SAMPLE_HTML
    resp.status_code = 200
    with patch("cfs.requests.get", return_value=resp):
        result = cfs.lookup_filament("Sunlu", "PLA+")
    assert result is not None
    assert result["brand"] == "Sunlu"
    assert result["name"] == "PLA+"
    assert result["type"] == "PLA"
    assert result["density"] == 1.23
    assert result["minTemp"] == 205
    assert result["maxTemp"] == 215
    assert result["flowRatio"] == 0.998
    assert result["pa"] == 0.032
    assert result["dryingTemp"] == 50
    assert result["dryingTime"] == 8


def test_weblookup_not_found():
    resp = MagicMock()
    resp.text = "<html>404 not found</html>"
    resp.status_code = 404
    with patch("cfs.requests.get", return_value=resp):
        with pytest.raises(SystemExit) as exc:
            cfs.lookup_filament("ObscureBrand", "XYZ")
        assert exc.value.code == cfs.EXIT_WEBLOOKUP


def test_weblookup_connection_fail():
    with patch("cfs.requests.get", side_effect=Exception("network error")):
        with pytest.raises(SystemExit) as exc:
            cfs.lookup_filament("Sunlu", "PLA+")
        assert exc.value.code == cfs.EXIT_WEBLOOKUP


def test_weblookup_parse_fail():
    resp = MagicMock()
    resp.text = "<html>no profile here</html>"
    resp.status_code = 200
    with patch("cfs.requests.get", return_value=resp):
        with pytest.raises(SystemExit) as exc:
            cfs.lookup_filament("Sunlu", "PLA+")
        assert exc.value.code == cfs.EXIT_WEBLOOKUP
