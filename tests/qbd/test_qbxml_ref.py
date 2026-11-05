import xml.etree.ElementTree as ET

import pytest

from qbd_ar.qbxml.codec import QBXMLError, _append_ref


def test_ref_requires_exactly_one_identifier():
    parent = ET.Element("Req")
    with pytest.raises(QBXMLError):
        _append_ref(parent, "CustomerRef")
    with pytest.raises(QBXMLError):
        _append_ref(parent, "CustomerRef", list_id="80000001-1", full_name="Acme")


def test_ref_uses_list_id_or_full_name():
    parent = ET.Element("Req")
    assert _append_ref(parent, "CustomerRef", list_id="80000001-1")[0].tag == "ListID"
    assert _append_ref(parent, "CustomerRef", full_name="Acme")[0].tag == "FullName"


def test_ref_rejects_blank_value():
    with pytest.raises(QBXMLError):
        _append_ref(ET.Element("Req"), "CustomerRef", full_name="   ")
