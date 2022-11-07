"""Build RxNorm test data."""
from pathlib import Path
import csv

from therapy.database import Database
from therapy.etl.rxnorm import RxNorm


db = Database()
rx = RxNorm(db)
rx._get_rrf(False)
TEST_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "rxnorm"
rxnorm_outfile_path = TEST_DATA_DIR / rx._src_file.name
drug_forms_outfile_path = TEST_DATA_DIR / rx._drug_forms_file.name

rxnorm_ids = {
    "100213", "2555", "142424", "644", "10600", "1011", "1911", "44", "61", "595",
    "10582", "4493", "227224"
}

rows_to_add = []
with open(rx._src_file, "r") as f:
    reader = csv.reader(f)

    for row in reader:
        if row[0] in rxnorm_ids:
            rows_to_add.append(row)

with open(rxnorm_outfile_path, "w") as f:
    writer = csv.writer(f)
    writer.writerows(rows_to_add)
