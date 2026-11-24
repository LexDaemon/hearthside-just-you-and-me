"""Local editor preferences; math preview uses the bundled passive SVG renderer."""
import json
from .errors import CollectionError
DEFAULT={'shrink_images':True,'mathjax_preview':True,'close_html_tags':True}
KEY='authoring_editor_options_v1'
def load(db):
 row=db.execute('SELECT value FROM metadata WHERE key=?',(KEY,)).fetchone()
 return dict(DEFAULT,**(json.loads(row[0]) if row else {}))
def read(collection):
 with collection._study_connection() as db:return load(db)
def write(collection,changes):
 if not isinstance(changes,dict) or not changes or set(changes)-set(DEFAULT) or any(type(v) is not bool for v in changes.values()):raise CollectionError('Las opciones de edición no son válidas.')
 with collection._study_connection() as db:
  db.execute('BEGIN IMMEDIATE');value=dict(load(db),**changes)
  db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(KEY,json.dumps(value)))
  return value

