"""A base class for extraction, transformation, and loading of data."""
from abc import ABC, abstractmethod
import ftplib
from pathlib import Path
import logging
from typing import List, Dict, Optional, Callable
import os
import zipfile
import tempfile
import re
import json
from functools import lru_cache

from pydantic import ValidationError
import requests
import bioversions
from disease.query import QueryHandler as DiseaseNormalizer

from therapy import APP_ROOT, ITEM_TYPES, DownloadException
from therapy.schemas import Drug, SourceName
from therapy.database import Database
from therapy.etl.rules import Rules


logger = logging.getLogger("therapy")
logger.setLevel(logging.DEBUG)

DEFAULT_DATA_PATH: Path = APP_ROOT / "data"


class Base(ABC):
    """The ETL base class.

    Default methods are declared to provide basic functions for core source
    data-gathering phases (extraction, transformation, loading), as well
    as some common subtasks (getting most recent version, downloading data
    from an FTP server). Classes should expand or reimplement these methods as
    needed.
    """

    def __init__(self, database: Database, data_path: Path = DEFAULT_DATA_PATH) -> None:
        """Extract from sources.

        :param Database database: application database object
        :param Path data_path: path to app data directory
        """
        self.name = self.__class__.__name__
        self.database = database
        self._src_dir: Path = Path(data_path / self.name.lower())
        self._added_ids: List[str] = []
        self._set_existing_ids()
        self._rules = Rules(SourceName(self.name))

    def _set_existing_ids(self) -> None:
        """Assign all existing concept IDs to `self._existing_ids`"""
        last_evaluated_key = None
        concept_ids = []
        params = {
            "ProjectionExpression": "concept_id,item_type,src_name",
        }
        while True:
            if last_evaluated_key:
                response = self.database.therapies.scan(
                    ExclusiveStartKey=last_evaluated_key, **params
                )
            else:
                response = self.database.therapies.scan(**params)
            records = response["Items"]
            for record in records:
                if (
                    record["item_type"] == "identity"
                    and record["src_name"] == self.name  # noqa: W503
                ):
                    concept_ids.append(record["concept_id"])
            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break
        self._existing_ids = set(concept_ids)

    def perform_etl(self, use_existing: bool = False) -> List[str]:
        """Public-facing method to begin ETL procedures on given data.
        Returned concept IDs can be passed to Merge method for computing
        merged concepts.

        :param bool use_existing: if True, don't try to retrieve latest source data
        :return: list of concept IDs which were successfully processed and
            uploaded.
        """
        self._extract_data(use_existing)
        self._load_meta()
        self._transform_data()
        for old_concept in self._existing_ids:
            self.database.delete_record(f"{old_concept.lower()}##identity", old_concept)
        return self._added_ids

    def get_latest_version(self) -> str:
        """Get most recent version of source data. Should be overriden by
        sources not added to Bioversions yet, or other special-case sources.
        :return: most recent version, as a str
        """
        return bioversions.get_version(self.__class__.__name__)

    @abstractmethod
    def _download_data(self) -> None:
        """Acquire source data and deposit in a usable form with correct file
        naming conventions (generally, `<source>_<version>.<filetype>`, or
        `<source>_<subset>_<version>.<filetype>` if sources require multiple
        files). Shouldn't set any instance attributes.
        """
        raise NotImplementedError

    def _zip_handler(self, dl_path: Path, outfile_path: Path) -> None:
        """Provide simple callback function to extract the largest file within a given
        zipfile and save it within the appropriate data directory.
        :param Path dl_path: path to temp data file
        :param Path outfile_path: path to save file within
        """
        with zipfile.ZipFile(dl_path, "r") as zip_ref:
            if len(zip_ref.filelist) > 1:
                files = sorted(
                    zip_ref.filelist, key=lambda z: z.file_size, reverse=True
                )
                target = files[0]
            else:
                target = zip_ref.filelist[0]
            target.filename = outfile_path.name
            zip_ref.extract(target, path=outfile_path.parent)
        os.remove(dl_path)

    @staticmethod
    def _http_download(
        url: str,
        outfile_path: Path,
        headers: Optional[Dict] = None,
        handler: Optional[Callable[[Path, Path], None]] = None,
    ) -> None:
        """Perform HTTP download of remote data file.
        :param str url: URL to retrieve file from
        :param Path outfile_path: path to where file should be saved. Must be an actual
            Path instance rather than merely a pathlike string.
        :param Optional[Dict] headers: Any needed HTTP headers to include in request
        :param Optional[Callable[[Path, Path], None]] handler: provide if downloaded
            file requires additional action, e.g. it's a zip file.
        """
        if handler:
            dl_path = Path(tempfile.gettempdir()) / "therapy_dl_tmp"
        else:
            dl_path = outfile_path
        # use stream to avoid saving download completely to memory
        with requests.get(url, stream=True, headers=headers) as r:
            try:
                r.raise_for_status()
            except requests.HTTPError:
                raise DownloadException(
                    f"Failed to download {outfile_path.name} from {url}."
                )
            with open(dl_path, "wb") as h:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        h.write(chunk)
        if handler:
            handler(dl_path, outfile_path)

    def _ftp_download(self, host: str, host_dir: str, host_fn: str) -> None:
        """Download data file from FTP site.
        :param str host: Source's FTP host name
        :param str host_dir: Data directory located on FTP site
        :param str host_fn: Filename on FTP site to be downloaded
        """
        try:
            with ftplib.FTP(host) as ftp:
                ftp.login()
                logger.debug(f"FTP login to {host} was successful")
                ftp.cwd(host_dir)
                with open(self._src_dir / host_fn, "wb") as fp:
                    ftp.retrbinary(f"RETR {host_fn}", fp.write)
        except ftplib.all_errors as e:
            logger.error(f"FTP download failed: {e}")
            raise Exception(e)

    def _parse_version(
        self, file_path: Path, pattern: Optional[re.Pattern] = None
    ) -> str:
        """Get version number from provided file path.

        :param Path file_path: path to located source data file
        :param Optional[re.Pattern] pattern: regex pattern to use
        :return: source data version
        :raises: FileNotFoundError if version parsing fails
        """
        if pattern is None:
            pattern = re.compile(type(self).__name__.lower() + r"_(.+)\..+")
        matches = re.match(pattern, file_path.name)
        if matches is None:
            raise FileNotFoundError
        else:
            return matches.groups()[0]

    def _extract_data(self, use_existing: bool = False) -> None:
        """Get source file from data directory.

        This method should ensure the source data directory exists, acquire source data,
        set the source version value, and assign the source file location to
        `self._src_file`. Child classes needing additional functionality (like setting
        up a DB cursor, or managing multiple source files) will need to reimplement
        this method. If `use_existing` is True, the version number will be parsed from
        the existing filename; otherwise, it will be retrieved from the data source,
        and if the local file is out-of-date, the newest version will be acquired.

        :param bool use_existing: if True, don't try to fetch latest source data
        """
        self._src_dir.mkdir(exist_ok=True, parents=True)
        src_name = type(self).__name__.lower()
        if use_existing:
            files = list(sorted(self._src_dir.glob(f"{src_name}_*.*")))
            if len(files) < 1:
                raise FileNotFoundError(f"No source data found for {src_name}")
            self._src_file: Path = files[-1]
            try:
                self._version = self._parse_version(self._src_file)
            except FileNotFoundError:
                raise FileNotFoundError(
                    f"Unable to parse version value from {src_name} source data file "
                    f"located at {self._src_file.absolute().as_uri()} -- "
                    "check filename against schema defined in README: "
                    "https://github.com/cancervariants/therapy-normalization#update-sources"  # noqa: E501
                )
        else:
            self._version = self.get_latest_version()
            fglob = f"{src_name}_{self._version}.*"
            latest = list(self._src_dir.glob(fglob))
            if not latest:
                self._download_data()
                latest = list(self._src_dir.glob(fglob))
            assert len(latest) != 0  # probably unnecessary, but just to be safe
            self._src_file = latest[0]

    @abstractmethod
    def _load_meta(self) -> None:
        """Load source metadata entry."""
        raise NotImplementedError

    @abstractmethod
    def _transform_data(self) -> None:
        """Prepare source data for loading into DB. Individually extract each
        record and call the Base class's `_load_therapy()` method.
        """
        raise NotImplementedError

    def _load_completed_therapy(self, therapy: Dict) -> None:
        """Load finalized therapy object into DB
        :param therapy: processed therapy object
        """
        self.database.add_record(therapy)
        concept_id = therapy["concept_id"]

        for attr_type, item_type in ITEM_TYPES.items():
            value = therapy.get(attr_type)
            if value:
                if attr_type in therapy:
                    if attr_type == "label":
                        self.database.add_ref_record(value, concept_id, item_type)
                    else:
                        for item in {v.lower() for v in value}:
                            self.database.add_ref_record(item, concept_id, item_type)

    def _new_load_therapy(self, therapy: Dict) -> None:
        """
        TODO
        * how to update merge refs?
        * nightmare: how to handle rxnorm deprecated names?
        * pkeys are lower-cased. When diffing, don't create new refs if it's just a
            casing change.
        """
        concept_id = therapy["concept_id"]
        if concept_id not in self._existing_ids:
            self._load_completed_therapy(therapy)
        else:
            remove_expressions = []
            update_expressions = []
            update_values = {}
            existing = self.database.get_record_by_id(concept_id, True)
            if existing is None:
                raise Exception  # TODO shouldn't be possible

            # queue updates
            label = therapy.get("label")
            existing_label = existing.get("label")
            if label != existing_label:
                if label and existing_label:
                    self.database.batch.delete_item(
                        Key={
                            "label_and_type": f"{existing_label.lower()}##label",
                            "concept_id": concept_id.lower(),
                        }
                    )
                    update_expressions.append("label=:a")
                    update_values["a"] = label
                    self.database.add_ref_record(label.lower(), concept_id, "label")
                elif label:
                    update_expressions.append("label=:a")
                    update_values["a"] = label
                    self.database.add_ref_record(label.lower(), concept_id, "label")
                elif existing_label:
                    remove_expressions.append("label")
                    self.database.batch.delete_item(
                        Key={
                            "label_and_type": f"{existing_label.lower()}##label",
                            "concept_id": concept_id.lower(),
                        }
                    )

            for item_type, property_name, ref in [
                ("alias", "aliases", ":b"),
                ("trade_name", "trade_names", ":c"),
                ("xref", "xrefs", ":d"),
                ("associated_with", "associated_with", ":e"),
            ]:
                new_field = set(therapy.get(property_name, []))
                existing_field = set(existing.get(property_name, []))
                if new_field != existing_field:
                    if new_field:
                        update_expressions.append(f"{property_name}={ref}")
                        update_values[ref] = list(existing_field)
                    elif existing_field:
                        remove_expressions.append(property_name)
                    for new_ref in new_field - existing_field:
                        self.database.add_ref_record(new_ref, concept_id, item_type)
                    for old_ref in existing_field - new_field:
                        self.database.batch.delete_item(
                            Key={
                                "label_and_type": f"{old_ref.lower()}##{item_type}",
                                "concept_id": concept_id.lower(),
                            }
                        )
                    if item_type == "xref":
                        remove_expressions.append("merge_ref")

            for prop_type, symbol in (
                ("has_indication", ":g"),
                ("approval_ratings", ":h"),
                ("approval_year", ":i"),
            ):
                new_prop = set(therapy.get(prop_type, []))
                existing_prop = set(existing.get(prop_type, []))
                if new_prop != existing_prop:
                    if not existing_prop:
                        remove_expressions.append(prop_type)
                    else:
                        update_expressions.append(f"{prop_type}={symbol}")
                        update_values[symbol] = list(new_prop)

            if remove_expressions:
                self.database.therapies.update_item(
                    Key={
                        "label_and_type": f"{existing['concept_id'].lower()}##identity",
                        "concept_id": concept_id,
                    },
                    UpdateExpression=f"REMOVE {', '.join(remove_expressions)}",
                )
            if update_expressions:
                self.database.therapies.update_item(
                    Key={
                        "label_and_type": f"{existing['concept_id'].lower()}##identity",
                        "concept_id": concept_id,
                    },
                    UpdateExpression=f"SET {', '.join(update_expressions)}",
                    ExpressionAttributeValues=update_values,
                )

    def _prepare_therapy(self, raw_therapy: Dict) -> Dict:
        """Construct ready-to-upload therapy object. Removes redundant property
        entries, adds DB-specific fields like `src_name` and `label_and_type`, etc.
        :param raw_therapy: therapy as built by source importer.
        :return: DB-ready therapy
        """
        therapy = self._rules.apply_rules_to_therapy(raw_therapy)
        try:
            Drug(**therapy)
        except ValidationError as e:
            logger.error(f"Attempted to load invalid therapy: {therapy}")
            raise e

        concept_id = therapy["concept_id"]

        for attr_type in ITEM_TYPES.keys():
            if attr_type in therapy:
                value = therapy[attr_type]
                if value is None or value == []:
                    del therapy[attr_type]
                    continue

                if attr_type == "label":
                    value = value.strip()
                    therapy["label"] = value
                    continue

                value_set = {v.strip() for v in value}

                # clean up listlike symbol fields
                if attr_type == "aliases" and "trade_names" in therapy:
                    value = list(value_set - set(therapy["trade_names"]))
                else:
                    value = list(value_set)

                if (
                    attr_type == "aliases" or attr_type == "trade_names"
                ) and "label" in therapy:
                    try:
                        value.remove(therapy["label"])
                    except ValueError:
                        pass

                if len(value) > 20:
                    logger.debug(f"{concept_id} has > 20 {attr_type}.")
                    del therapy[attr_type]
                    continue

                therapy[attr_type] = value

        # compress has_indication
        indications = therapy.get("has_indication")
        if indications:
            therapy["has_indication"] = list(
                {
                    json.dumps(
                        [
                            ind["disease_id"],
                            ind["disease_label"],
                            ind.get("normalized_disease_id"),
                            ind.get("supplemental_info"),
                        ]
                    )
                    for ind in indications
                }
            )
        elif "has_indication" in therapy:
            del therapy["has_indication"]

        # handle detail fields
        for field in ("approval_ratings", "approval_year"):
            field_value = therapy.get(field)
            if field in therapy and field_value is None:
                del therapy[field]
        return therapy

    def _load_therapy(self, raw_therapy: Dict) -> None:
        """Load individual therapy record into database.
        Additionally, this method takes responsibility for:
            * validating record structure correctness
            * removing duplicates from list-like fields
            * removing empty fields

        :param Dict therapy: valid therapy object.
        """
        therapy = self._prepare_therapy(raw_therapy)
        self._new_load_therapy(therapy)
        self._added_ids.append(therapy["concept_id"])


class DiseaseIndicationBase(Base):
    """Base class for sources that require disease normalization capabilities."""

    def __init__(self, database: Database, data_path: Path = DEFAULT_DATA_PATH):
        """Initialize source ETL instance.

        :param therapy.database.Database database: application database
        :param Path data_path: path to normalizer data directory
        """
        super().__init__(database, data_path)
        self.disease_normalizer = DiseaseNormalizer(self.database.endpoint_url)

    @lru_cache(maxsize=64)
    def _normalize_disease(self, query: str) -> Optional[str]:
        """Attempt normalization of disease term.
        :param str query: term to normalize
        :return: ID if successful, None otherwise
        """
        response = self.disease_normalizer.normalize(query)
        if response.match_type > 0:
            return response.disease_descriptor.disease
        else:
            logger.warning(f"Failed to normalize disease term: {query}")
            return None
