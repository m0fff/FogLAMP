# -*- coding: utf-8 -*-

# FOGLAMP_BEGIN
# See: http://foglamp.readthedocs.io/
# FOGLAMP_END

import json
import asyncpg
import http.client
import pytest
import asyncio
from datetime import datetime, timezone

from foglamp.services.core.api.backup_restore import Status

__author__ = "Vaibhav Singhal"
__copyright__ = "Copyright (c) 2017 OSIsoft, LLC"
__license__ = "Apache 2.0"
__version__ = "${VERSION}"

# TODO : Use storage layer
# Module attributes
__DB_NAME = "foglamp"
BASE_URL = 'localhost:8081'
headers = {"Content-Type": 'application/json'}

pytestmark = pytest.mark.asyncio

test_data = [{'filename': 'test_file1', 'ts': datetime.now(tz=timezone.utc), 'type': 0, 'status': 1},
             {'filename': 'test_file2', 'ts': datetime.now(tz=timezone.utc), 'type': 0, 'status': 2},
             {'filename': 'test_file3', 'ts': datetime.now(tz=timezone.utc), 'type': 1, 'status': 5},
             {'filename': 'test_file4', 'ts': datetime.now(tz=timezone.utc), 'type': 0, 'status': 2}]


async def add_master_data():
    """Inserts master data into backup table and returns the ids of inserted items"""
    conn = await asyncpg.connect(database=__DB_NAME)
    for item in test_data:
        await conn.execute("""INSERT INTO foglamp.backups(file_name,ts,type,status)
        VALUES($1, $2, $3, $4);""", item['filename'], item['ts'], item['type'], item['status'])
        res = await conn.fetchval('''SELECT id from foglamp.backups WHERE file_name IN ($1)''', item['filename'])
        # test_data.append({item['filename']: res})
        item.update({"id": res})
    await conn.close()


async def delete_master_data():
    """
    Delete test data records from backup table
    """
    conn = await asyncpg.connect(database=__DB_NAME)
    await conn.execute('''DELETE from foglamp.backups WHERE file_name LIKE ($1)''', 'test_%')
    await conn.close()


def setup_module(module):
    """
    Create backup files in db, directory (if required)
    """
    asyncio.get_event_loop().run_until_complete(add_master_data())


def teardown_module(module):
    """
    Delete the created files from backup db, directory (if created)
    """
    asyncio.get_event_loop().run_until_complete(delete_master_data())


@pytest.allure.feature("api")
@pytest.allure.story("backup")
class TestBackup:

    @pytest.mark.parametrize("request_params, response_code, exp_length, exp_output", [
        ('', 200, 4, test_data),
        ('?limit=1', 200, 1, [test_data[3]]),
        ('?skip=3', 200, 1, [test_data[0]]),
        ('?limit=2&skip=1', 200, 2, test_data[1:]),
        ('?status=failed', 200, 1, [test_data[2]]),
        ('?limit=2&skip=1&status=completed', 200, 1, [test_data[1]]),
    ])
    async def test_get_backups_valid(self, request_params, response_code, exp_length, exp_output):
        """
        Test to get all backups, where:
        1. No request parameter is passed
        2. valid limit is specified
        3. valid skip is specified
        4. valid limit and skip is specified
        5. valid status is specified
        6. valid limit, skip and status is specified
        There can be multiple records in return, test asserts if test data is present in return or not
        """
        conn = http.client.HTTPConnection(BASE_URL)
        conn.request("GET", '/foglamp/backup{}'.format(request_params))
        r = conn.getresponse()
        assert response_code == r.status
        r = r.read().decode()
        retval = json.loads(r)
        response_length = len(retval['backups'])
        assert exp_length == response_length
        count = 0
        for i in range(response_length):
            count += 1
            assert exp_output[exp_length - count]['id'] == retval['backups'][i]['id']
            assert Status(exp_output[exp_length - count]["status"]).name == retval['backups'][i]['status']
            assert retval['backups'][i]['date'] is not None
        conn.close()

    @pytest.mark.parametrize("request_params, response_code, output_code, output_message", [
        ('?limit=invalid', 200, 400, "limit must be an integer"),
        ('?skip=invalid', 200, 400, "skip must be an integer"),
        ('?status=invalid', 200, 400, "'INVALID' not a valid status"),
    ])
    async def test_get_backups_invalid(self, request_params, response_code, output_code, output_message):
        """
        Test to get all backups, where:
        1. invalid limit is specified
        2. invalid skip is specified
        3. invalid status is specified
        """
        conn = http.client.HTTPConnection(BASE_URL)
        conn.request("GET", '/foglamp/backup{}'.format(request_params))
        r = conn.getresponse()
        assert response_code == r.status
        r = r.read().decode()
        retval = json.loads(r)
        assert output_code == retval['error']['code']
        assert output_message == retval['error']['message']
        conn.close()

    @pytest.mark.parametrize("request_params, response_code, output", [
        (test_data[0], 200, test_data[0])
    ])
    async def test_get_backup_details_valid(self, request_params, response_code, output):
        """
        Test to get details of backup, where:
        1. Valid backup id is specified as query parameter
        """
        conn = http.client.HTTPConnection(BASE_URL)
        conn.request("GET", '/foglamp/backup/{}'.format(request_params["id"]))
        r = conn.getresponse()
        assert response_code == r.status
        r = r.read().decode()
        retval = json.loads(r)
        assert output['id'] == retval['id']
        assert Status(output['status']).name == retval['status']
        assert retval['date'] is not None
        conn.close()

    @pytest.mark.parametrize("request_params, response_code, output_code, output_message", [
        ('invalid', 200, 400, "Invalid backup id"),
        ('-1', 200, 404, "Backup with -1 does not exist")
    ])
    async def test_get_backup_details_invalid(self, request_params, response_code, output_code, output_message):
        """
        Test to get details of backup, where:
        1. Invalid backup id is specified as query parameter
        """
        conn = http.client.HTTPConnection(BASE_URL)
        conn.request("GET", '/foglamp/backup/{}'.format(request_params))
        r = conn.getresponse()
        assert response_code == r.status
        r = r.read().decode()
        retval = json.loads(r)
        assert output_code == retval['error']['code']
        assert output_message == retval['error']['message']
        conn.close()

    # TODO: Create mocks for this
    @pytest.mark.skip(reason="FOGL-865")
    async def test_create_backup(self):
        """
        Test checks the api call to create a backup, Use mocks and do not backup as backup is a time consuming process
        """
        pass

    # TODO: Create mocks for this
    @pytest.mark.skip(reason="FOGL-865")
    async def test_delete_backup(self):
        """
        Test checks the api call to delete a backup, Use Mocks and test data files as point of removal, do not delete
        an actual backup, scenarios:
        1. Invalid backup id is specified as query parameter
        2. Valid backup id is specified as query parameter
        """
        pass


@pytest.allure.feature("api")
@pytest.allure.story("restore")
class TestRestore:

    @pytest.mark.skip(reason="FOGL-861")
    async def test_restore(self):
        """
        Test checks the api call to restore a backup, Use mocks and do not restore as it is a time consuming process
        an actual backup, scenarios:
        1. Invalid backup id is specified as query parameter
        2. Valid backup id is specified as query parameter
        """
        pass
