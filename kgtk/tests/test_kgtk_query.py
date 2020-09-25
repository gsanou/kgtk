import shutil
import unittest
import tempfile
import pandas as pd
from kgtk.cli_entry import cli_entry


class TestKGTKQuery(unittest.TestCase):
    def setUp(self) -> None:
        self.file_path = 'data/kypher/graph.tsv'
        self.temp_dir = tempfile.mkdtemp()
        self.df = pd.read_csv(self.file_path, sep='\t')

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir)

    def test_kgtk_query_default(self):
        cli_entry("kgtk", "query", "-i", self.file_path, "-o", f'{self.temp_dir}/out.tsv')
        df = pd.read_csv(f'{self.temp_dir}/out.tsv', sep='\t')
        self.assertTrue(len(df) == 9)

    def test_kgtk_query_match(self):
        cli_entry("kgtk", "query", "-i", self.file_path, "-o", f'{self.temp_dir}/out.tsv', "--match",
                  "(i)-[:loves]->(c)")
        df = pd.read_csv(f'{self.temp_dir}/out.tsv', sep='\t')
        self.assertTrue(len(df) == 3)
        ids = list(df['id'].unique())
        self.assertTrue('e11' in ids)
        self.assertTrue('e12' in ids)
        self.assertTrue('e14' in ids)
