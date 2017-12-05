# -*- coding: utf-8 -*-

# FOGLAMP_BEGIN
# See: http://foglamp.readthedocs.io/
# FOGLAMP_END

""" Module for Sensortag CC2650 'poll' type plugin """

import copy
import datetime
import uuid

import asyncio

from foglamp.plugins.south.common.sensortag_cc2650 import *
from foglamp.common.parser import Parser
from foglamp.services.south import exceptions
from foglamp.common import logger

__author__ = "Amarendra K Sinha"
__copyright__ = "Copyright (c) 2017 OSIsoft, LLC"
__license__ = "Apache 2.0"
__version__ = "${VERSION}"

_DEFAULT_CONFIG = {
    'plugin': {
         'description': 'Python module name of the plugin to load',
         'type': 'string',
         'default': 'cc2650poll'
    },
    'pollInterval': {
        'description': 'The interval between poll calls to the device poll routine expressed in milliseconds.',
        'type': 'integer',
        'default': '500'
    },
    'bluetooth_address': {
        'description': 'Bluetooth MAC address',
        'type': 'string',
        'default': 'B0:91:22:EA:79:04'
    }
}

_LOGGER = logger.setup(__name__, level=20)

sensortag_characteristics = characteristics


def plugin_info():
    """ Returns information about the plugin.

    Args:
    Returns:
        dict: plugin information
    Raises:
    """

    return {
        'name': 'Async plugin',
        'version': '1.0',
        'mode': 'async',
        'type': 'device',
        'interface': '1.0',
        'config': _DEFAULT_CONFIG
    }


def plugin_init(config):
    """ Initialise the plugin.

    Args:
        config: JSON configuration document for the device configuration category
    Returns:
        handle: JSON object to be used in future calls to the plugin
    Raises:
    """
    global sensortag_characteristics

    bluetooth_adr = config['bluetooth_address']['value']
    tag = SensorTagCC2650(bluetooth_adr)

    # The GATT table can change for different firmware revisions, so it is important to do a proper characteristic
    # discovery rather than hard-coding the attribute handles.
    for char in sensortag_characteristics.keys():
        for type in ['data', 'configuration', 'period']:
            handle = tag.get_char_handle(sensortag_characteristics[char][type]['uuid'])
            sensortag_characteristics[char][type]['handle'] = handle

    # print(json.dumps(sensortag_characteristics))

    data = copy.deepcopy(config)
    data['characteristics'] = sensortag_characteristics
    data['bluetooth_adr'] = bluetooth_adr

    _LOGGER.info('SensorTagCC2650 {} Polling initialized'.format(bluetooth_adr))

    return data


def plugin_start(handle):
    """ Extracts data from the sensor and returns it in a JSON document as a Python dict.

    Available for poll mode only.

    Args:
        handle: handle returned by the plugin initialisation call
    Returns:
        returns a sensor reading in a JSON document, as a Python dict, if it is available
        None - If no reading is available
    Raises:
        DataRetrievalError
    """
    inputs = list()
    incoming = list()
    event = asyncio.Event()

    async def save_data(event):
        global inputs
        await event.wait()
        print("==============> ", len(inputs))
        # for input in inputs:
        #     await handle['ingest'].add_readings(asset=input['asset'],
        #                                                         timestamp=input['timestamp'],
        #                                                         key=input['key'],
        #                                                         readings=input['readings'])
        event.clear()

    asyncio.ensure_future(save_data(event))

    try:
        bluetooth_adr = handle['bluetooth_adr']
        object_temp_celsius = None
        ambient_temp_celsius = None
        lux_luminance = None
        rel_humidity = None
        bar_pressure = None
        movement = None

        tag = SensorTagCC2650(bluetooth_adr)  # pass the Bluetooth Address

        # Enable notification
        tag.char_write_cmd(tag.get_notification_handle(handle['characteristics']['temperature']['data']['handle']), notf_enable)
        tag.char_write_cmd(tag.get_notification_handle(handle['characteristics']['luminance']['data']['handle']), notf_enable)
        tag.char_write_cmd(tag.get_notification_handle(handle['characteristics']['humidity']['data']['handle']), notf_enable)
        tag.char_write_cmd(tag.get_notification_handle(handle['characteristics']['pressure']['data']['handle']), notf_enable)
        # tag.char_write_cmd(tag.get_notification_handlehandle['characteristics']['movement']['data']['handle']), notf_enable)

        # Enable sensors
        tag.char_write_cmd(handle['characteristics']['temperature']['configuration']['handle'], char_enable)
        tag.char_write_cmd(handle['characteristics']['luminance']['configuration']['handle'], char_enable)
        tag.char_write_cmd(handle['characteristics']['humidity']['configuration']['handle'], char_enable)
        tag.char_write_cmd(handle['characteristics']['pressure']['configuration']['handle'], char_enable)
        # tag.char_write_cmd(handle['characteristics']['movement']['configuration']['handle'], char_enable)

        # TODO: How to implement CTRL-C or terminate process?
        while True:
            time_stamp = str(datetime.datetime.now(tz=datetime.timezone.utc))
            try:
                pnum = tag.con.expect('Notification handle = .*? \r', timeout=4)
            except pexpect.TIMEOUT:
                print("TIMEOUT exception!")
                break
            if pnum == 0:
                after = tag.con.after
                hxstr = after.split()[3:]
                print("****", hxstr, event.is_set())
                # Get temperature
                if int(handle['characteristics']['temperature']['data']['handle'], 16) == int(hxstr[0].decode(), 16):
                    object_temp_celsius, ambient_temp_celsius = tag.hexTemp2C(tag.get_raw_measurement("temperature", hxstr))
                    data = {
                        'asset': 'TI sensortag/temperature',
                        'timestamp': time_stamp,
                        'key': str(uuid.uuid4()),
                        'readings': {
                            'temperature': {
                                "object": object_temp_celsius,
                                'ambient': ambient_temp_celsius
                            },
                        }
                    }

                # Get luminance
                if int(handle['characteristics']['luminance']['data']['handle'], 16) == int(hxstr[0].decode(), 16):
                    lux_luminance = tag.hexLum2Lux(tag.get_raw_measurement("luminance", hxstr))
                    data = {
                        'asset': 'TI sensortag/luxometer',
                        'timestamp': time_stamp,
                        'key': str(uuid.uuid4()),
                        'readings': {
                            'luxometer': {"lux": lux_luminance},
                        }
                    }

                # Get humidity
                if int(handle['characteristics']['humidity']['data']['handle'], 16) == int(hxstr[0].decode(), 16):
                    rel_humidity, rel_temperature = tag.hexHum2RelHum(tag.get_raw_measurement("humidity", hxstr))
                    data = {
                        'asset': 'TI sensortag/humidity',
                        'timestamp': time_stamp,
                        'key': str(uuid.uuid4()),
                        'readings': {
                            'humidity': {
                                "humidity": rel_humidity,
                                "temperature": rel_temperature
                            },
                        }
                    }

                # Get pressure
                if int(handle['characteristics']['pressure']['data']['handle'], 16) == int(hxstr[0].decode(), 16):
                    bar_pressure = tag.hexPress2Press(tag.get_raw_measurement("pressure", hxstr))
                    data = {
                        'asset': 'TI sensortag/pressure',
                        'timestamp': time_stamp,
                        'key': str(uuid.uuid4()),
                        'readings': {
                            'pressure': {"pressure": bar_pressure},
                        }
                    }

                # TODO: Implement movement data capture
                # Get movement

                incoming.append(data)
                if len(incoming) >= 50:
                    inputs = copy.deepcopy(incoming)
                    incoming = list()
                    event.set()
            else:
                print("TIMEOUT!!")
    except Exception as ex:
        _LOGGER.exception("SensorTagCC2650 {} async exception: {}".format(bluetooth_adr, str(ex)))
        raise exceptions.DataRetrievalError(ex)

    _LOGGER.info("SensorTagCC2650 {} async reading: {}".format(bluetooth_adr, json.dumps(data)))
    return data


def plugin_reconfigure(handle, new_config):
    """ Reconfigures the plugin, it should be called when the configuration of the plugin is changed during the
        operation of the device service.
        The new configuration category should be passed.

    Args:
        handle: handle returned by the plugin initialisation call
        new_config: JSON object representing the new configuration category for the category
    Returns:
        new_handle: new handle to be used in the future calls
    Raises:
    """

    new_handle = {}

    return new_handle


def plugin_shutdown(handle):
    """ Shutdowns the plugin doing required cleanup, to be called prior to the device service being shut down.

    Args:
        handle: handle returned by the plugin initialisation call
    Returns:
    Raises:
    """
    bluetooth_adr = handle['bluetooth_adr']
    tag = SensorTagCC2650(bluetooth_adr)  # pass the Bluetooth Address

    # Disable sensors
    tag.char_write_cmd(handle['characteristics']['temperature']['configuration']['handle'], char_disable)
    tag.char_write_cmd(handle['characteristics']['luminance']['configuration']['handle'], char_disable)
    tag.char_write_cmd(handle['characteristics']['humidity']['configuration']['handle'], char_disable)
    tag.char_write_cmd(handle['characteristics']['pressure']['configuration']['handle'], char_disable)
    # tag.char_write_cmd(handle['characteristics']['movement']['configuration']['handle'], char_disable)

    # Disable notification
    tag.char_write_cmd(tag.get_notification_handle(handle['characteristics']['temperature']['configuration']['handle']),
                       notf_disable)
    tag.char_write_cmd(tag.get_notification_handle(handle['characteristics']['luminance']['configuration']['handle']),
                       notf_disable)
    tag.char_write_cmd(tag.get_notification_handle(handle['characteristics']['humidity']['configuration']['handle']),
                       notf_disable)
    tag.char_write_cmd(tag.get_notification_handle(handle['characteristics']['pressure']['configuration']['handle']),
                       notf_disable)
    # tag.char_write_cmd(tag.get_notification_handlehandle['characteristics']['movement']['configuration']['handle']), notf_enable)

    _LOGGER.info('SensorTagCC2650 {} async shutdown'.format(bluetooth_adr))


if __name__ == "__main__":
    # To run: python3 python/foglamp/plugins/south/cc2650async/cc2650async.py --bluetooth_adr=B0:91:22:EA:79:04

    # bluetooth_adr = sys.argv[1]
    # print(plugin_init({'bluetooth_adr': bluetooth_adr}))
    plugin_start(plugin_init({}))

    # tag = SensorTagCC2650(bluetooth_adr)
    # handle = tag.get_char_handle(characteristics['temperature']['data']['uuid'])
    # print(handle)
