"""Independent SM-2-derived adapter; no Anki source or dependency.

Core specification: https://super-memory.com/english/ol/sm2.htm
Quality mapping Again/Hard/Good/Easy -> 2/3/4/5. Intraday learning,
relearning and four-button policies are ours, not Anki's SM-2 scheduler.
"""
from dataclasses import dataclass, replace, asdict
from datetime import datetime, timedelta, timezone
from enum import IntEnum
import json
import math
from .errors import CollectionError

VERSION = 'sm2-classic-v1'
QUALITY = {'again':2,'hard':3,'good':4,'easy':5}


class State(IntEnum):
    Learning=1
    Review=2
    Relearning=3


@dataclass
class Card:
    card_id: int
    due: datetime
    state: State = State.Learning
    step: int = 0
    successes: int = 0
    interval_days: int = 0
    ease_factor: int = 2500
    last_review: datetime | None = None

    def to_json(self):
        data=asdict(self)
        data.update(due=self.due.isoformat(),last_review=self.last_review.isoformat() if self.last_review else None,state=int(self.state))
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw):
        data=json.loads(raw)
        if type(data['state']) is not int or type(data['card_id']) is not int or not 0 < data['card_id'] < 2**63-1:
            raise ValueError('state/card_id')
        data['due']=datetime.fromisoformat(data['due'])
        data['last_review']=datetime.fromisoformat(data['last_review']) if data['last_review'] else None
        data['state']=State(data['state'])
        card=cls(**data)
        if card.due.tzinfo is None or not 0 < card.due.timestamp() < 253402214400:
            raise ValueError('due')
        if card.last_review and (card.last_review.tzinfo is None or card.last_review > card.due):
            raise ValueError('last_review')
        for key,lo,hi in [('step',0,8),('successes',0,2**31-1),('interval_days',0,36500),('ease_factor',1300,1000000)]:
            value=getattr(card,key)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(key)
        return card


def restore(row):
    if row['classic_json']:
        card=Card.from_json(row['classic_json'])
        return replace(card,due=datetime.fromtimestamp(row['due_at'],timezone.utc))
    # A current FSRS card has no exact classic counterpart. Seed explicitly
    # from its current interval/ease/step; never reinterpret FSRS difficulty as ease.
    saved=json.loads(row['fsrs_json']) if row['fsrs_json'] else None
    state=State(saved['state']) if saved else State.Relearning if row['queue'] in (1,3) and row['card_type']==2 else State.Learning if row['queue'] in (0,1,3) else State.Review
    interval=max(0,row['interval_days'])
    successes=0 if state!=State.Review else 1 if interval<6 else 2
    return Card(row['id'],datetime.fromtimestamp(row['due_at'],timezone.utc),state,(saved.get('step') or 0) if saved else 0,successes,interval,max(1300,row['ease_factor']),datetime.fromisoformat(saved['last_review']) if saved and saved.get('last_review') else None)


def _review_interval(card, rating, maximum):
    q=QUALITY[rating]
    ease=max(1300,round(card.ease_factor+1000*(0.1-(5-q)*(0.08+(5-q)*0.02))))
    successes=card.successes+1 if q>=3 else 0
    interval=1 if successes<=1 else 6 if successes==2 else math.ceil(card.interval_days*card.ease_factor/1000)
    return min(maximum,max(1,interval)),successes,ease


def evaluate(row, options, rating, now):
    if rating not in QUALITY:
        raise CollectionError('Calificación no válida.')
    before=restore(row)
    at=datetime.fromtimestamp(now,timezone.utc)
    card=replace(before,last_review=at)
    maximum=options['maximum_interval']
    if before.state==State.Review:
        interval,successes,ease=_review_interval(before,rating,maximum)
        card=replace(card,interval_days=interval,successes=successes,ease_factor=ease)
        if rating=='again' and options['relearning_steps']:
            card=replace(card,state=State.Relearning,step=0,due=at+timedelta(seconds=options['relearning_steps'][0]))
        else:
            card=replace(card,state=State.Review,step=0,due=at+timedelta(days=interval))
    else:
        steps=options['relearning_steps'] if before.state==State.Relearning else options['learning_steps']
        step=min(before.step,max(0,len(steps)-1))
        if steps and rating!='easy' and (rating in ('again','hard') or step+1<len(steps)):
            step=0 if rating=='again' else step+1 if rating=='good' else step
            delay=steps[step]
            if rating=='hard':
                delay=round((delay+steps[step+1])/2) if step+1<len(steps) else round(delay*1.5)
            card=replace(card,step=step,due=at+timedelta(seconds=delay))
        else:
            # Graduating completes the first successful classic repetition.
            interval,successes,ease=_review_interval(replace(card,successes=0),rating,maximum)
            card=replace(card,state=State.Review,step=0,interval_days=interval,successes=successes,ease_factor=ease,due=at+timedelta(days=interval))
    return before,card,None
