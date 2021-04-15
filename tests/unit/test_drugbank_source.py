"""Test that the therapy normalizer works as intended for the DrugBank
source.
"""
import pytest

from tests.conftest import compare_response
from therapy.query import QueryHandler
from therapy.schemas import Drug, MatchType
import re


@pytest.fixture(scope='module')
def drugbank():
    """Build DrugBank normalizer test fixture."""
    class QueryGetter:

        def __init__(self):
            self.query_handler = QueryHandler()

        def search(self, query_str):
            resp = self.query_handler.search_sources(query_str, keyed=True,
                                                     incl='drugbank')
            return resp['source_matches']['DrugBank']

        def fetch_meta(self):
            return self.query_handler._fetch_meta('DrugBank')

    return QueryGetter()


@pytest.fixture(scope='module')
def cisplatin():
    """Create a cisplatin drug fixture."""
    params = {
        'label': 'Cisplatin',
        'concept_id': 'drugbank:DB00515',
        'aliases': [
            'CDDP',
            'Cis-DDP',
            'cis-diamminedichloroplatinum(II)',
            'Cisplatin',
            'APRD00359',
            'cisplatino'
        ],
        'other_identifiers': [
            'chemidplus:15663-27-1',
        ],
        'trade_names': [],
    }
    return Drug(**params)


@pytest.fixture(scope='module')
def bentiromide():
    """Create a bentiromide drug fixture."""
    params = {
        'label': 'Bentiromide',
        'concept_id': 'drugbank:DB00522',
        'aliases': [
            'APRD00818',
            'Bentiromide',
            'BTPABA',
            'PFT',
            'PFD'
        ],
        'other_identifiers': [
            'chemidplus:37106-97-1',
        ],
        'trade_names': [],
    }
    return Drug(**params)


# Tests filtering on aliases and trade_names length
@pytest.fixture(scope='module')
def db14201():
    """Create a db14201 drug fixture."""
    params = {
        'label': '2,2\'-Dibenzothiazyl disulfide',
        'concept_id': 'drugbank:DB14201',
        'aliases': [],
        'approval_status': 'approved',
        'other_identifiers': [
            'chemidplus:120-78-5',
        ],
        'trade_names': [],
    }
    return Drug(**params)


def test_concept_id_match(drugbank, cisplatin, bentiromide, db14201):
    """Test that concept ID query resolves to correct record."""
    response = drugbank.search('drugbank:DB00515')
    compare_response(response, cisplatin, MatchType.CONCEPT_ID)

    response = drugbank.search('DB00515')
    compare_response(response, cisplatin, MatchType.CONCEPT_ID)

    response = drugbank.search('drugbank:db00515')
    compare_response(response, cisplatin, MatchType.CONCEPT_ID)

    response = drugbank.search('Drugbank:db00515')
    compare_response(response, cisplatin, MatchType.CONCEPT_ID)

    response = drugbank.search('druGBank:DB00515')
    compare_response(response, cisplatin, MatchType.CONCEPT_ID)

    response = drugbank.search('drugbank:DB00522')
    compare_response(response, bentiromide, MatchType.CONCEPT_ID)

    response = drugbank.search('DB00522')
    compare_response(response, bentiromide, MatchType.CONCEPT_ID)

    response = drugbank.search('drugbank:db00522')
    compare_response(response, bentiromide, MatchType.CONCEPT_ID)

    response = drugbank.search('Drugbank:db00522')
    compare_response(response, bentiromide, MatchType.CONCEPT_ID)

    response = drugbank.search('druGBank:DB00522')
    compare_response(response, bentiromide, MatchType.CONCEPT_ID)

    response = drugbank.search('drugbank:DB14201')
    compare_response(response, db14201, MatchType.CONCEPT_ID)

    response = drugbank.search('DB14201')
    compare_response(response, db14201, MatchType.CONCEPT_ID)

    response = drugbank.search('drugbank:db14201')
    compare_response(response, db14201, MatchType.CONCEPT_ID)

    response = drugbank.search('Drugbank:db14201')
    compare_response(response, db14201, MatchType.CONCEPT_ID)

    response = drugbank.search('druGBank:DB14201')
    compare_response(response, db14201, MatchType.CONCEPT_ID)


def test_label_match(drugbank, cisplatin, bentiromide, db14201):
    """Test that label query resolves to correct record."""
    response = drugbank.search('cisplatin')
    compare_response(response, cisplatin, MatchType.LABEL)

    response = drugbank.search('cisplatin')
    compare_response(response, cisplatin, MatchType.LABEL)

    response = drugbank.search('Bentiromide')
    compare_response(response, bentiromide, MatchType.LABEL)

    response = drugbank.search('bentiromide')
    compare_response(response, bentiromide, MatchType.LABEL)

    response = drugbank.search("2,2'-Dibenzothiazyl disulfide")
    compare_response(response, db14201, MatchType.LABEL)

    response = drugbank.search('2,2\'-dibenzothiazyl disulfide')
    compare_response(response, db14201, MatchType.LABEL)


def test_alias_match(drugbank, cisplatin, bentiromide):
    """Test that alias query resolves to correct record."""
    response = drugbank.search('Abiplatin')
    compare_response(response, cisplatin, MatchType.ALIAS)

    response = drugbank.search('Cis-ddp')
    compare_response(response, cisplatin, MatchType.ALIAS)

    response = drugbank.search('Platidiam')
    compare_response(response, cisplatin, MatchType.ALIAS)

    response = drugbank.search('APRD00818')
    compare_response(response, bentiromide, MatchType.ALIAS)

    response = drugbank.search('pfd')
    compare_response(response, bentiromide, MatchType.ALIAS)

    response = drugbank.search('PFT')
    compare_response(response, bentiromide, MatchType.ALIAS)


def test_other_id_match(drugbank, cisplatin, bentiromide, db14201):
    """Test that other_id query resolves to correct record."""
    response = drugbank.search('chemidplus:37106-97-1')
    compare_response(response, bentiromide, MatchType.OTHER_ID)

    # TODO more


def test_no_match(drugbank):
    """Test that a term normalizes to correct drug concept as a NO match."""
    response = drugbank.search('lepirudi')
    assert response['match_type'] == MatchType.NO_MATCH
    assert len(response['records']) == 0

    # Polish alias for DB14201
    response = drugbank.search('Dwusiarczek dwubenzotiazylu')
    assert response['match_type'] == \
           MatchType.NO_MATCH

    # Test white space in between id
    response = drugbank.search('DB 00001')
    assert response['match_type'] == MatchType.NO_MATCH

    # Test empty query
    response = drugbank.search('')
    assert response['match_type'] == MatchType.NO_MATCH
    assert len(response['records']) == 0


def test_meta_info(drugbank):
    """Test that the meta field is correct."""
    response = drugbank.fetch_meta()
    assert response.data_license == 'CC0 1.0'
    assert response.data_license_url == 'https://creativecommons.org/publicdomain/zero/1.0/'  # noqa: E501
    assert re.match(r'[0-9]+\.[0-9]+\.[0-9]', response.version)
    assert response.data_url == 'https://go.drugbank.com/releases/latest#open-data'  # noqa: E501
    assert response.rdp_url == 'http://reusabledata.org/drugbank.html'  # noqa: E501
    assert response.data_license_attributes == {
        "non_commercial": False,
        "share_alike": False,
        "attribution": False
    }
