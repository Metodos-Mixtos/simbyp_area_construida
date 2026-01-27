import json
import re
from pathlib import Path
from google.cloud import storage

SECTION_PAT = re.compile(r"{{#(\w+)}}(.*?){{/\1}}", re.DOTALL)
TOKEN_PAT = re.compile(r"{{\s*([\w\.]+)\s*}}")

def _read_text(path):
    p = str(path)
    if p.startswith("gs://"):
        # parse gs://bucket/path/to/blob
        _, rest = p.split("gs://", 1)
        bucket_name, blob_path = rest.split("/", 1)
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        return blob.download_as_bytes().decode("utf-8")
    else:
        return Path(p).read_text(encoding="utf-8")

def render(template_path: Path, data_path: Path, out_path: Path):
    template = _read_text(template_path)
    data = json.loads(_read_text(data_path))
    html = render_template(template, data)
    if str(out_path).startswith("gs://"):
        # upload to GCS
        _, rest = str(out_path).split("gs://", 1)
        bucket_name, blob_path = rest.split("/", 1)
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(html.encode("utf-8"), content_type="text/html")
    else:
        out_path.write_text(html, encoding="utf-8")
    return out_path

def render_template(tpl: str, root: dict) -> str:
    def _render_block(block: str, ctx: dict) -> str:
        def _section(m):
            key, inner = m.group(1), m.group(2)
            arr = ctx.get(key, [])
            if not isinstance(arr, list):
                return ""
            return "".join(_render_block(inner, {**ctx, **item}) for item in arr)

        out = SECTION_PAT.sub(_section, block)
        def _token(m):
            k = m.group(1)
            return str(ctx.get(k, root.get(k, "")))
        return TOKEN_PAT.sub(_token, out)
    return _render_block(tpl, root)
