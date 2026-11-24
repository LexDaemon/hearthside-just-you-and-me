"""Read-only card statistics, using stored events and the pinned FSRS engine."""
import math,json
from . import scheduler as fsrs
from .note_schema import load


def _retrievability(engine,memory,at):
    # FSRS 6.3.2 get_card_retrievability uses timedelta.days (whole days).
    # Use that same engine's fitted decay/factor with fractional elapsed days for a continuous plot.
    days=max(0,(at-memory.last_review.timestamp())/86400)
    return (1+engine._FACTOR*days/memory.stability)**engine._DECAY


def build(collection,db,card,now):
    aggregate=db.execute('SELECT COUNT(*) AS total,MIN(reviewed_at) AS first,MAX(reviewed_at) AS latest,COALESCE(SUM(response_ms),0) AS milliseconds FROM reviews WHERE card_id=? AND reviewed_at<=?',(card['id'],now)).fetchone()
    logs=[dict(r) for r in db.execute('SELECT * FROM reviews WHERE card_id=? AND reviewed_at<=? ORDER BY reviewed_at DESC,id DESC LIMIT 200',(card['id'],now))]
    options=collection._options(db,card['deck_id']);schema=load(db,card['note_type_id'])
    templates=schema['templates'];ordinal=card['ordinal'];template=templates[min(ordinal,len(templates)-1)]['name'] if templates else None
    if schema['kind']=='cloze':template=(template or 'Respuesta anidada')+' '+str(ordinal+1)
    summary=dict(added=card['added_at'],first_review=aggregate['first'],latest_review=aggregate['latest'],due=card['due_at'] if card['card_type'] else None,position=card['new_position'],interval_days=card['interval_days'],stability=None,difficulty_percent=None,retrievability_percent=None,ease_percent=None if options['fsrs_enabled'] else card['ease_factor']/10,review_count=card['reps'],lapses=card['lapses'],average_ms=aggregate['milliseconds']/aggregate['total'] if aggregate['total'] else None,total_ms=aggregate['milliseconds'] if aggregate['total'] else None,card_template=template,note_type=card['model_label'],deck=card['deck'],configuration='Predeterminado' if options==collection.default_deck_options() else 'Personalizada',card_id=card['id'],note_id=card['note_id'])
    curve=None
    if options['fsrs_enabled'] and aggregate['total'] and card['reps']:
        from .review_actions import history
        engine=fsrs.scheduler(options,collection._parameters(db,card['deck_id']))
        memory=fsrs.restore(card,[] if card['fsrs_json'] else history(db,card['id'],now),engine,options)
        if memory.last_review and memory.last_review.timestamp()<=now and memory.stability is not None and memory.difficulty is not None:
            summary.update(stability=memory.stability,difficulty_percent=max(0,min(100,(memory.difficulty-1)*100/9)),retrievability_percent=_retrievability(engine,memory,now)*100)
            start=int(memory.last_review.timestamp());duration=max(86400,min(36500*86400,int(memory.stability*1.5*86400)))
            end=max(now+86400,card['due_at']+86400,start+duration)
            points=[{'at':at} for at in sorted({now,*[start+(end-start)*i//120 for i in range(121)]})]
            for point in points:point['percent']=_retrievability(engine,memory,point['at'])*100
            if all(math.isfinite(p['percent']) for p in points):curve={'points':points,'now':now,'desired_retention':options['desired_retention']*100}
    for entry in logs:
        entry['rating_number']=fsrs.RATINGS.get(entry['rating'])
        entry['review_kind']={'new':'Aprender','learning':'Aprender','interday':'Aprender','review':'Repasar'}.get(entry.get('study_kind'),'—')
        # Legacy logs did not store the exact sub-day interval or per-answer FSRS difficulty.
        # Do not substitute today's difficulty or reconstruct historical values with changed options.
        entry['ease_percent']=entry['ease_factor']/10 if entry.get('scheduler')=='sm2-classic-v1' else None
        entry['interval_seconds']=None
        if entry.get('scheduler')=='fsrs-6' and entry.get('fsrs_log'):
            try:detail=json.loads(entry['fsrs_log']).get('_memoria_info_v1',{})
            except (ValueError,TypeError,AttributeError):detail={}
            if isinstance(detail,dict):
                seconds=detail.get('interval_seconds');difficulty=detail.get('difficulty_after');state=detail.get('state_before')
                if type(seconds) is int and seconds>=0:entry['interval_seconds']=seconds
                if type(difficulty) in (int,float) and math.isfinite(difficulty) and 1<=difficulty<=10:entry['ease_percent']=(difficulty-1)*100/9
                if type(state) is int and state in (1,2,3):entry['review_kind']={1:'Aprender',2:'Repasar',3:'Reaprender'}[state]

    return {'info_version':2,'summary':summary,'forgetting_curve':curve,'history':logs,'history_total':aggregate['total']}
