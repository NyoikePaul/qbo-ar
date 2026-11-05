import pytest

from qbd_ar.qbxml.codec import QBXMLError, parse_responses

BOMB = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "lol">'
    '<!ENTITY b "&a;&a;&a;&a;">]>'
    "<QBXML><QBXMLMsgsRs>&b;</QBXMLMsgsRs></QBXML>"
)


def test_parse_responses_rejects_entity_declarations():
    with pytest.raises(QBXMLError):
        parse_responses(BOMB)
