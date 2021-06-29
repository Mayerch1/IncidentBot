import os
import pymongo
from pymongo import MongoClient

from decimal import *
from datetime import datetime, timedelta
import json
from threading import Lock
import copy

from tinydb import TinyDB, Query, where
from tinydb.operations import delete

import lib.data


class Server:
    g_id = None  # id
    prefix = '*'

    statement_ch_id = None
    log_ch_id = None
    incident_section_id = None
    stewards_id = None
    incident_cnt = 0

    active_incidents = {}

class TinyConnector:
    _current_file = 'servers.json'
    db = TinyDB(_current_file)
    q = Query()

    cache = {}

    db_lock = Lock()

    client = None
    db = None

    @staticmethod
    def init():
        host = os.getenv('MONGO_CONN')
        port = int(os.getenv('MONGO_PORT'))

        uname = os.getenv('MONGO_ROOT_USER')
        pw = os.getenv('MONGO_ROOT_PASS')

        TinyConnector.client = MongoClient(host=host, username=uname, password=pw, port=port)
        TinyConnector.db = TinyConnector.client.incidentBot


    @staticmethod
    def _delete_guild(guild_id: int):
        TinyConnector.db.settings.delete_one({'g_id': str(guild_id)})
        TinyConnector.db.incident_cnt.delete_one({'g_id': str(guild_id)})


    # get the server object from db, creates new entry if not exists yet
    # guaranteed to return a object
    @staticmethod
    def get_settings(guild_id: int):

        sett_json = TinyConnector.db.settings.find_one({'g_id': str(guild_id)})

        if not sett_json:
            return lib.data.Settings({'g_id': guild_id})

        return lib.data.Settings(sett_json)


    # save changes to a server into the db
    # never use self-contsructed Server objects
    @staticmethod
    def update_settings(settings: lib.data.Settings):
        """update the entire guild object in the db
           might cause data loss, if multiple guild objects are accessed async

        """
        sett_json = settings._to_json()
        TinyConnector.db.settings.replace_one({'g_id': str(settings.g_id)}, sett_json, upsert=True)


    @staticmethod
    def update_incident(incident: lib.data.Incident):
        """update only the passed incident in the db
           all other modifications to the guild obj are lost
           prevents data loss, as long as each incident is only accessed at a time
           (but multiple guild objects at a time)

           get_guild MUST be called before

        """

        inc_json = incident._to_json()
        TinyConnector.db.incidents.replace_one({'channel_id': str(incident.channel_id)}, inc_json, upsert=True)


    @staticmethod
    def update_incident_msg_ts(channel_id: int):
        """update the last_msg property of the current incident
           to the current utc timestamp

           Returns: true on success, false if incident is not existing
        """

        ts = datetime.utcnow()

        result = TinyConnector.db.incidents.find_one_and_update({'channel_id': str(channel_id)}, {'$set': {'last_msg': ts}})

        return result != None


    @staticmethod
    def get_incident(channel_id: int):
        """return the incident of the given channel
           None, if no incident is open
        """
        inc_json = TinyConnector.db.incidents.find_one({'channel_id': str(channel_id)})

        if not inc_json:
            return None

        return lib.data.Incident(inc_json)


    @staticmethod
    def extend_cleanup_queue(channel_id, message_ids: []):
        """add a range of message ids to the cleanup queue
           NOP if incident does not exist
        """

        m_ids = list(map(str, message_ids))

        TinyConnector.db.incidents.find_one_and_update({'channel_id': str(channel_id)}, {'$push': {'cleanup_queue': {'$each': m_ids}}})


    @staticmethod
    def clear_cleanup_queue(channel_id: int):
        """resets the cleanup queue of the specified incident
           NOP if incident not existing
        """

        TinyConnector.db.incidents.find_one_and_update({'channel_id': str(channel_id)}, {'$set': {'cleanup_queue': []}})


    @staticmethod
    def get_incident_modified_before(timestamp):
        """get all incident with the last modification
           older than the supplied timestamp
        """

        incs =  list(TinyConnector.db.incidents.find({'last_msg': {'$lt': timestamp}}))
        incs = list(map(lib.data.Incident, incs))

        return incs

    @staticmethod
    def get_incident_locked_before(timestamp):
        """get all incidents which have been locked
           before the given timestmap
        """

        incs =  list(TinyConnector.db.incidents.find({'locked_time': {'$lt': timestamp}}))
        incs = list(map(lib.data.Incident, incs))

        return incs

    @staticmethod
    def delete_incident(channel_id: int):
        """deletes given incident out of db
           get_guild MUST be called before

        """
        TinyConnector.db.incidents.delete_one({'channel_id': str(channel_id)})

    @staticmethod
    def get_inc_cnt(guild_id: int):

        result = TinyConnector.db.incident_cnt.find_one({'g_id': str(guild_id)}, {'incident_cnt': 1})

        if not result:
            return 0

        return result.get('incident_cnt', 0)


    @staticmethod
    def incr_inc_cnt(guild_id: int):

        TinyConnector.db.incident_cnt.find_one_and_update({'g_id': str(guild_id)}, {'$inc': {'incident_cnt': 1}}, new=True, upsert=True)
