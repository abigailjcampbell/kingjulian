import csv
import io
import zipfile

from attachments.tests.base import FilesWorld


def parse(response_or_text):
    """Parse a CSV download exactly as a spreadsheet would: strip the BOM, honour quoting."""
    text = response_or_text if isinstance(response_or_text, str) else response_or_text.content.decode("utf-8")
    assert text.startswith("\ufeff"), "UTF-8 BOM expected so Excel reads accents correctly"
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def zip_members(response):
    z = zipfile.ZipFile(io.BytesIO(response.content))
    return {n: z.read(n).decode("utf-8") for n in z.namelist()}


class ExportWorld(FilesWorld):
    def table(self, url, user=None):
        if user:
            self.login(user)
        r = self.client.get(url)
        assert r.status_code == 200, (url, r.status_code)
        rows = parse(r)
        return rows[0], rows[1:]
