from decimal import *
from datetime import datetime, timedelta
import json

import copy


from tinydb import TinyDB, Query
from tinydb.operations import delete

import lib.data



class Server:
    g_id = None  # id
    prefix = '*'

    statement_ch_id = None
    incident_section_id = None
    stewards_id = None

    active_incidents = {}




class TinyConnector:
    _current_file = 'servers.json'
    db = TinyDB(_current_file)
    q = Query()

    cache = {}

    @staticmethod
    def _set_database(file_path: str):
        TinyConnector._current_file = file_path

        # reset database to new file
        # requires clearing of cache
        TinyConnector.db.close()
        TinyConnector.db = TinyDB(TinyConnector._current_file)
        TinyConnector.q = Query()
        TinyConnector._clear_cache()


    @staticmethod
    def _clear_cache():
        """clears the in-memory cache
        used when manually edited the save_file
        e.g. for unittest

        """
        TinyConnector.cache = {}



    @staticmethod
    def _get_new_server(guild_id: int):
        server = Server()
        server.g_id = guild_id
        return server

    # create insert a new server object into the db
    @staticmethod
    def _init_guild(guild_id: int):
        if not TinyConnector.db.contains(TinyConnector.q.g_id == guild_id):

            server = TinyConnector._get_new_server(guild_id)
            TinyConnector._add_guild(server)


    @staticmethod
    def _delete_guild(guild_id: int):
        if TinyConnector.db.contains(TinyConnector.q.g_id == guild_id):
            TinyConnector.db.remove(TinyConnector.q.g_id == guild_id)


    @staticmethod
    def _add_guild(guild: Server):
        TinyConnector.db.insert(TinyConnector._obj_to_json(guild))



    @staticmethod
    def _obj_to_json(guild: Server):

        # modifications of balance poisons cache, when not done by value
        guild = copy.deepcopy(guild)

        # convert all decimals into strings
        # (balance)

        return dict({'g_id': guild.g_id,
                        'prefix': guild.prefix,
                        'incident_section_id': guild.incident_section_id,
                        'stewards_id': guild.stewards_id,
                        'statement_ch_id': guild.statement_ch_id,
                        'active_incidents': lib.data.incidents_to_json(guild.active_incidents)
                        })


    @staticmethod
    def _json_to_obj(db_json: dict()):
        # map json object to python class
        server = Server()
        server.g_id = db_json['g_id']
        server.prefix = db_json.get('prefix', '*')

        server.incident_section_id = db_json.get('incident_section_id', None)
        server.stewards_id = db_json.get('stewards_id', None)
        server.statement_ch_id = db_json.get('statement_ch_id', None)
        server.active_incidents = lib.data.json_to_incidents(db_json.get('active_incidents', {}))

        return server





    # only return the guild prefix
    # if guild is cached use this cache (equal performance to get_guild)
    # if NOT cached, do not convert str->Decimal (faster than copmlete conversion, but cache will not be updated)
    @staticmethod
    def get_guild_prefix(guild_id: int):

        if guild_id in TinyConnector.cache:
            return TinyConnector.cache[guild_id].prefix

        # does nothing when guild exists in db
        TinyConnector._init_guild(guild_id)
        db_json = TinyConnector.db.get(TinyConnector.q.g_id == guild_id)  # must return valid entry
        return db_json['prefix'] # prefix is guaranteed to be in the db


    # get the server object from db, creates new entry if not exists yet
    # guaranteed to return a object
    @staticmethod
    def get_guild(guild_id: int):
        # return cached obj
        if guild_id in TinyConnector.cache:
            # hand back copy
            # keeps db in sync with cache, all changes must occur over update_guild
            # this allows easy abort of functions on error, w/o corrupted datasets
            return copy.deepcopy(TinyConnector.cache[guild_id])

        TinyConnector._init_guild(guild_id)  # does nothing if guild exists in db
        db_json = TinyConnector.db.get(TinyConnector.q.g_id == guild_id)  # must return valid entry

        server = TinyConnector._json_to_obj(db_json)

        # save object in cache aswell and return it
        TinyConnector.cache[guild_id] = server
        return copy.deepcopy(server) # only copy


    # save changes to a server into the db
    # never use self-contsructed Server objects
    @staticmethod
    def update_guild(guild: Server):
        # update cache
        # but always update db aswell in case of program crash
        TinyConnector.cache[guild.g_id] = guild

        # user should only supply a Server object which he retrieved from get_guild
        guild_json = TinyConnector._obj_to_json(guild)
        guild_json.pop('g_id') # get the obj->json but remove the key of the server_id as this is the primary (sort of)
        TinyConnector.db.update(guild_json, TinyConnector.q.g_id == guild.g_id)
