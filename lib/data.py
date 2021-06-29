import json
from datetime import datetime
from enum import Enum


def _get_int(d: dict, attr: str):
    """returns the attribute of the dict,
       converted to integer

       if key is not in dict, return None

       if value is a string, but NaN, exception is raused
    """

    val = d.get(attr, None)
    return int(val) if val else None

class State(Enum):
    VICTIM_STATEMENT = 0
    VICTIM_PROOF = 1
    OFFENDER_STATEMENT = 2
    OFFENDER_PROOF = 3
    STEWARD_STATEMENT = 4
    DISCUSSION_PHASE = 6
    CLOSED_PHASE = 7


class Driver:
    def __init__(self, json={}):

        if not json:
            json = {}

        self.name = json.get('name', '')
        self.number = json.get('number', 0)
        self.u_id = int(json.get('u_id', 0))

    def _to_json(self):
        d = dict()

        d['name'] = self.name
        d['number'] = self.number
        d['u_id'] = str(self.u_id)

        return d


class Incident:
    def __init__(self, json={}):

        if not json:
            json = {}

        self.g_id = _get_int(json, 'g_id')
        self.race_name = json.get('race_name', '')
        self.infringement = json.get('infringement', '')
        self.outcome = json.get('outcome', '')

        self.channel_id = int(json.get('channel_id', '0'))
        self.lap = json.get('lap', '-')
        self.state = State(json.get('state', State.VICTIM_STATEMENT.value))

        self.offender = Driver(json.get('offender', {}))
        self.victim = Driver(json.get('victim', {}))

        self.locked_time = json.get('locked_time', None)
        self.last_msg = json.get('last_msg', datetime.utcnow())

        self.cleanup_queue = list(map(int, json.get('cleanup_queue', [])))

    def _to_json(self):

        d = dict({
            'g_id': str(self.g_id),  # must be set, cannot be None
            'channel_id': str(self.channel_id),
            'lap': self.lap,
            'race_name': self.race_name,
            'infringement': self.infringement,
            'outcome': self.outcome,
            'state': self.state.value,
            'last_msg': self.last_msg,
            'locked_time': self.locked_time,
            'cleanup_queue': list(map(str, self.cleanup_queue)),
            'victim': self.victim._to_json(),
            'offender': self.offender._to_json()
        })

        return d


class Settings:
    def __init__(self, json={}):

        if not json:
            json = {}

        self.g_id = _get_int(json, 'g_id')
        self.incident_section_id = _get_int(json, 'incident_section_id')
        self.stewards_id = _get_int(json, 'stewards_id')
        self.statement_ch_id = _get_int(json, 'statement_ch_id')
        self.log_ch_id = _get_int(json, 'log_ch_id')


    def _to_json(self):
        d = dict({
            'g_id': str(self.g_id),  # must not be None
            'incident_section_id': str(self.incident_section_id) if self.incident_section_id else None,
            'stewards_id': str(self.stewards_id) if self.stewards_id else None,
            'statement_ch_id': str(self.statement_ch_id) if self.statement_ch_id else None,
            'log_ch_id': str(self.log_ch_id) if self.log_ch_id else None
        })

        return d
