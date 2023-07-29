"""Pytest test config tools."""
import logging
import os
from typing import Optional, List, Callable
import json
from pathlib import Path

import pytest

from therapy.database.database import AbstractDatabase, create_db
from therapy.etl.base import Base
from therapy.query import QueryHandler
from therapy.schemas import Therapy, MatchType, MatchesKeyed
from therapy.database import AWS_ENV_VAR_NAME


_logger = logging.getLogger(__name__)
logging.getLogger("botocore").setLevel(logging.ERROR)
logging.getLogger("boto3").setLevel(logging.ERROR)
logging.getLogger("urllib").setLevel(logging.ERROR)


def pytest_collection_modifyitems(items):
    """Modify test items in place to ensure test modules run in a given order.
    When creating new test modules, be sure to add them here.
    """
    MODULE_ORDER = [
        "test_chembl",
        "test_chemidplus",
        "test_drugbank",
        "test_drugsatfda",
        "test_guidetopharmacology",
        "test_hemonc",
        "test_ncit",
        "test_rxnorm",
        "test_wikidata",
        "test_merge",
        "test_database",
        "test_query",
        "test_emit_warnings",
        "test_disease_indication"
    ]
    assert len(MODULE_ORDER) == len(list(Path(__file__).parent.rglob("test_*.py")))
    items.sort(key=lambda i: MODULE_ORDER.index(i.module.__name__))


IS_TEST_ENV = os.environ.get("THERAPY_TEST", "").lower() == "true" and AWS_ENV_VAR_NAME not in os.environ  # noqa: E501


@pytest.fixture(scope="session")
def is_test_env():
    """If true, currently in test environment (i.e. okay to overwrite DB). Downstream
    users should also make sure to check if in a production environment.
    Provided here to be accessible directly within test modules.
    """
    return IS_TEST_ENV


def pytest_sessionstart():
    """Wipe DB before testing if in test environment."""
    if IS_TEST_ENV:
        if os.environ.get(AWS_ENV_VAR_NAME):
            assert False, f"Cannot have both THERAPY_TEST and {AWS_ENV_VAR_NAME} set."
        db = create_db()
        db.drop_db()
        db.initialize_db()


TEST_DATA_DIRECTORY = Path(__file__).resolve().parents[1] / "tests" / "data"


@pytest.fixture(scope="session")
def test_data():
    """Provide test data location to test modules"""
    return TEST_DATA_DIRECTORY


@pytest.fixture(scope="module")
def database():
    """Provide a database instance to be used by tests."""
    db = create_db()
    yield db
    db.close_connection()


@pytest.fixture(scope="session")
def disease_normalizer():
    """Provide mock disease normalizer."""
    with open(TEST_DATA_DIRECTORY / "disease_normalization.json", "r") as f:
        disease_data = json.load(f)

    def _normalize_disease(query: str):
        return disease_data.get(query.lower())

    return _normalize_disease


@pytest.fixture(scope="module")
def test_source(
    database: AbstractDatabase, test_data: Path, disease_normalizer: Callable
):
    """Provide query endpoint for testing sources. If THERAPY_TEST is set, will try to
    load DB from test data.

    :param database: test database instance
    :param is_test_env: if true, load from test data
    :param disease_normalizer: mock disease normalizer instance
    :return: factory function that takes an ETL class instance and returns a query
    endpoint.
    """
    def test_source_factory(EtlClass: Base):
        if IS_TEST_ENV:
            _logger.debug(f"Reloading DB with data from {TEST_DATA_DIRECTORY}")
            test_class = EtlClass(database, test_data)  # type: ignore
            test_class._normalize_disease = disease_normalizer  # type: ignore
            test_class.perform_etl(use_existing=True)
            test_class._database.complete_write_transaction()

        class QueryGetter:

            def __init__(self):
                self.query_handler = QueryHandler(database)
                self._src_name = EtlClass.__name__  # type: ignore

            def search(self, query_str: str):
                resp = self.query_handler.search(
                    query_str, keyed=True, incl=self._src_name
                )
                return resp.source_matches[self._src_name]

        return QueryGetter()

    return test_source_factory


def _compare_records(actual: Therapy, fixt: Therapy):
    """Check that identity records are identical."""
    assert actual.concept_id == fixt.concept_id
    assert actual.label == fixt.label

    assert (actual.aliases is None) == (fixt.aliases is None)
    if (actual.aliases is not None) and (fixt.aliases is not None):
        assert set(actual.aliases) == set(fixt.aliases)

    assert (actual.trade_names is None) == (fixt.trade_names is None)
    if (actual.trade_names is not None) and (fixt.trade_names is not None):
        assert set(actual.trade_names) == set(fixt.trade_names)

    assert (actual.xrefs is None) == (fixt.xrefs is None)
    if (actual.xrefs is not None) and (fixt.xrefs is not None):
        assert set(actual.xrefs) == set(fixt.xrefs)

    assert (actual.associated_with is None) == (fixt.associated_with is None)
    if (actual.associated_with is not None) and (fixt.associated_with is not None):
        assert set(actual.associated_with) == set(fixt.associated_with)

    assert (not actual.approval_ratings) == (not fixt.approval_ratings)
    if (actual.approval_ratings) and (fixt.approval_ratings):
        assert set(actual.approval_ratings) == set(fixt.approval_ratings)

    assert (actual.approval_year is None) == (fixt.approval_year is None)
    if (actual.approval_year is not None) and (fixt.approval_year is not None):
        assert set(actual.approval_year) == set(fixt.approval_year)

    assert (actual.has_indication is None) == (fixt.has_indication is None)
    if (actual.has_indication is not None) and (fixt.has_indication is not None):
        actual_inds = actual.has_indication.copy()
        fixture_inds = fixt.has_indication.copy()
        assert len(actual_inds) == len(fixture_inds)
        actual_inds.sort(key=lambda x: x.disease_id)
        fixture_inds.sort(key=lambda x: x.disease_id)
        for i in range(len(actual_inds)):
            assert actual_inds[i] == fixture_inds[i]


@pytest.fixture(scope="session")
def compare_records():
    """Provide record comparison function"""
    return _compare_records


def _compare_response(
    response: MatchesKeyed, match_type: MatchType, fixture: Optional[Therapy] = None,
    fixture_list: Optional[List[Therapy]] = None, num_records: int = 0
):
    """Check that test response is correct. Only 1 of {fixture, fixture_list}
    should be passed as arguments. num_records should only be passed with fixture_list.

    :param Dict response: response object returned by QueryHandler
    :param MatchType match_type: expected match type
    :param Therapy fixture: single therapy object to match response against
    :param List[Therapy] fixture_list: multiple therapy objects to match response
        against
    :param int num_records: expected number of records in response. If not given, tests
        for number of fixture therapies given (ie, 1 for single fixture and length of
        fixture_list otherwise)
    """
    if fixture and fixture_list:
        raise Exception("Args provided for both `fixture` and `fixture_list`")
    elif not fixture and not fixture_list:
        raise Exception("Must pass 1 of {fixture, fixture_list}")
    if fixture and num_records:
        raise Exception("`num_records` should only be given with "
                        "`fixture_list`.")

    assert response.match_type == match_type
    if fixture:
        assert len(response.records) == 1
        _compare_records(response.records[0], fixture)
    elif fixture_list:
        if not num_records:
            assert len(response.records) == len(fixture_list)
        else:
            assert len(response.records) == num_records
        for fixt in fixture_list:
            for record in response.records:
                if fixt.concept_id == record.concept_id:
                    _compare_records(record, fixt)
                    break
            else:
                assert False  # test fixture not found in response


@pytest.fixture(scope="session")
def compare_response():
    """Provide response comparison function"""
    return _compare_response
