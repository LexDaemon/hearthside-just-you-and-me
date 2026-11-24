"""Profile-scoped local API for Hearthside's Anki study pane."""

from __future__ import annotations

import base64
import json
import importlib
import importlib.util
import mimetypes
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

_LOCAL_PACKAGE = "_hermes_hearthside_just_you_and_me_anki"
_PACKAGE_DIR = Path(__file__).resolve().parent / "anki_portable"
if _LOCAL_PACKAGE not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _LOCAL_PACKAGE,
        _PACKAGE_DIR / "__init__.py",
        submodule_search_locations=[str(_PACKAGE_DIR)],
    )
    if _spec is None or _spec.loader is None:
        raise RuntimeError("No se encontró el paquete local de la colección de Memoria.")
    _package = importlib.util.module_from_spec(_spec)
    sys.modules[_LOCAL_PACKAGE] = _package
    try:
        _spec.loader.exec_module(_package)
    except Exception:
        sys.modules.pop(_LOCAL_PACKAGE, None)
        raise
else:
    _package = sys.modules[_LOCAL_PACKAGE]
_collection_module = importlib.import_module(f"{_LOCAL_PACKAGE}.collection")
AnkiCollection = _package.AnkiCollection
CollectionError = _package.CollectionError
MissingZstandard = _package.MissingZstandard
SCHEDULER_VERSION = _collection_module.SCHEDULER_VERSION
_scheduler = importlib.import_module(f"{_LOCAL_PACKAGE}.scheduler")
MissingScheduler = importlib.import_module(f"{_LOCAL_PACKAGE}.errors").MissingScheduler

PLUGIN_ID = "hearthside-just-you-and-me"
router = APIRouter()

@router.get('/assets/{resource:path}')
def hearthside_packaged_image(resource: str):
    """Serve only reviewed public package images; never profile data or arbitrary files."""
    root = Path(__file__).resolve().parent.parent
    allowed = json.loads((root / 'runtime-resources.json').read_text())
    parts = resource.split('/')
    if resource not in allowed or any(p in ('', '.', '..') for p in parts):
        raise HTTPException(status_code=404, detail='Unknown public image')
    base = root / 'desktop'
    image = base.joinpath(*parts)
    if any(p.is_symlink() for p in (base, image, *image.parents)) or not image.is_file():
        raise HTTPException(status_code=404, detail='Public image unavailable')
    if not image.resolve().is_relative_to(base.resolve()) or image.stat().st_size > 16 * 1024 * 1024:
        raise HTTPException(status_code=404, detail='Invalid public image')
    mime = {'.png':'image/png', '.webp':'image/webp'}.get(image.suffix.lower())
    if mime is None:
        raise HTTPException(status_code=404, detail='Invalid image format')
    return {'url':'data:' + mime + ';base64,' + base64.b64encode(image.read_bytes()).decode('ascii')}



# Loaded by path, without exposing an arbitrary filesystem API or changing sys.path.
_story_spec = importlib.util.spec_from_file_location(
    '_hermes_hearthside_story_project', Path(__file__).with_name('story_project.py')
)
if _story_spec is None or _story_spec.loader is None:
    raise RuntimeError('Hearthside project service is missing')
_story_module = importlib.util.module_from_spec(_story_spec)
_story_spec.loader.exec_module(_story_module)
router.include_router(_story_module.router)


@router.post('/anki/extensions/duplicate-deck')
def anki_extension_duplicate_deck(payload:dict):
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.extensions_duplicate')
    return _call(lambda:module.duplicate(_collection(),payload))


@router.post('/anki/extensions/note-clones/plan')
def anki_extension_clone_plan(payload:dict):
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.extensions_clones')
    return _call(lambda:module.plan(_collection(),payload))


@router.post('/anki/extensions/note-clones/delete')
def anki_extension_clone_delete(payload:dict):
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.extensions_clones')
    return _call(lambda:module.remove(_collection(),payload))


@router.post('/anki/extensions/link-matches')
def anki_extension_link_matches(payload:dict):
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.extensions_links')
    return _call(lambda:module.matches(_collection(),payload))


@router.post('/anki/extensions/link-target')
def anki_extension_link_target(payload:dict):
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.extensions_links')
    return _call(lambda:module.target(_collection(),payload.get('note_id'),payload.get('card_id')))


@router.get('/anki/extensions/events')
def anki_extension_events():
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.extensions_events')
    return _call(lambda:module.read(_collection()))


@router.post('/anki/extensions/events')
def anki_extension_event_save(payload:dict):
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.extensions_events')
    return _call(lambda:module.save(_collection(),payload))


@router.post('/anki/extensions/events/delete')
def anki_extension_event_delete(payload:dict):
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.extensions_events')
    return _call(lambda:module.delete(_collection(),payload.get('id')))


@router.get('/anki/extensions/progress')
def anki_extension_progress(deck_id:int=None,include_children:bool=False,session_id:str=None):
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.extensions_progress')
    return _call(lambda:module.read(_collection(),deck_id=deck_id,include_children=include_children,session_id=session_id))



def _data_dir() -> Path:
    """Resolve storage under the active Hermes profile, never beside installable code."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        root = plugin_data_dir(PLUGIN_ID)
    except Exception:
        # Supports isolated tests and standalone dashboard loading.
        from hermes_constants import get_hermes_home

        root = get_hermes_home() / "plugin-data" / PLUGIN_ID
        root.mkdir(parents=True, exist_ok=True)
    path = Path(root) / "anki"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _collection() -> AnkiCollection:
    return AnkiCollection(_data_dir() / "collection.sqlite3")


def _call(operation):
    try:
        return operation()
    except (MissingZstandard, MissingScheduler) as exc:
        raise HTTPException(status_code=424, detail=str(exc)) from exc
    except CollectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="No se pudo completar la operación de la colección.",
        ) from exc


def _required_path(payload: Dict[str, Any]) -> Path:
    raw = payload.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(status_code=400, detail="Elegí un archivo .apkg.")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(status_code=400, detail="La ruta seleccionada no es válida.")
    return candidate


@router.get("/anki/health")
def anki_health() -> Dict[str, Any]:
    _call(_scheduler.dependencies)
    return {
        "ok": True,
        "scheduler_version": SCHEDULER_VERSION,
        "study_options_version": 6,
        "daily_limit_scopes_version": 1,
        "custom_study_version": 1,
        "filtered_decks_version": 1,
        "browser_search_history_version": 1,
        "review_actions_version": 1,
        "review_queue_version": 1,
        "preferences_version": 2,
        "storage": "active-profile plugin-data",
    }


@router.get('/anki/review/cards/{card_id}')
def anki_review_info(card_id: int):
    return _call(lambda: _collection().review_info(card_id))


@router.post('/anki/review/cards/{card_id}/action')
def anki_review_action(card_id: int, payload: Dict[str, Any]):
    if set(payload)-{'action','expected_reps','value'} or not {'action','expected_reps'}<=set(payload):
        raise HTTPException(status_code=400,detail='La acción de repaso no es válida.')
    return _call(lambda: _collection().review_action(card_id,payload['action'],expected_reps=payload['expected_reps'],value=payload.get('value')))


@router.get('/anki/backups')
def anki_backups():
    return _call(lambda:_collection().backup_status())


@router.post('/anki/backups/check')
def anki_backup_check():
    return _call(lambda:_collection().create_backup())


@router.post('/anki/backups/create')
def anki_backup_create():
    return _call(lambda:_collection().create_backup(force=True))


@router.post('/anki/backups/restore')
def anki_backup_restore(payload:Dict[str,Any]):
    if set(payload)!={'name','expected_state','confirm'}:raise HTTPException(status_code=400,detail='La solicitud de restauración no es válida.')
    return _call(lambda:_collection().restore_backup(payload['name'],payload['expected_state'],payload['confirm']))


@router.get('/anki/preferences/full')
def anki_full_preferences():
    return _call(lambda:_collection().preferences_state())


@router.post('/anki/preferences/full')
def anki_set_full_preferences(payload:Dict[str,Any]):
    if set(payload)!={'preferences','expected_version'} or not isinstance(payload['expected_version'],str):raise HTTPException(status_code=400,detail='La versión de preferencias no es válida.')
    collection=_collection()
    return _call(lambda:collection.set_full_preferences(payload['preferences'],payload['expected_version']))


@router.get('/anki/preferences')
def anki_review_preferences():
    return _call(lambda:_collection().review_preferences())


@router.post('/anki/preferences')
def anki_set_review_preferences(payload:Dict[str,Any]):
    return _call(lambda:_collection().set_review_preferences(payload))


@router.get("/anki/overview")
def anki_overview() -> Dict[str, Any]:
    return _call(lambda: _collection().overview())


@router.post('/anki/statistics')
def anki_statistics(payload: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {'query','current_deck_id','history','year','separate','forecast_days','review_days',
               'interval_range','stability_range','hourly_days','button_days','added_days'}
    if set(payload) - allowed:
        raise HTTPException(status_code=400, detail='El filtro estadístico no es válido.')
    return _call(lambda: _collection().statistics(**payload))


@router.post("/anki/decks")
def anki_create_deck(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = payload.get("name")
    description = payload.get("description", "")
    if not isinstance(name, str) or not isinstance(description, str):
        raise HTTPException(status_code=400, detail="El nombre y la descripción deben ser texto.")
    parent_id = payload.get("parent_id")
    if parent_id is not None and (not isinstance(parent_id, int) or isinstance(parent_id, bool) or parent_id < 1):
        raise HTTPException(status_code=400, detail="Elegí un mazo padre válido.")
    collection = _collection()
    deck_id = _call(lambda: collection.create_deck(name, description, parent_id=parent_id))
    actual_name = next(d["name"] for d in collection.overview()["decks"] if d["id"] == deck_id)
    return {"id": deck_id, "name": actual_name}


@router.post("/anki/decks/{deck_id}/rename")
def anki_rename_deck(deck_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload.get('name'), str):
        raise HTTPException(status_code=400, detail="El nombre debe ser texto.")
    return _call(lambda: _collection().rename_deck(deck_id, payload['name']))


@router.post("/anki/decks/{deck_id}/move")
def anki_move_deck(deck_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    if 'parent_id' not in payload:
        raise HTTPException(status_code=400, detail="Elegí un destino; null indica el nivel principal.")
    parent_id = payload['parent_id']
    if parent_id is not None and (not isinstance(parent_id, int) or isinstance(parent_id, bool) or parent_id < 1):
        raise HTTPException(status_code=400, detail="Elegí un destino válido.")
    return _call(lambda: _collection().move_deck(deck_id, parent_id))


@router.post("/anki/decks/{deck_id}/delete")
def anki_delete_deck(deck_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    counts = [payload.get('expected_card_count'), payload.get('expected_deck_count')]
    if payload.get('confirm') is not True or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in counts):
        raise HTTPException(status_code=400, detail="Confirmá explícitamente la eliminación y sus cantidades.")
    return _call(lambda: _collection().delete_deck(deck_id, *counts))


@router.post("/anki/cards")
def anki_create_card(payload: Dict[str, Any]) -> Dict[str, Any]:
    deck_id = payload.get("deck_id")
    front, back = payload.get("front"), payload.get("back")
    tags = payload.get("tags", [])
    if not isinstance(deck_id, int) or isinstance(deck_id, bool):
        raise HTTPException(status_code=400, detail="Elegí un mazo válido.")
    if not isinstance(front, str) or not isinstance(back, str):
        raise HTTPException(status_code=400, detail="La pregunta y la respuesta deben ser texto.")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise HTTPException(status_code=400, detail="Las etiquetas deben ser una lista de texto.")
    card_id = _call(lambda: _collection().add_basic_card(deck_id, front, back, tags))
    return {"id": card_id}


@router.get('/anki/authoring/editor-options')
def anki_editor_options():
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.editor_options')
    return _call(lambda:module.read(_collection()))

@router.post('/anki/authoring/editor-options')
def anki_save_editor_options(payload:dict):
    module=importlib.import_module(f'{_LOCAL_PACKAGE}.editor_options')
    return _call(lambda:module.write(_collection(),payload))



@router.get('/anki/authoring')
def anki_authoring_capabilities() -> Dict[str, Any]:
    collection=_collection()
    types = _call(lambda: collection.note_types())
    pref_state=_call(lambda:collection.preferences_state())
    latex_status=importlib.import_module(f'{_LOCAL_PACKAGE}.latex').status
    BUILTINS=importlib.import_module(f'{_LOCAL_PACKAGE}.note_types').BUILTINS
    return {'editor_version':1,'editor_options':importlib.import_module(f'{_LOCAL_PACKAGE}.editor_options').read(collection),'schema_version':1,'field_schemas':_call(lambda:collection.note_field_schemas()),'preferences':pref_state['preferences'],'last_decks':pref_state['last_decks'],'version': 3, 'generation_version': 1, 'type_management_version': 2, 'latex_version':1,'latex_runtime':latex_status(),'presets':BUILTINS, 'type_definitions': types, 'note_types': [[t['id'],t['name']] for t in types], 'max_image_bytes': 8*1024*1024, 'max_field_chars': 100000}


@router.post('/anki/authoring/types')
def anki_create_note_type(payload: Dict[str, Any]) -> Dict[str, Any]:
    if set(payload)-{'source','name'}:raise HTTPException(status_code=400,detail='Los datos del tipo no son válidos.')
    return _call(lambda: _collection().create_note_type(payload.get('source'),payload.get('name')))


@router.get('/anki/authoring/types/manage')
def anki_manage_types():
    return _call(lambda: {'types':_collection().manage_note_types()})


@router.post('/anki/authoring/types/rename')
def anki_rename_type(payload: Dict[str,Any]):
    if set(payload)!={'id','name'}:raise HTTPException(status_code=400,detail='Los datos del tipo no son válidos.')
    return _call(lambda:_collection().rename_note_type(payload['id'],payload['name']))


@router.post('/anki/authoring/types/delete')
def anki_delete_type(payload: Dict[str,Any]):
    if set(payload)!={'id','expected_note_count'}:raise HTTPException(status_code=400,detail='La confirmación no es válida.')
    return _call(lambda:_collection().delete_note_type(payload['id'],payload['expected_note_count']))


@router.post('/anki/authoring/types/latex')
def anki_type_latex(payload: Dict[str,Any]):
    if set(payload)=={'id'}:return _call(lambda:_collection().note_latex(payload['id']))
    if set(payload)=={'id','options'}:return _call(lambda:_collection().set_note_latex(payload['id'],payload['options']))
    raise HTTPException(status_code=400,detail='Las opciones no son válidas.')


@router.get('/anki/authoring/schema/{ident}')
def anki_note_schema(ident:str) -> Dict[str,Any]:
    return _call(lambda:_collection().note_schema(ident))


@router.post('/anki/authoring/schema/{ident}')
def anki_save_note_schema(ident:str,payload:Dict[str,Any]) -> Dict[str,Any]:
    if set(payload)!={'schema','expected_version','confirm'} or type(payload['confirm']) is not bool:
        raise HTTPException(status_code=400,detail='Los datos del tipo de nota no son válidos.')
    return _call(lambda:_collection().save_note_schema(ident,payload['schema'],payload['expected_version'],payload['confirm']))


@router.post('/anki/authoring/schema/{ident}/preview')
def anki_preview_note_schema(ident:str,payload:Dict[str,Any]) -> Dict[str,Any]:
    if set(payload)!={'schema','expected_version','fields'}:
        raise HTTPException(status_code=400,detail='Los datos de vista previa no son válidos.')
    return _call(lambda:_collection().preview_note_schema(ident,payload['schema'],payload['expected_version'],payload['fields']))


@router.post('/anki/authoring/preview')
def anki_authoring_preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    if set(payload)!={'note_type','fields'}:raise HTTPException(status_code=400,detail='Los datos de vista previa no son válidos.')
    return _call(lambda: _collection().preview_note(payload.get('note_type'),payload.get('fields')))


@router.post('/anki/authoring/image')
def anki_authoring_image(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _call(lambda: _collection().add_image(payload.get('base64')))


@router.post('/anki/notes')
def anki_create_note(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _call(lambda: _collection().add_note(payload.get('deck_id'), payload.get('front'), payload.get('back'),
        payload.get('tags', []), note_type=payload.get('note_type', 'basic'), allow_duplicate=payload.get('allow_duplicate', False), fields=payload.get('fields')))


@router.get('/anki/decks/{deck_id}/options')
def anki_deck_options(deck_id: int) -> Dict[str, Any]:
    collection = _collection()
    return _call(lambda: {'options': collection.deck_options(deck_id), 'defaults': collection.default_deck_options(), 'optimization': collection.optimization_status(deck_id), 'daily_limits': collection.daily_limit_options(deck_id)})


@router.post('/anki/decks/{deck_id}/options')
def anki_save_options(deck_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    if set(payload) - {'options', 'reset', 'daily_limits'} or type(payload.get('reset', False)) is not bool or ('daily_limits' in payload and not isinstance(payload['daily_limits'],dict)):
        raise HTTPException(status_code=400, detail='Las opciones no son válidas.')
    return _call(lambda: {'options': _collection().set_deck_options(deck_id, payload.get('options'), reset=payload.get('reset',False), daily_limits=payload.get('daily_limits'))})


@router.post('/anki/decks/{deck_id}/optimize')
def anki_optimize(deck_id: int) -> Dict[str, Any]:
    return _call(lambda: _collection().optimize_fsrs(deck_id))


@router.get("/anki/review/next")
def anki_next_card(deck_id: Optional[int] = None, include_children: bool = False) -> Dict[str, Any]:
    if deck_id is not None and deck_id < 1:
        raise HTTPException(status_code=422, detail="Elegí un mazo válido.")
    collection = _collection()
    now = collection._now(None)
    def read_next():
        card = collection.next_due(now=now,deck_id=deck_id,include_children=include_children)
        return {'card':card,'review_options':collection.deck_options(card['deck_id']) if card else None,
                'status':collection.study_status(now=now,deck_id=deck_id,include_children=include_children),'review_preferences':collection.review_preferences()}
    return _call(read_next)


@router.post('/anki/cards/{card_id}/bury')
def anki_bury_card(card_id:int,payload:Dict[str,Any]) -> Dict[str,Any]:
    expected = payload.get('expected_reps')
    if set(payload)-{'expected_reps'} or type(expected) is not int or expected < 0:
        raise HTTPException(status_code=400,detail='La versión de la tarjeta no es válida.')
    return _call(lambda:_collection().bury_card(card_id,expected_reps=expected))


@router.post("/anki/cards/{card_id}/grade")
def anki_grade_card(card_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    rating = payload.get("rating")
    response_ms = payload.get("response_ms", 0)
    if not isinstance(rating, str):
        raise HTTPException(status_code=400, detail="La calificación no es válida.")
    if not isinstance(response_ms, int) or isinstance(response_ms, bool):
        raise HTTPException(status_code=400, detail="El tiempo de respuesta no es válido.")
    expected = payload.get('expected_reps')
    if expected is not None and (type(expected) is not int or expected < 0):
        raise HTTPException(status_code=400, detail='La versión de la tarjeta no es válida.')
    return _call(lambda: _collection().grade(card_id, rating, response_ms=response_ms, expected_reps=expected,review_deck_id=payload.get('review_deck_id'),include_children=payload.get('include_children',False)))


@router.post("/anki/import")
def anki_import(payload: Dict[str, Any]) -> Dict[str, Any]:
    package_path = _required_path(payload)
    result = _call(lambda: _collection().import_apkg(package_path))
    return {"result": result, "filename": package_path.name}


@router.get("/anki/media/{name}")
def anki_media(name: str) -> Dict[str, str]:
    raw = _call(lambda: _collection().media_bytes(name))
    if name.startswith('latex-') and name.endswith('.svg'):
        clean=importlib.import_module(f'{_LOCAL_PACKAGE}.latex').clean_svg
        raw=_call(lambda:clean(raw))
        return {'name':name,'mime':'image/svg+xml','base64':base64.b64encode(raw).decode('ascii')}
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="La imagen supera el límite de 8 MiB para mostrarse en una tarjeta.")
    mime, _ = mimetypes.guess_type(name)
    if mime not in {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}:
        raise HTTPException(status_code=415, detail="Este tipo de imagen se conserva en el .apkg, pero no se muestra todavía.")
    return {"name": name, "mime": mime, "base64": base64.b64encode(raw).decode("ascii")}


@router.post("/anki/export")
def anki_export(payload: Dict[str, Any]) -> Dict[str, Any]:
    package_path = _required_path(payload)
    deck_id = payload.get('deck_id')
    if deck_id is not None and (not isinstance(deck_id, int) or isinstance(deck_id, bool) or deck_id < 1):
        raise HTTPException(status_code=400, detail="Elegí un mazo válido para exportar.")
    collection = _collection()
    result = _call(lambda: collection.export_apkg(package_path, deck_id=deck_id))
    summary = collection.overview()
    count = summary['card_count'] if deck_id is None else next((deck['subtree_card_count'] for deck in summary['decks'] if deck['id'] == deck_id), 0)
    return {"filename": result.name, "cards": count}


@router.post('/anki/browser/command')
def anki_browser_command(payload:Dict[str,Any]):
    if set(payload)!={'ids','mode','action','value'}:raise HTTPException(status_code=400,detail='La acción no es válida.')
    return _call(lambda:_collection().browser_command(**payload))

@router.get('/anki/browser/cards/{card_id}/preview')
def anki_browser_preview(card_id:int):
    return _call(lambda:_collection().browser_preview(card_id))

@router.post('/anki/browser/duplicates')
def anki_browser_duplicates(payload:Dict[str,Any]):
    if set(payload)-{'field','query'} or 'field' not in payload:raise HTTPException(status_code=400,detail='La búsqueda de duplicadas no es válida.')
    return _call(lambda:_collection().browser_duplicates(**payload))

@router.post('/anki/browser/change-note-type/plan')
def anki_browser_change_plan(payload:Dict[str,Any]):
    if set(payload)-{'ids','mode','target','fields','templates'} or not {'ids','mode','target'}<=set(payload):raise HTTPException(status_code=400,detail='La conversión no es válida.')
    return _call(lambda:_collection().browser_change_plan(**payload))

@router.post('/anki/browser/export')
def anki_browser_export(payload:Dict[str,Any]):
    if set(payload)!={'ids','mode','path'}:raise HTTPException(status_code=400,detail='La exportación no es válida.')
    destination=_required_path(payload)
    def export():
        from anki_portable.browser_ops import selection
        collection=_collection()
        with collection._study_connection() as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            rows=selection(db,payload['ids'],payload['mode']);nids=sorted({r['note_id'] for r in rows})
        result=collection.export_apkg(destination,note_ids=nids)
        return {'filename':result.name}
    return _call(export)

@router.post('/anki/browser/copy')
def anki_browser_copy(payload:Dict[str,Any]):
    if set(payload)!={'ids','mode'}:raise HTTPException(status_code=400,detail='La selección no es válida.')
    return _call(lambda:_collection().browser_copy(payload['ids'],payload['mode']))

@router.post('/anki/browser/sidebar')
def anki_browser_sidebar(payload:Dict[str,Any]):
    if set(payload)!={'action','value'}:raise HTTPException(status_code=400,detail='La operación de barra lateral no es válida.')
    return _call(lambda:_collection().browser_sidebar(payload['action'],payload['value']))

@router.get('/anki/browser/search-history')
def anki_browser_search_history():
    return _call(lambda: _collection().browser_search_history())

@router.post('/anki/browser/search-history')
def anki_browser_record_search(payload: Dict[str, Any]):
    if set(payload) - {'query', 'current_deck_id'} or 'query' not in payload:
        raise HTTPException(status_code=400, detail='La búsqueda no es válida.')
    return _call(lambda: _collection().browser_search_history(**payload))

@router.post('/anki/browser/saved-search/manage')
def anki_browser_manage_saved_search(payload: Dict[str, Any]):
    if set(payload) - {'action', 'name', 'new_name'} or not {'action', 'name'} <= set(payload):
        raise HTTPException(status_code=400, detail='La búsqueda guardada no es válida.')
    return _call(lambda: _collection().browser_manage_saved_search(**payload))

@router.get('/anki/browser/history')
def anki_browser_history():
    return _call(lambda:_collection().browser_history())

@router.post('/anki/browser/history')
def anki_browser_undo(payload:Dict[str,Any]):
    if set(payload)!={'action'} or payload['action'] not in ('undo','redo'):raise HTTPException(status_code=400,detail='La acción de historial no es válida.')
    return _call(lambda:_collection().browser_history(payload['action']))

@router.get('/anki/browser/config')
def anki_browser_config():
    return _call(lambda:_collection().browser_config())

@router.post('/anki/browser/config')
def anki_browser_set_config(payload:Dict[str,Any]):
    if set(payload)!={'state'}:raise HTTPException(status_code=400,detail='La configuración de Explorar no es válida.')
    return _call(lambda:_collection().browser_config(payload['state']))

@router.post('/anki/browser/table')
def anki_browser_table(payload:Dict[str,Any]):
    if set(payload)-{'query','mode','sort','descending','limit','offset','current_deck_id'}:raise HTTPException(status_code=400,detail='La búsqueda no es válida.')
    return _call(lambda:_collection().browser_table(**payload))

@router.get('/anki/browser/facets')
def anki_browser_facets() -> Dict[str, Any]:
    return _call(lambda: _collection().browser_facets())


@router.post('/anki/browser/search')
def anki_browser_search(payload: Dict[str, Any]) -> Dict[str, Any]:
    if set(payload)-{'query','mode','sort','descending','limit','offset','current_deck_id'}:
        raise HTTPException(status_code=400, detail='Los parámetros de Explorar no son válidos.')
    return _call(lambda: _collection().browse(**payload))


@router.post('/anki/browser/notes/{note_id}')
def anki_browser_edit(note_id:int,payload:Dict[str,Any]) -> Dict[str,Any]:
    if set(payload)!={'fields','tags','expected_version'}:
        raise HTTPException(status_code=400,detail='Los datos de la nota no son válidos.')
    return _call(lambda: _collection().browser_edit(note_id,payload['fields'],payload['tags'],payload['expected_version']))


@router.post('/anki/browser/action')
def anki_browser_action(payload:Dict[str,Any]) -> Dict[str,Any]:
    if set(payload)!={'card_ids','action','value'}:
        raise HTTPException(status_code=400,detail='La acción sobre tarjetas no es válida.')
    return _call(lambda: _collection().browser_action(payload['card_ids'],payload['action'],payload['value']))


@router.post('/anki/browser/saved-search')
def anki_browser_save_search(payload:Dict[str,Any]) -> Dict[str,Any]:
    if set(payload)!={'name','query'}:
        raise HTTPException(status_code=400,detail='La búsqueda guardada no es válida.')
    return _call(lambda: _collection().browser_save_search(payload['name'],payload['query']))


@router.get('/anki/filtered-decks/options')
def anki_filtered_deck_options():
    names = {d['name'] for d in _collection().overview()['decks']}
    index = 1
    while 'Mazo filtrado '+str(index) in names:
        index += 1
    return {'default_name': 'Mazo filtrado '+str(index), 'orders': [[0,'Más antiguas revisadas primero'],[1,'Aleatorio'],[2,'Intervalos crecientes'],[3,'Intervalos decrecientes'],[4,'Más olvidos primero'],[5,'Orden de adición'],[6,'Orden de vencimiento'],[7,'Orden de adición inverso'],[8,'Recuperabilidad creciente'],[9,'Recuperabilidad decreciente']]}

@router.get('/anki/filtered-decks/preview')
def anki_preview_filtered_deck(terms: str, deck_id: Optional[int]=None, current_deck_id: Optional[int]=None):
    try:
        filters = json.loads(terms)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail='El filtro no es válido.')
    return _call(lambda: _collection().preview_filtered_deck(filters, deck_id=deck_id, current_deck_id=current_deck_id))

@router.get('/anki/filtered-decks/{deck_id}')
def anki_filtered_deck(deck_id: int):
    return _call(lambda: _collection().filtered_deck(deck_id))

@router.post('/anki/filtered-decks')
def anki_build_filtered_deck(payload: Dict[str, Any]):
    allowed = {'name','terms','reschedule','allow_empty','deck_id','preview_again','preview_hard','preview_good','current_deck_id'}
    if set(payload)-allowed or not {'name','terms'} <= set(payload):
        raise HTTPException(status_code=400, detail='El mazo filtrado no es válido.')
    return _call(lambda: _collection().build_filtered_deck(**payload))

@router.post('/anki/filtered-decks/{deck_id}/rebuild')
def anki_rebuild_filtered_deck(deck_id: int, payload: Dict[str, Any]):
    if payload:
        raise HTTPException(status_code=400, detail='La reconstrucción no admite opciones adicionales.')
    return _call(lambda: _collection().rebuild_filtered_deck(deck_id))

@router.post('/anki/filtered-decks/{deck_id}/empty')
def anki_empty_filtered_deck(deck_id: int, payload: Dict[str, Any]):
    if payload:
        raise HTTPException(status_code=400, detail='Vaciar no admite opciones adicionales.')
    return _call(lambda: _collection().empty_filtered_deck(deck_id))


@router.get('/anki/decks/{deck_id}/custom-study')
def anki_custom_study_info(deck_id:int,include_children:bool=False) -> Dict[str,Any]:
    return _call(lambda:_collection().custom_study_info(deck_id,include_children))


@router.post('/anki/decks/{deck_id}/custom-study')
def anki_custom_study_start(deck_id:int,payload:Dict[str,Any]) -> Dict[str,Any]:
    if 'mode' not in payload or set(payload)-{'mode','include_children','amount','days','limit','state','include_tags','exclude_tags'}:
        raise HTTPException(status_code=400,detail='Los datos de estudio personalizado no son válidos.')
    return _call(lambda:_collection().start_custom_study(deck_id,**payload))


@router.get('/anki/custom-study/{session_id}/next')
def anki_custom_session_next(session_id:str) -> Dict[str,Any]:
    return _call(lambda:_collection().custom_session_next(session_id))


@router.post('/anki/custom-study/{session_id}/grade')
def anki_custom_session_grade(session_id:str,payload:Dict[str,Any]) -> Dict[str,Any]:
    if not {'card_id','rating','expected_token','expected_reps'}<=set(payload) or set(payload)-{'card_id','rating','expected_token','expected_reps','response_ms'}:
        raise HTTPException(status_code=400,detail='La respuesta de la sesión no es válida.')
    return _call(lambda:_collection().custom_session_grade(session_id,**payload))


@router.post('/anki/custom-study/{session_id}/close')
def anki_custom_session_close(session_id:str,payload:Dict[str,Any]) -> Dict[str,Any]:
    if payload: raise HTTPException(status_code=400,detail='Los datos para cerrar la sesión no son válidos.')
    return _call(lambda:_collection().close_custom_session(session_id))


@router.post('/anki/decks/{deck_id}/description')
def anki_deck_description(deck_id:int,payload:Dict[str,Any]) -> Dict[str,Any]:
    if set(payload)!={'description'}: raise HTTPException(status_code=400,detail='La descripción no es válida.')
    return _call(lambda:_collection().set_deck_description(deck_id,payload['description']))
