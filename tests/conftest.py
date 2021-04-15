"""Pytest test config tools."""
from therapy.schemas import Drug, MatchType
from therapy.database import Database
from typing import Dict, Any, Optional, List
import json
import pytest
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def mock_database():
    """Return MockDatabase object."""

    class MockDatabase(Database):
        """Mock database object to use in test cases."""

        def __init__(self):
            """Initialize mock database object. This class's method's shadow the
            actual Database class methods.

            `self.records` loads preexisting DB items.
            `self.added_records` stores add record requests, with the
            concept_id as the key and the complete record as the value.
            `self.updates` stores update requests, with the concept_id as the
            key and the updated attribute and new value as the value.
            """
            infile = TEST_ROOT / 'tests' / 'unit' / 'data' / 'therapies.json'
            self.records = {}
            with open(infile, 'r') as f:
                records_json = json.load(f)
            for record in records_json:
                label_and_type = record['label_and_type']
                concept_id = record['concept_id']
                if self.records.get(label_and_type):
                    self.records[label_and_type][concept_id] = record
                else:
                    self.records[label_and_type] = {concept_id: record}
            self.added_records: Dict[str, Dict[Any, Any]] = {}
            self.updates: Dict[str, Dict[Any, Any]] = {}

            meta = TEST_ROOT / 'tests' / 'unit' / 'data' / 'metadata.json'
            with open(meta, 'r') as f:
                meta_json = json.load(f)
            self.cached_sources = {}
            for src in meta_json:
                name = src['src_name']
                self.cached_sources[name] = src
                del self.cached_sources[name]['src_name']

        def get_record_by_id(self, record_id: str,
                             case_sensitive: bool = True,
                             merge: bool = False) -> Optional[Dict]:
            """Fetch record corresponding to provided concept ID.

            :param str record_id: concept ID for therapy record
            :param bool case_sensitive: if true, performs exact lookup, which
                is more efficient. Otherwise, performs filter operation, which
                doesn't require correct casing.
            :param bool merge: if true, retrieve merged record
            :return: complete therapy record, if match is found; None otherwise
            """
            if merge:
                label_and_type = f'{record_id.lower()}##merger'
                record_lookup = self.records.get(label_and_type)
                if record_lookup:
                    return list(record_lookup.values())[0].copy()
                else:
                    return None
            else:
                label_and_type = f'{record_id.lower()}##identity'
            record_lookup = self.records.get(label_and_type, None)
            if record_lookup:
                if case_sensitive:
                    record = record_lookup.get(record_id, None)
                    if record:
                        return record.copy()
                    else:
                        return None
                elif record_lookup.values():
                    return list(record_lookup.values())[0].copy()
            return None

        def get_records_by_type(self, query: str,
                                match_type: str) -> List[Dict]:
            """Retrieve records for given query and match type.

            :param query: string to match against
            :param str match_type: type of match to look for. Should be one
                of {"alias", "trade_name", "label", "rx_brand", "other_id"}
                (use get_record_by_id for concept ID lookup)
            :return: list of matching records. Empty if lookup fails.
            """
            assert match_type in ('alias', 'trade_name', 'label', 'rx_brand',
                                  'other_id')
            label_and_type = f'{query}##{match_type.lower()}'
            records_lookup = self.records.get(label_and_type, None)
            if records_lookup:
                return [v.copy() for v in records_lookup.values()]
            else:
                return []

        def add_record(self, record: Dict, record_type: str):
            """Store add record request sent to database.

            :param Dict record: record (of any type) to upload. Must include
                `concept_id` key. If record is of the `identity` type, the
                concept_id must be correctly-cased.
            :param str record_type: ignored by this function
            """
            self.added_records[record['concept_id']] = record

        def update_record(self, concept_id: str, attribute: str,
                          new_value: Any):
            """Store update request sent to database.

            :param str concept_id: record to update
            :param str attribute: name of field to update
            :param str new_value: new value
            """
            assert f'{concept_id.lower()}##identity' in self.records
            self.updates[concept_id] = {attribute: new_value}

    return MockDatabase


def compare_records(actual: Drug, fixture: Drug):
    """Check that identity records are identical."""
    assert actual.concept_id == fixture.concept_id
    assert actual.label == fixture.label
    assert set(actual.aliases) == set(fixture.aliases)
    assert set(actual.trade_names) == set(fixture.trade_names)
    assert actual.approval_status == fixture.approval_status
    assert set(actual.other_identifiers) == set(fixture.other_identifiers)
    assert set(actual.xrefs) == set(fixture.xrefs)


def compare_response(response: Dict, fixture: Drug, match_type: MatchType):
    """Check that test response is correct."""
    assert response['match_type'] == match_type
    assert len(response['records']) == 1
    compare_records(response['records'][0], fixture)
