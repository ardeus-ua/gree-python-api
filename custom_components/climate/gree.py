import asyncio
import logging
import binascii
import socket
import os.path
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from homeassistant.components.climate import (DOMAIN, ClimateDevice, PLATFORM_SCHEMA, STATE_IDLE, STATE_HEAT, STATE_COOL, STATE_AUTO, STATE_DRY,
SUPPORT_OPERATION_MODE, SUPPORT_TARGET_TEMPERATURE, SUPPORT_FAN_MODE, SUPPORT_SWING_MODE)
from homeassistant.const import (ATTR_UNIT_OF_MEASUREMENT, ATTR_TEMPERATURE, CONF_NAME, CONF_HOST, CONF_MAC, CONF_TIMEOUT, CONF_CUSTOMIZE)
from homeassistant.helpers.event import (async_track_state_change)
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from configparser import ConfigParser
from base64 import b64encode, b64decode

REQUIREMENTS = ['gree==0.3.2']

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE | SUPPORT_FAN_MODE | SUPPORT_SWING_MODE

CONF_UNIQUE_KEY = 'unique_key'
CONF_MIN_TEMP = 'min_temp'
CONF_MAX_TEMP = 'max_temp'
CONF_TARGET_TEMP = 'target_temp'
CONF_TEMP_SENSOR = 'temp_sensor'
CONF_OPERATIONS = 'operations'
CONF_FAN_MODES = 'fan_modes'
CONF_SWING_LIST = 'swing_list'
CONF_DEFAULT_OPERATION = 'default_operation'
CONF_DEFAULT_FAN_MODE = 'default_fan_mode'
CONF_DEFAULT_SWING_MODE = 'default_swing_mode'

CONF_DEFAULT_OPERATION_FROM_IDLE = 'default_operation_from_idle'

STATE_FAN = 'fan'
STATE_OFF = 'off'

DEFAULT_NAME = 'GREE AC Climate'
DEFAULT_TIMEOUT = 10
DEFAULT_RETRY = 3
DEFAULT_MIN_TEMP = 16
DEFAULT_MAX_TEMP = 30
DEFAULT_TARGET_TEMP = 20
DEFAULT_OPERATION_LIST = [STATE_OFF, STATE_AUTO, STATE_COOL, STATE_DRY, STATE_FAN, STATE_HEAT]
OPERATION_LIST_MAP = {
    STATE_AUTO: 0,
    STATE_COOL: 1,
    STATE_DRY: 2,
    STATE_FAN: 3,
    STATE_HEAT: 4,
}
DEFAULT_FAN_MODE_LIST = ['auto', 'low', 'medium-low', 'medium', 'medium-high', 'high']
FAN_MODE_MAP = {
    'auto': 0,
    'low': 1,
    'medium-low': 2,
    'medium': 3,
    'medium-high': 4,
    'high': 5
}
DEFAULT_SWING_LIST = ['default', 'swing-full-range', 'fixed-up', 'fixed-middle', 'fixed-down', 'swing-up', 'swing-middle', 'swing-down']
SWING_MAP = {
    'default': 0,
    'swing-full-range': 1,
    'fixed-up': 2,
    'fixed-middle': 4,
    'fixed-down': 6,
    'swing-up': 11,
    'swing-middle': 9,
    'swing-down': 7
}
DEFAULT_OPERATION = 'idle'
DEFAULT_FAN_MODE = 'auto'
DEFAULT_SWING_MODE = 'default'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_MAC): cv.string,
    vol.Required(CONF_UNIQUE_KEY): cv.string,
    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int, 
    vol.Optional(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): cv.positive_int,
    vol.Optional(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): cv.positive_int,
    vol.Optional(CONF_TARGET_TEMP, default=DEFAULT_TARGET_TEMP): cv.positive_int,
    vol.Optional(CONF_TEMP_SENSOR): cv.entity_id,
    vol.Optional(CONF_DEFAULT_OPERATION, default=DEFAULT_OPERATION): cv.string,
    vol.Optional(CONF_DEFAULT_FAN_MODE, default=DEFAULT_FAN_MODE): cv.string,
    vol.Optional(CONF_DEFAULT_SWING_MODE, default=DEFAULT_SWING_MODE): cv.string,
    vol.Optional(CONF_DEFAULT_OPERATION_FROM_IDLE): cv.string
})

@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the GREE platform."""
    name = config.get(CONF_NAME)
    ip_addr = config.get(CONF_HOST)
    mac_addr = config.get(CONF_MAC)
    unique_key = config.get(CONF_UNIQUE_KEY).encode()
      
    min_temp = config.get(CONF_MIN_TEMP)
    max_temp = config.get(CONF_MAX_TEMP)
    target_temp = config.get(CONF_TARGET_TEMP)
    temp_sensor_entity_id = config.get(CONF_TEMP_SENSOR)
    operation_list = DEFAULT_OPERATION_LIST
    swing_list = DEFAULT_SWING_LIST
    fan_list = DEFAULT_FAN_MODE_LIST
    default_operation = config.get(CONF_DEFAULT_OPERATION)
    default_fan_mode = config.get(CONF_DEFAULT_FAN_MODE)
    default_swing_mode = config.get(CONF_DEFAULT_SWING_MODE)
    
    default_operation_from_idle = config.get(CONF_DEFAULT_OPERATION_FROM_IDLE)
    
    import gree

    gree_device = gree.GreeDevice(mac_addr, unique_key, ip_addr)

    try:
        gree_device.update_status()
    except socket.timeout:
        _LOGGER.error("Failed to connect to Gree Device")
    
    async_add_devices([
        GreeClimate(hass, name, gree_device, min_temp, max_temp, target_temp, temp_sensor_entity_id, operation_list, fan_list, swing_list, default_operation, default_fan_mode, default_swing_mode, default_operation_from_idle)
    ])

    ATTR_VALUE = 'value'
    DEFAULT_VALUE = True

    def gree_set_health(call):
        value = call.data.get(ATTR_VALUE, DEFAULT_VALUE)

        gree_device.send_command(health_mode=bool(value))

    hass.services.async_register(DOMAIN, 'gree_set_health', gree_set_health)

class GreeClimate(ClimateDevice):

    def __init__(self, hass, name, gree_device, min_temp, max_temp, target_temp, temp_sensor_entity_id, operation_list, fan_list, swing_list, default_operation, default_fan_mode, default_swing_mode, default_operation_from_idle):
                 
        """Initialize the Gree Climate device."""
        self.hass = hass
        self._name = name

        self._min_temp = min_temp
        self._max_temp = max_temp
        self._target_temperature = target_temp
        self._target_temperature_step = 1
        self._unit_of_measurement = hass.config.units.temperature_unit
        
        self._current_temperature = 0
        self._temp_sensor_entity_id = temp_sensor_entity_id

        self._current_operation = default_operation
        self._current_fan_mode = default_fan_mode
        self._current_swing_mode = default_swing_mode
        
        self._operation_list = operation_list
        self._fan_list = fan_list
        self._swing_list = swing_list
        
        self._default_operation_from_idle = default_operation_from_idle
                
        self._gree_device = gree_device
        
        if temp_sensor_entity_id:
            async_track_state_change(
                hass, temp_sensor_entity_id, self._async_temp_sensor_changed)
                
            sensor_state = hass.states.get(temp_sensor_entity_id)    
                
            if sensor_state:
                self._async_update_current_temp(sensor_state)

    def send_command(self):
        power = True
        mode = None
        operation = self._current_operation.lower()
        
        if operation == 'off':
            power = False
        else: 
            mode = OPERATION_LIST_MAP[operation]

        fan_speed = FAN_MODE_MAP[self._current_fan_mode.lower()]
        temperature = self._target_temperature
        swing = SWING_MAP[self._current_swing_mode.lower()]

        for retry in range(DEFAULT_RETRY):
            try:
                self._gree_device.send_command(power_on=power, temperature=temperature, fan_speed=fan_speed, mode=mode, swing=swing)
            except (socket.timeout, ValueError):
                try:
                    self._gree_device.update_status()
                except socket.timeout:
                    if retry == DEFAULT_RETRY-1:
                        _LOGGER.error("Failed to send command to Gree Device")

    @asyncio.coroutine
    def _async_temp_sensor_changed(self, entity_id, old_state, new_state):
        """Handle temperature changes."""
        if new_state is None:
            return

        self._async_update_current_temp(new_state)
        yield from self.async_update_ha_state()
        
    @callback
    def _async_update_current_temp(self, state):
        """Update thermostat with latest state from sensor."""
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        try:
            _state = state.state
            if self.represents_float(_state):
                self._current_temperature = self.hass.config.units.temperature(
                    float(_state), unit)
        except ValueError as ex:
            _LOGGER.error('Unable to update from sensor: %s', ex)    

    def represents_float(self, s):
        try: 
            float(s)
            return True
        except ValueError:
            return False     
    
    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._name

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature
        
    @property
    def min_temp(self):
        """Return the polling state."""
        return self._min_temp
        
    @property
    def max_temp(self):
        """Return the polling state."""
        return self._max_temp    
        
    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature
        
    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self._target_temperature_step

    @property
    def current_operation(self):
        """Return current operation ie. heat, cool, idle."""
        return self._current_operation

    @property
    def operation_list(self):
        """Return the list of available operation modes."""
        return self._operation_list

    @property
    def swing_list(self):
        """Return the list of available swing modes."""
        return self._swing_list

    @property
    def current_fan_mode(self):
        """Return the fan setting."""
        return self._current_fan_mode

    @property
    def current_swing_mode(self):
        """Return current swing mode."""
        return self._current_swing_mode

    @property
    def fan_list(self):
        """Return the list of available fan modes."""
        return self._fan_list
        
    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_FLAGS        
 
    def set_temperature(self, **kwargs):
        """Set new target temperatures."""
        if kwargs.get(ATTR_TEMPERATURE) is not None:
            self._target_temperature = kwargs.get(ATTR_TEMPERATURE)
            
            if not (self._current_operation.lower() == 'off' or self._current_operation.lower() == 'idle'):
                self.send_command()
            elif self._default_operation_from_idle is not None:
                self.set_operation_mode(self._default_operation_from_idle)

            self.schedule_update_ha_state()

    def set_fan_mode(self, fan):
        """Set new target temperature."""
        self._current_fan_mode = fan
        
        if not (self._current_operation.lower() == 'off' or self._current_operation.lower() == 'idle'):
            self.send_command()
            
        self.schedule_update_ha_state()

    def set_operation_mode(self, operation_mode):
        """Set new target temperature."""
        self._current_operation = operation_mode

        self.send_command()
        self.schedule_update_ha_state()

    def set_swing_mode(self, swing_mode):
        """Set new target swing operation."""
        self._current_swing_mode = swing_mode

        self.send_command()
        self.schedule_update_ha_state()
        
    @asyncio.coroutine
    def async_added_to_hass(self):
        state = yield from RestoreEntity(self.hass, self.entity_id)
        
        if state is not None:
            self._target_temperature = state.attributes['temperature']
            self._current_operation = state.attributes['operation_mode']
            self._current_fan_mode = state.attributes['fan_mode']
            self._current_swing_mode = state.attributes['swing_mode']
