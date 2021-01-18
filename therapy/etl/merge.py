"""Create concept groups and merged records."""
from therapy.schemas import SourceName
from therapy.database import Database
from typing import Set, Dict
import logging

logger = logging.getLogger('therapy')
logger.setLevel(logging.DEBUG)


class Merge:
    """Handles record merging."""

    def __init__(self, database: Database):
        """Initialize Merge instance.

        :param Database database: db instance to use for record retrieval
            and creation.
        """
        self._database = database
        self._merged_groups = {}  # dict keying concept IDs to group Sets

    def create_merged_concepts(self, record_ids: Set[str]):
        """Create concept groups, generate merged concept records, and
        update database.

        :param Set[str] record_ids: concept identifiers to create groups of

        TODO
         * Consider moving update method calls into database object
         * Make final call on how to handle dangling IDs
         * When generating merged records, should poll other_id_set if
           merged record already found?
         * When updating existing records, how to ensure that no dangling
           records remain after an other_identifier is removed?
         * When computing groups, how to handle cases where new group
           additions are discovered in subsequent passes?
         * Order that ChemIDplus/RxNorm go relative to other sources?
        """
        raise NotImplementedError

    def _create_record_id_set(self, record_id: str,
                              observed_id_set: Set = set()) -> Set[str]:
        """Create concept ID group for an individual record ID.

        :param str record_id: concept ID for record to build group from
        :return: set of related identifiers pertaining to a common concept.
        """
        if record_id in self._merged_groups:
            return self._merged_groups[record_id]
        else:
            db_record = self._database.get_record_by_id(record_id)
            if not db_record or 'other_identifiers' not in db_record:
                logger.error(f"Could not retrieve record for {record_id}"
                             f"in ID set: {observed_id_set}")
                return observed_id_set | {record_id}

            local_id_set = {db_record['other_identifiers']}
            merged_id_set = local_id_set | observed_id_set | \
                {db_record['concept_id']}

            for local_record_id in local_id_set - observed_id_set:
                merged_id_set |= self._create_record_id_set(local_record_id,
                                                            merged_id_set)
            return merged_id_set

    def _generate_merged_record(self, record_id_set: Set) -> Dict:
        """Generate merged record from provided concept ID group.
        Where attributes are sets, they should be merged, and where they are
        scalars, assign from the highest-priority source where that attribute
        is non-null. Priority is NCIt > ChEMBL > DrugBank > Wikidata.

        :param Set record_id_set: group of concept IDs
        :return: completed merged drug object to be stored in DB
        """
        records = []
        for record_id in record_id_set:
            record = self._database.get_record_by_id(record_id)
            if record:
                records.append(record)
            else:
                logger.error(f"Could not retrieve record for {record_id}"
                             f"in {record_id_set}")

        def record_order(record_to_order):
            src = record_to_order['src_name']
            if src == SourceName.NCIT:
                return 1
            elif src == SourceName.CHEMBL:
                return 2
            elif src == SourceName.DRUGBANK:
                return 3
            else:
                return 4
        records.sort(key=record_order)

        attrs = {'aliases': set(), 'concept_id': '',
                 'trade_names': set(), 'xrefs': set()}
        set_fields = ['aliases', 'trade_names', 'xrefs']
        for record in records:
            for field in set_fields:
                if field in record:
                    attrs[field] |= set(record[field])
            new_id_grp = f'{attrs["concept_id"]}|{record["concept_id"]}'
            attrs['concept_id'] = new_id_grp
            for field in ['label', 'approval_status']:
                if field not in attrs:
                    value = record.get(field, None)
                    if value:
                        attrs[field] = value
        for field in set_fields:
            attrs[field] = list(attrs[field])

        attrs['label_and_type'] = f'{attrs["concept_id"].lower()}##merger'
        return attrs
