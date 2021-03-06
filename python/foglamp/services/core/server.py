#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# FOGLAMP_BEGIN
# See: http://foglamp.readthedocs.io/
# FOGLAMP_END

"""Core server module"""

import asyncio
import os
import subprocess
import sys
import http.client
import json
import time
from aiohttp import web

from foglamp.common import logger
from foglamp.common.configuration_manager import ConfigurationManager
from foglamp.common.web import middleware
from foglamp.common.storage_client.exceptions import *
from foglamp.common.storage_client.storage_client import StorageClient
from foglamp.services.core import routes as admin_routes
from foglamp.services.common.microservice_management import routes as management_routes
from foglamp.services.core.service_registry.service_registry import ServiceRegistry
from foglamp.services.core.service_registry import exceptions as service_registry_exceptions
from foglamp.services.core.interest_registry.interest_registry import InterestRegistry
from foglamp.services.core.interest_registry import exceptions as interest_registry_exceptions
from foglamp.services.core.scheduler.scheduler import Scheduler
from foglamp.services.core.service_registry.monitor import Monitor
from foglamp.services.core import connect
from foglamp.services.common.service_announcer import ServiceAnnouncer

__author__ = "Amarendra K. Sinha, Praveen Garg, Terris Linenbach"
__copyright__ = "Copyright (c) 2017 OSIsoft, LLC"
__license__ = "Apache 2.0"
__version__ = "${VERSION}"

_logger = logger.setup(__name__, level=20)

# FOGLAMP_ROOT env variable
_FOGLAMP_ROOT= os.getenv("FOGLAMP_ROOT", default='/usr/local/foglamp')
_SCRIPTS_DIR= os.path.expanduser(_FOGLAMP_ROOT + '/scripts')

class Server:
    """ FOGLamp core server.

     Starts the FogLAMP REST server, storage and scheduler
    """

    scheduler = None
    """ foglamp.core.Scheduler """

    service_monitor = None
    """ foglamp.microservice_management.service_registry.Monitor """

    admin_announcer = None
    """ The Announcer for the Admin API """

    user_announcer = None
    """ The Announcer for the Admin API """

    management_announcer = None
    """ The Announcer for the management API """

    _host = '0.0.0.0'
    """ Host IP of core """

    core_management_port = 0
    """ Microservice management port of core """

    # TODO: FOGL-655 Get Admin API port from configuration option
    rest_service_port = 8081
    """ Admin service port of core """

    _start_time = time.time()
    """ Start time of core process """

    _storage_client = None
    """ Storage client to storage service """

    _configuration_manager = None
    """ Instance of configuration manager (singleton) """

    _interest_registry = None
    """ Instance of interest registry (singleton) """

    @staticmethod
    def _make_app():
        """Creates the REST server

        :rtype: web.Application
        """
        app = web.Application(middlewares=[middleware.error_middleware])
        admin_routes.setup(app)
        return app

    @classmethod
    def _make_core_app(cls):
        """Creates the Service management REST server Core a.k.a. service registry

        :rtype: web.Application
        """
        app = web.Application(middlewares=[middleware.error_middleware])
        management_routes.setup(app, cls)
        return app

    @classmethod
    async def _start_service_monitor(cls):
        """Starts the micro-service monitor"""
        cls.service_monitor = Monitor()
        await cls.service_monitor.start()

    @classmethod
    async def stop_service_monitor(cls):
        """Stops the micro-service monitor"""
        await cls.service_monitor.stop()

    @classmethod
    async def _start_scheduler(cls):
        """Starts the scheduler"""
        _logger.info("start scheduler")
        cls.scheduler = Scheduler(cls._host, cls.core_management_port)
        await cls.scheduler.start()

    @staticmethod
    def __start_storage(host, m_port):
        _logger.info("start storage, from directory %s", _SCRIPTS_DIR)
        try:
            cmd_with_args = ['./services/storage', '--address={}'.format(host),
                             '--port={}'.format(m_port)]
            subprocess.call(cmd_with_args, cwd=_SCRIPTS_DIR)
        except Exception as ex:
            _logger.exception(str(ex))

    @classmethod
    async def _start_storage(cls, loop):
        if loop is None:
            loop = asyncio.get_event_loop()
            # callback with args
        loop.call_soon(cls.__start_storage, cls._host, cls.core_management_port)

    @classmethod
    async def _get_storage_client(cls):
        storage_service = None
        while storage_service is None and cls._storage_client is None:
            try:
                found_services = ServiceRegistry.get(name="FogLAMP Storage")
                storage_service = found_services[0]
                cls._storage_client = StorageClient(cls._host, cls.core_management_port, svc=storage_service)
            except (service_registry_exceptions.DoesNotExist, InvalidServiceInstance, StorageServiceUnavailable, Exception) as ex:
                await asyncio.sleep(5)

    @classmethod
    def _start_app(cls, loop, app, host, port):
        if loop is None:
            loop = asyncio.get_event_loop()

        handler = app.make_handler()
        coro = loop.create_server(handler, host, port)
        # added coroutine
        server = loop.run_until_complete(coro)
        return server, handler

    @classmethod
    def _start_core(cls, loop=None):
        _logger.info("start core")

        try:
            host = cls._host

            core_app = cls._make_core_app()
            core_server, core_server_handler = cls._start_app(loop, core_app, host, 0)
            address, cls.core_management_port = core_server.sockets[0].getsockname()
            _logger.info('Management API started on http://%s:%s', address, cls.core_management_port)
            # see http://<core_mgt_host>:<core_mgt_port>/foglamp/service for registered services

            _logger.info('Announce management API service')
            cls.management_announcer = ServiceAnnouncer('FogLAMP-Core', '_foglamp_core._tcp', cls.core_management_port,
                                                    ['The FogLAMP Core REST API'])

            # start storage
            loop.run_until_complete(cls._start_storage(loop))
            
            # get storage client
            loop.run_until_complete(cls._get_storage_client())
            
            # obtain configuration manager and interest registry
            cls._configuration_manager = ConfigurationManager(cls._storage_client)
            cls._interest_registry = InterestRegistry(cls._configuration_manager)

            # start scheduler
            # see scheduler.py start def FIXME
            # scheduler on start will wait for storage service registration
            loop.run_until_complete(cls._start_scheduler())

            # start monitor
            loop.run_until_complete(cls._start_service_monitor())

            service_app = cls._make_app()
            service_server, service_server_handler = cls._start_app(loop, service_app, host, cls.rest_service_port)
            address, service_server_port = service_server.sockets[0].getsockname()
            _logger.info('Rest Server started on http://%s:%s', address, service_server_port)

            cls.admin_announcer = ServiceAnnouncer('FogLAMP', '_foglamp._tcp', service_server_port, ['The FogLAMP Admin REST API'])
            cls.user_announcer = ServiceAnnouncer('FogLAMP', '_foglamp_app._tcp', service_server_port,
                                              ['The FogLAMP Application  REST API'])
            # register core
            # a service with 2 web server instance,
            # registering now only when service_port is ready to listen the request
            cls._register_core(host, cls.core_management_port, cls.rest_service_port)
            print("(Press CTRL+C to quit)")

            try:
                loop.run_forever()
            except KeyboardInterrupt:
                pass
            finally:
                # graceful-shutdown
                # http://aiohttp.readthedocs.io/en/stable/web.html
                # TODO: FOGL-653 shutdown implementation
                # stop the scheduler
                loop.run_until_complete(cls._stop_scheduler())

                # stop monitor
                loop.run_until_complete(cls.stop_service_monitor())

                # stop the REST api (exposed on service port)
                service_server.close()
                loop.run_until_complete(service_server.wait_closed())
                loop.run_until_complete(service_app.shutdown())
                loop.run_until_complete(service_server_handler.shutdown(60.0))
                loop.run_until_complete(service_app.cleanup())

                # stop storage
                cls.stop_storage()

                # stop core management api
                core_server.close()
                loop.run_until_complete(core_server.wait_closed())
                loop.run_until_complete(core_app.shutdown())
                loop.run_until_complete(core_server_handler.shutdown(60.0))
                loop.run_until_complete(core_app.cleanup())

                loop.close()
        except (OSError, RuntimeError, TimeoutError) as e:
            sys.stderr.write('Error: ' + format(str(e)) + "\n")
            sys.exit(1)
        except Exception as e:
            sys.stderr.write('Error: ' + format(str(e)) + "\n")
            sys.exit(1)

    @classmethod
    def _register_core(cls, host, mgt_port, service_port):
        core_service_id = ServiceRegistry.register(name="FogLAMP Core", s_type="Core", address=host,
                                                     port=service_port, management_port=mgt_port)

        return core_service_id

    @classmethod
    def start(cls):
        """Starts the server"""

        loop = asyncio.get_event_loop()
        cls._start_core(loop=loop)

    @classmethod
    def stop_storage(cls):
        """ stop Storage service """

        # TODO: FOGL-653 shutdown implementation
        # remove me, and allow this call in service registry API

        found_services = ServiceRegistry.get(name="FogLAMP Storage")
        svc = found_services[0]
        if svc is None:
            return

        management_api_url = '{}:{}'.format(svc._address, svc._management_port)

        conn = http.client.HTTPConnection(management_api_url)
        # TODO: need to set http / https based on service protocol

        conn.request('POST', url='/foglamp/service/shutdown', body=None)
        r = conn.getresponse()

        # TODO: FOGL-615
        # log error with message if status is 4xx or 5xx
        if r.status in range(400, 500):
            _logger.error("Client error code: %d", r.status)
        if r.status in range(500, 600):
            _logger.error("Server error code: %d", r.status)

        res = r.read().decode()
        conn.close()
        return json.loads(res)

    @classmethod
    async def _stop_scheduler(cls):
        if cls.scheduler:
            try:
                await cls.scheduler.stop()
                cls.scheduler = None
            except TimeoutError:
                _logger.exception('Unable to stop the scheduler')
                return

    @classmethod
    async def ping(cls, request):
        """ health check

        """
        since_started = time.time() - cls._start_time
        return web.json_response({'uptime': since_started})

    @classmethod
    async def register(cls, request):
        """ Register a service

        :Example: curl -d '{"type": "Storage", "name": "Storage Services", "address": "127.0.0.1", "service_port": 8090,
                "management_port": 1090, "protocol": "https"}' -X POST http://localhost:8082/foglamp/service
        service_port is optional
        """

        try:
            data = await request.json()

            service_name = data.get('name', None)
            service_type = data.get('type', None)
            service_address = data.get('address', None)
            service_port = data.get('service_port', None)
            service_management_port = data.get('management_port', None)
            service_protocol = data.get('protocol', 'http')

            if not (service_name.strip() or service_type.strip() or service_address.strip()
                    or service_management_port.strip() or not service_management_port.isdigit()):
                raise web.HTTPBadRequest(reason='One or more values for type/name/address/management port missing')

            if service_port is not None:
                if not (isinstance(service_port, int)):
                    raise web.HTTPBadRequest(reason="Service's service port can be a positive integer only")

            if not isinstance(service_management_port, int):
                raise web.HTTPBadRequest(reason='Service management port can be a positive integer only')

            try:
                registered_service_id = ServiceRegistry.register(service_name, service_type, service_address,
                                                                   service_port, service_management_port, service_protocol)
            except service_registry_exceptions.AlreadyExistsWithTheSameName:
                raise web.HTTPBadRequest(reason='A Service with the same name already exists')
            except service_registry_exceptions.AlreadyExistsWithTheSameAddressAndPort:
                raise web.HTTPBadRequest(reason='A Service is already registered on the same address: {} and '
                                                'service port: {}'.format(service_address, service_port))
            except service_registry_exceptions.AlreadyExistsWithTheSameAddressAndManagementPort:
                raise web.HTTPBadRequest(reason='A Service is already registered on the same address: {} and '
                                                'management port: {}'.format(service_address, service_management_port))

            if not registered_service_id:
                raise web.HTTPBadRequest(reason='Service {} could not be registered'.format(service_name))

            _response = {
                'id': registered_service_id,
                'message': "Service registered successfully"
            }

            return web.json_response(_response)

        except ValueError as ex:
            raise web.HTTPNotFound(reason=str(ex))


    @classmethod
    async def unregister(cls, request):
        """ Unregister a service

        :Example: curl -X DELETE  http://localhost:8082/foglamp/service/dc9bfc01-066a-4cc0-b068-9c35486db87f
        """

        try:
            service_id = request.match_info.get('service_id', None)

            if not service_id:
                raise web.HTTPBadRequest(reason='Service id is required')

            try:
                ServiceRegistry.get(idx=service_id)
            except service_registry_exceptions.DoesNotExist:
                raise web.HTTPBadRequest(reason='Service with {} does not exist'.format(service_id))

            ServiceRegistry.unregister(service_id)

            _resp = {'id': str(service_id), 'message': 'Service unregistered'}

            return web.json_response(_resp)
        except ValueError as ex:
            raise web.HTTPNotFound(reason=str(ex))


    @classmethod
    async def get_service(cls, request):
        """ Returns a list of all services or of the selected service

        :Example: curl -X GET  http://localhost:8082/foglamp/service
        :Example: curl -X GET  http://localhost:8082/foglamp/service?name=X&type=Storage
        """
        service_name = request.query['name'] if 'name' in request.query else None
        service_type = request.query['type'] if 'type' in request.query else None

        try:
            if not service_name and not service_type:
                services_list = ServiceRegistry.all()
            elif service_name and not service_type:
                services_list = ServiceRegistry.get(name=service_name)
            elif not service_name and service_type:
                services_list = ServiceRegistry.get(s_type=service_type)
            else:
                services_list = ServiceRegistry.filter_by_name_and_type(
                        name=service_name, s_type=service_type
                    )
        except service_registry_exceptions.DoesNotExist as ex:
            raise web.HTTPBadRequest(reason="Invalid service name and/or type provided" + str(ex))

        services = []
        for service in services_list:
            svc = dict()
            svc["id"] = service._id
            svc["name"] = service._name
            svc["type"] = service._type
            svc["address"] = service._address
            svc["management_port"] = service._management_port
            svc["protocol"] = service._protocol
            svc["status"] =  service._status
            if service._port:
                svc["service_port"] = service._port
            services.append(svc)

        return web.json_response({"services": services})


    @classmethod
    async def shutdown(cls, request):
        pass

    @classmethod
    async def register_interest(cls, request):
        """ Register an interest in a configuration category

        :Example: curl -d '{"category": "COAP", "service": "x43978x8798x"}' -X POST http://localhost:8082/foglamp/interest
        """

        try:
            data = await request.json()
            category_name = data.get('category', None)
            microservice_uuid = data.get('service', None)

            if not (category_name.strip() or microservice_uuid.strip()):
                raise web.HTTPBadRequest(reason='One or more values of category_name, service missing')

            try:
                registered_interest_id = cls._interest_registry.register(microservice_uuid, category_name)
            except interest_registry_exceptions.ErrorInterestRegistrationAlreadyExists:
                raise web.HTTPBadRequest(reason='An InterestRecord already exists by microservice_uuid {} for category_name {}'.format(microservice_uuid, category_name))

            if not registered_interest_id:
                raise web.HTTPBadRequest(reason='Interest by microservice_uuid {} for category_name {} could not be registered'.format(microservice_uuid, category_name))

            _response = {
                'id': registered_interest_id,
                'message': "Interest registered successfully"
            }

            return web.json_response(_response)

        except ValueError as ex:
            raise web.HTTPNotFound(reason=str(ex))



    @classmethod
    async def unregister_interest(cls, request):
        """ Unregister an interest

        :Example: curl -X DELETE  http://localhost:8082/foglamp/interest/dc9bfc01-066a-4cc0-b068-9c35486db87f
        """

        try:
            interest_registration_id = request.match_info.get('interest_id', None)

            if not interest_registration_id:
                raise web.HTTPBadRequest(reason='Registration id is required')

            try:
                cls._interest_registry.get(registration_id=interest_registration_id)
            except interest_registry_exceptions.DoesNotExist:
                raise web.HTTPBadRequest(reason='InterestRecord with registration_id {} does not exist'.format(interest_registration_id))

            cls._interest_registry.unregister(interest_registration_id)

            _resp = {'id': str(interest_registration_id), 'message': 'Interest unregistered'}

            return web.json_response(_resp)
        except ValueError as ex:
            raise web.HTTPNotFound(reason=str(ex))


    @classmethod
    async def change(cls, request):
        pass


def main():
    """ Processes command-line arguments
           COMMAND LINE ARGUMENTS:
               - start
               - stop

           :raises ValueError: Invalid or missing arguments provided
           """

    if len(sys.argv) == 1:
        raise ValueError("Usage: start|stop")
    elif len(sys.argv) == 2:
        command = sys.argv[1]
        if command == 'start':
            Server().start()
        elif command == 'stop':
            Server().stop_storage()
            Server().stop_service_monitor()
            # scheduler has signal binding
        else:
            raise ValueError("Unknown argument: {}".format(sys.argv[1]))
