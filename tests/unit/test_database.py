"""Test DynamoDB"""
import pytest
from therapy.database import Database
from tests.conftest import TEST_ROOT
import json
import os


@pytest.fixture(scope='module')
def db():
    """Create a DynamoDB test fixture."""

    class DB:
        def __init__(self):
            self.db = Database(db_url=os.environ['THERAPY_NORM_DB_URL'])
            if os.environ.get('TEST') is not None:
                self.load_test_data()

        def load_test_data(self):
            with open(f'{TEST_ROOT}/tests/unit/'
                      f'data/therapies.json', 'r') as f:
                therapies = json.load(f)
                with self.db.therapies.batch_writer() as batch:
                    for therapy in therapies:
                        batch.put_item(Item=therapy)
                f.close()

            with open(f'{TEST_ROOT}/tests/unit/'
                      f'data/metadata.json', 'r') as f:
                metadata = json.load(f)
                with self.db.metadata.batch_writer() as batch:
                    for m in metadata:
                        batch.put_item(Item=m)
                f.close()

    return DB().db


def test_tables_created(db):
    """Check that therapy_concepts and therapy_metadata are created."""
    existing_tables = db.dynamodb_client.list_tables()['TableNames']
    assert 'therapy_concepts' in existing_tables
    assert 'therapy_metadata' in existing_tables
