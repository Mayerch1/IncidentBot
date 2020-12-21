import json
import datetime



class Driver:
    def __init__(self):
        self.name = ""
        self.number = 0
        self.u_id = 0


class Incident:
    def __init__(self):
        self.race_name = ""
        self.infringement = ""
        self.outcome = ""

        self.channel_id = None
        self.offender = None
        self.victim = None
        self.lap = 0
        self.state = 0
        self.last_msg = datetime.datetime.now().timestamp()
        self.locked_time = 0
        self.cleanup_queue = []


def _driver_to_json(d: Driver):
    return dict({
        'name': d.name,
        'number': d.number,
        'u_id': d.u_id
    })


def _json_to_driver(json):
    d = Driver()

    d.name = json['name']
    d.number = int(json['number'])
    d.u_id = int(json['u_id'])

    return d



def _incident_to_json(incident: Incident):

    d = dict({
        'channel_id': incident.channel_id,
        'lap': incident.lap,
        'race_name': incident.race_name,
        'infringement': incident.infringement,
        'outcome': incident.outcome,
        'state': incident.state,
        'last_msg': incident.last_msg,
        'locked_time': incident.locked_time,
        'cleanup_queue': incident.cleanup_queue
    })

    d['victim'] = _driver_to_json(incident.victim)
    d['offender'] = _driver_to_json(incident.offender)


    return d


def _json_to_incident(json):
    i = Incident()

    i.race_name = json['race_name']
    i.infringement = json['infringement']
    i.outcome = json['outcome']
    i.channel_id = int(json['channel_id'])
    i.lap = int(json['lap'])
    i.state = int(json['state'])
    i.last_msg = float(json['last_msg'])
    i.locked_time = float(json['locked_time'])
    i.cleanup_queue = json['cleanup_queue']

    i.victim = _json_to_driver(json['victim'])
    i.offender = _json_to_driver(json['offender'])

    return i


def incidents_to_json(incs: {}):

    incs_dict = dict()

    for i_key in incs:
        incs_dict[i_key] = _incident_to_json(incs[i_key])

    return incs_dict


def json_to_incidents(json):

    incs = {}

    for key in json:
        incs[int(key)] = _json_to_incident(json[key])

    return incs
